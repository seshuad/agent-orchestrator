"""The designer's back end: a FastAPI app over the store, the analysis and the run manager.

    uv run agent-service serve            http://127.0.0.1:8700 (the built web app, and /api)

Everything the web app does goes through /api. Saving a draft returns fresh design-time feedback
(errors pinned to fields, warnings, the compiled YAML, the Free-form graphs), so the editor never
has to guess what the compiler thinks.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .. import definition, runner
from . import analysis
from .connections import SERVICES, allowed_actions, steps_using
from .runs import Runs
from . import connectors as conn_types
from . import author, google, mcp_oauth
from .store import EXAMPLES, SAMPLE_SETS, Conflict, NotFound, Store
from ..runtime import upstream, vault

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

TEMPLATES = {  # start from a vetted pattern: a copy of an example, renamed
    "read-check-approve-act": ("Read, check, approve, act", EXAMPLES / "travel-sync-free/travel-sync-free.agent.yaml",
                               "Free-form reading and checking, then a person approves before anything changes."),
    "match-and-approve": ("Match, then approve", EXAMPLES / "invoice-check/invoice-check.agent.yaml",
                          "Look things up, compare them with rules, and ask a person before acting."),
}


def blank(name: str, description: str) -> dict[str, Any]:
    return {"format": definition.FORMAT, "name": name, "description": description or "",
            "trigger": {"kind": "manual"},
            "run_options": {"dry_run": {"type": "yes/no", "default": True, "description": "List what it would do, and change nothing."}},
            "limits": {"budget_usd": 1.0, "timeout_minutes": 15}, "connections": {}, "records": {}, "steps": []}


def trigger_text(trigger: dict[str, Any]) -> str:
    kind = trigger.get("kind")
    if kind == "schedule":
        every = {"weekday": "Weekdays", "day": "Every day", "week": "Weekly"}.get(trigger.get("every", ""), trigger.get("every", ""))
        return f"{every} at {trigger.get('at', '')}"
    if kind == "email":
        return f"Email to {trigger.get('to', '')}"
    if kind == "webhook":
        return "When a webhook is called"
    return "Only when run manually"


def next_run(trigger: dict[str, Any]) -> str | None:
    if trigger.get("kind") != "schedule" or not trigger.get("at"):
        return "On the next email" if trigger.get("kind") == "email" else None
    hh, mm = (int(x) for x in trigger["at"].split(":"))
    t = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    while t <= datetime.now() or (trigger.get("every") == "weekday" and t.weekday() >= 5):
        t += timedelta(days=1)
    return t.strftime("%a %b %-d, %H:%M")


class NewAgent(BaseModel):
    name: str
    description: str = ""
    start: str = "blank"                       # blank | a template key
    sample_set: str | None = None


class Draft(BaseModel):
    draft: dict[str, Any]


class DescribeAgent(BaseModel):
    description: str
    name: str = ""                             # optional; Claude picks one otherwise
    sample_set: str | None = None              # optional; Claude picks one otherwise


class Refine(BaseModel):
    instruction: str


class Suggest(BaseModel):
    draft: dict[str, Any]                      # the editor's draft as it is now (autosave may lag a moment)
    path: list[Any]                            # the step, e.g. ["steps", 0] or ["steps", 0, "steps", 2]
    field: str                                 # instructions | task


class RunRequest(BaseModel):
    version: int | None = None                 # None: the draft (a test run)
    inputs: dict[str, str] = {}
    email_id: str | None = None
    scripted: bool = False
    source: str = "sample"                     # sample | live: Gmail steps read the real signed-in accounts


class Approval(BaseModel):
    choice: str
    ids: str | None = None


class TestData(BaseModel):
    sample_set: str | None = None


class Publish(BaseModel):
    note: str = ""


class GitHubToken(BaseModel):
    token: str


class ConnectionIn(BaseModel):
    connector: str | None = None               # the connector it's under (new accounts); None keeps the existing one
    service: str
    account: str = ""                          # filled in by the backend when the account signs in
    label: str = ""
    permissions: list[str] = []
    force: bool = False                        # edit: remove permissions agents use


class ConnectorIn(BaseModel):
    type: str | None = None                    # google | github | mcp (new connectors)
    name: str = ""
    settings: dict[str, Any] = {}              # non-secret settings: client ID, API URL, MCP server and auth
    secret: str | None = None                  # a new secret for the vault (never sent back); None keeps the old one
    offered: dict[str, list[str]] | None = None
    who: str = "builders"                      # builders | admins: who may connect accounts
    domains: list[str] = []                    # Google: only accounts in these domains
    tools: list[dict[str, Any]] | None = None  # MCP: [{name, treat: read|act|off, limits}]; saving approves them as listed


def create_app(home: Path | None = None) -> FastAPI:
    store = Store(home or Path(os.environ.get("AGENT_SERVICE_HOME", ".workspace")))
    runs = Runs(store)
    vault_dir = store.home / "vault"
    os.environ["AGENT_SERVICE_VAULT"] = str(vault_dir)      # the service reads tokens here (sign-in, email pickers)
    sign_ins = google.Pending()

    mcp_sign_ins = mcp_oauth.Pending()

    def connectors_map() -> dict[str, dict[str, Any]]:
        return {c["id"]: c for c in store.connectors()}

    def signed_in(cid: str | None) -> bool:
        """Whether an account can reach its system for real: its own token, or its connector's shared credential."""
        conn = store.accounts().get(cid or "")
        if conn is None:
            return False
        kind = conn_types.sign_in_kind(store.connector(conn.get("connector")))
        if kind == "shared":
            return bool(vault.load(conn_types.secret_key(conn["connector"]), vault_dir))
        if kind == "none" and conn["service"] == "mcp":
            return True
        return vault.load(conn["id"], vault_dir) is not None

    def gmail_accounts(raw: dict[str, Any]) -> list[str]:
        """The workspace accounts an agent's Gmail connections use."""
        return [c.get("account") for c in (raw.get("connections") or {}).values() if c.get("service") == "gmail"]

    def live_accounts(raw: dict[str, Any], live: bool) -> list[tuple[str, str | None]]:
        """(service, workspace account) for each connection a run will use for real: MCP always, the rest on real-account runs."""
        return [(c.get("service"), c.get("account")) for c in (raw.get("connections") or {}).values()
                if c.get("service") in runner.ALWAYS_LIVE or (live and c.get("service") in runner.LIVE_SERVICES)]

    def is_admin() -> bool:
        return store.workspace()["user"].get("role") == "Admin"

    def require_admin() -> None:
        if not is_admin():
            raise HTTPException(status_code=403, detail="Only a workspace admin can change connectors.")

    app = FastAPI(title="Agent Orchestrator")

    def fail(exc: Exception, status: int = 400) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    def feedback(raw: dict[str, Any]) -> dict[str, Any]:
        out = analysis.check(raw, store.accounts(), connectors_map())
        graphs = {}
        try:
            agent = definition.Agent.model_validate(raw)
            graphs = {s.id: analysis.graph(s, agent) for s in agent.steps if isinstance(s, definition.FreeFormBlock)}
        except ValidationError:
            pass
        return {**out, "graphs": graphs}

    def agent_summary(name: str) -> dict[str, Any]:
        meta, raw = store.meta(name), store.draft(name)
        recent = runs.list(name)
        last = recent[0] if recent else None
        pub = store.version(name, meta["published"]) if meta["published"] else raw
        return {"name": name, "description": raw.get("description", ""), "owner": meta["owner"],
                "status": "published" if meta["published"] else "draft", "version": meta["published"],
                "has_changes": store.has_unpublished_changes(name), "trigger": pub.get("trigger", {}),
                "trigger_text": trigger_text(pub.get("trigger", {})), "next_run": next_run(pub.get("trigger", {})) if meta["published"] else None,
                "last_run": {k: last[k] for k in ("id", "status", "started_at", "started_by", "trigger")} if last else None,
                "recent": [r["status"] for r in recent[:10]][::-1]}

    # -------------------------------------------------------------- session and workspace

    @app.get("/api/session")
    def session() -> dict[str, Any]:
        ws = store.workspace()
        month = [r for r in runs.list() if r["started_at"] > time.time() - 31 * 86400]
        spend = sum(runs.detail(r["id"]).get("cost_usd", 0) for r in month[:50])
        week = [r for r in runs.list() if r["started_at"] > time.time() - 7 * 86400]
        return {"user": ws["user"], "workspace": {k: ws[k] for k in ("name", "kind", "members", "spend_limit_usd")},
                "claude_api": bool(os.environ.get("ANTHROPIC_API_KEY")), "spend_usd": round(spend, 2),
                "week": {s: sum(r["status"] == s for r in week) for s in ("succeeded", "failed", "stopped", "waiting", "running")}}

    # -------------------------------------------------------------- connections

    def used_by(cid: str) -> list[dict[str, Any]]:
        """Agents whose draft or published version uses a workspace connection, and which of their steps."""
        out = []
        for name in store.names():
            meta = store.meta(name)
            for label, raw in [("draft", store.draft(name))] + ([("published", store.version(name, meta["published"]))] if meta["published"] else []):
                for local, conn in (raw.get("connections") or {}).items():
                    if conn.get("account") == cid:
                        steps = [s.get("name", s.get("id")) for _, s in steps_using(raw, local)]
                        if not any(u["agent"] == name for u in out):
                            out.append({"agent": name, "in": label, "steps": steps, "actions": sorted({a for _, s in steps_using(raw, local) for a in s["uses"]["actions"]})})
        return out

    def connection_out(c: dict[str, Any]) -> dict[str, Any]:
        connector = store.connector(c.get("connector"))
        kind = conn_types.sign_in_kind(connector)
        cat = conn_types.catalog(c["service"], connector)
        return {**c, "connector_name": (connector or {}).get("name"), "sign_in": kind,
                "can_sign_in": kind in ("google", "token", "oauth") and (kind != "google" or c["service"] in google.SCOPES),
                "signed_in": signed_in(c["id"]), "service_name": cat["name"], "catalog": cat,
                "allowed": sorted(allowed_actions(c, connectors_map())), "used_by": used_by(c["id"])}

    @app.get("/api/services")
    def services() -> dict[str, Any]:
        return SERVICES

    @app.get("/api/connections")
    def list_connections() -> list[dict[str, Any]]:
        return [connection_out(c) for c in store.connections()]

    @app.get("/api/connections/{cid}")
    def get_connection(cid: str) -> dict[str, Any]:
        c = store.accounts().get(cid)
        if c is None:
            raise fail(NotFound(f"No connection {cid!r}."), 404)
        return connection_out(c)

    def _fields(body: ConnectionIn, cid: str, connector: dict[str, Any] | None) -> dict[str, Any]:
        perms = [p for p in body.permissions if p in conn_types.catalog(body.service, connector)["permissions"]]
        account = body.account.strip()
        return {"id": cid, "connector": (connector or {}).get("id"), "service": body.service, "account": account,
                "label": body.label.strip() or account or f"{(connector or {}).get('name', body.service)} account", "permissions": perms}

    @app.post("/api/connections")
    def add_connection(body: ConnectionIn) -> dict[str, Any]:
        connector = store.connector(body.connector or conn_types.CONNECTOR_OF.get(body.service))
        if connector is None:
            raise fail(ValueError("Pick a connector an admin has set up."), 422)
        if body.service not in conn_types.TYPES[connector["type"]]["services"]:
            raise fail(ValueError(f"{connector['name']} doesn't offer {body.service}."), 422)
        if connector.get("who") == "admins" and not is_admin():
            raise HTTPException(status_code=403, detail=f"Only admins can connect accounts through {connector['name']}.")
        if (connector.get("status") or {}).get("state") == "attention":
            raise fail(ValueError(f"{connector['name']} needs an admin's attention first: {connector['status'].get('message', '')}"), 422)
        base = re.sub(r"[^a-z0-9]+", "-", (body.label or body.account or connector["name"]).lower().replace("'", "")).strip("-") or body.service
        cid, n = base, 2
        while cid in store.accounts() or store.connector(cid) is not None:
            cid, n = f"{base}-{n}", n + 1
        return connection_out(store.save_connection(_fields(body, cid, connector)))

    @app.put("/api/connections/{cid}")
    def edit_connection(cid: str, body: ConnectionIn) -> dict[str, Any]:
        existing = store.accounts().get(cid)
        if existing is None:
            raise fail(NotFound(f"No connection {cid!r}."), 404)
        if body.service != existing["service"]:
            raise fail(ValueError("A connection's service can't change: connect a new account instead."), 422)
        updated = {**_fields(body, cid, store.connector(existing.get("connector"))), "account": existing.get("account", "")}
        needed = {(u["agent"], a) for u in used_by(cid) for a in u["actions"]}
        lost = sorted({f"{agent} ({a.replace('_', ' ')})" for agent, a in needed if a not in allowed_actions(updated, connectors_map())})
        if lost and not body.force:
            raise fail(ValueError("Agents use permissions you're removing: " + ", ".join(lost)
                                  + ". Save anyway to remove them; those agents will show errors until they change."), 409)
        return connection_out(store.save_connection(updated))

    @app.delete("/api/connections/{cid}")
    def remove_connection(cid: str) -> dict[str, str]:
        users = used_by(cid)
        if users:
            raise fail(ValueError("Still used by " + ", ".join(u["agent"] for u in users)
                                  + ". Pick another account in those agents' Connections first."), 409)
        try:
            store.delete_connection(cid)
        except NotFound as exc:
            raise fail(exc, 404)
        vault.delete(cid, vault_dir)              # its token goes with it
        return {"deleted": cid}

    # -------------------------------------------------------------- connectors (a workspace admin sets them up)

    def connector_out(c: dict[str, Any]) -> dict[str, Any]:
        out = conn_types.public(c, vault_dir, store.connections())
        out["services"] = {svc: conn_types.catalog(svc, c) for svc in conn_types.TYPES[c["type"]]["services"]}
        out["sign_in"] = conn_types.sign_in_kind(c)
        return out

    @app.get("/api/connector-types")
    def connector_types() -> dict[str, Any]:
        return {k: {**v, "services": {s: SERVICES[s] for s in v["services"] if s in SERVICES}} for k, v in conn_types.TYPES.items()}

    @app.get("/api/connectors")
    def list_connectors() -> list[dict[str, Any]]:
        return [connector_out(c) for c in store.connectors()]

    @app.get("/api/connectors/{cid}")
    def get_connector(cid: str) -> dict[str, Any]:
        c = store.connector(cid)
        if c is None:
            raise fail(NotFound(f"No connector {cid!r}."), 404)
        return connector_out(c)

    def _save_secret(cid: str, ctype: str, secret: str | None) -> None:
        if secret is None:
            return
        if not secret.strip():
            vault.delete(conn_types.secret_key(cid), vault_dir)
            return
        field = "client_secret" if ctype == "google" else "token"
        vault.save(conn_types.secret_key(cid), {field: secret.strip(), "_set_at": time.time()}, vault_dir)

    def _connector_fields(body: ConnectorIn, existing: dict[str, Any] | None, ctype: str) -> dict[str, Any]:
        item: dict[str, Any] = {"name": body.name.strip() or (existing or {}).get("name") or conn_types.TYPES[ctype]["name"],
                                "settings": {**((existing or {}).get("settings") or {}), **body.settings}, "who": body.who,
                                "domains": [d.strip().lower().lstrip("@") for d in body.domains if d.strip()]}
        if body.offered is not None and ctype != "mcp":
            valid = conn_types.default_offered(ctype)
            item["offered"] = {svc: [p for p in perms if p in valid.get(svc, [])] for svc, perms in body.offered.items() if svc in valid}
        if body.tools is not None and ctype == "mcp":
            known = {t["name"]: t for t in (existing or {}).get("tools") or []}
            chosen = {t["name"]: t for t in body.tools}
            merged = [{**t, "treat": chosen.get(n, {}).get("treat", t.get("treat", "off")),
                       "limits": [x for x in chosen.get(n, {}).get("limits", t.get("limits", [])) if x in t.get("limitable", [])]}
                      for n, t in known.items()]
            item["tools"] = conn_types.approve_tools(merged)
            state = ((existing or {}).get("status") or {})
            if state.get("state") != "attention" or state.get("reason") == "tools":   # a failed connection stays paused
                reads = sum(t["treat"] == "read" for t in merged)
                acts = sum(t["treat"] == "act" for t in merged)
                item["status"] = conn_types.status("ready", f"Approved {len(merged)} tools: {reads} read, {acts} act, "
                                                   f"{len(merged) - reads - acts} not offered.", store.workspace()["user"]["name"])
        return item

    @app.post("/api/connectors")
    def add_connector(body: ConnectorIn) -> dict[str, Any]:
        require_admin()
        if body.type not in conn_types.TYPES:
            raise fail(ValueError("Pick a connector type: Google Workspace, GitHub or an MCP server."), 422)
        base = conn_types.slug(body.name or conn_types.TYPES[body.type]["name"])
        cid, n = base, 2
        while store.connector(cid) is not None or cid in store.accounts():
            cid, n = f"{base}-{n}", n + 1
        item = {"id": cid, "type": body.type, "offered": conn_types.default_offered(body.type), "tools": [] if body.type == "mcp" else None,
                "status": {"state": "setup", "message": "Not tested yet."}, **_connector_fields(body, None, body.type)}
        item = {k: v for k, v in item.items() if v is not None}
        _save_secret(cid, body.type, body.secret)
        return connector_out(store.save_connector(item))

    @app.put("/api/connectors/{cid}")
    def edit_connector(cid: str, body: ConnectorIn) -> dict[str, Any]:
        require_admin()
        existing = store.connector(cid)
        if existing is None:
            raise fail(NotFound(f"No connector {cid!r}."), 404)
        _save_secret(cid, existing["type"], body.secret)
        saved = store.save_connector({"id": cid, **_connector_fields(body, existing, existing["type"])})
        for c in store.connections():                    # accounts can't hold more than the connector now offers
            if c.get("connector") == cid:
                keep = [p for p in c.get("permissions", []) if p in conn_types.catalog(c["service"], saved)["permissions"]]
                if keep != c.get("permissions"):
                    store.save_connection({**c, "permissions": keep})
        return connector_out(saved)

    @app.delete("/api/connectors/{cid}")
    def remove_connector(cid: str) -> dict[str, str]:
        require_admin()
        try:
            store.delete_connector(cid)
        except NotFound as exc:
            raise fail(exc, 404)
        except Conflict as exc:
            raise fail(exc, 409)
        for key in (conn_types.secret_key(cid), conn_types.secret_key(cid) + "-admin"):
            vault.delete(key, vault_dir)
        return {"deleted": cid}

    @app.post("/api/connectors/{cid}/test")
    def test_connector(cid: str) -> dict[str, Any]:
        """Checks the connector works; an MCP server also lists its tools (new ones start as not offered)."""
        require_admin()
        c = store.connector(cid)
        if c is None:
            raise fail(NotFound(f"No connector {cid!r}."), 404)
        by = store.workspace()["user"]["name"]
        update: dict[str, Any] = {"id": cid}
        if c["type"] == "google":
            ok, msg = conn_types.test_google(c, vault_dir)
        elif c["type"] == "github":
            ok, msg = conn_types.test_github(c)
        else:
            auth = ((c.get("settings") or {}).get("auth") or {}).get("kind", "none")
            if auth == "oauth" and not vault.load(conn_types.secret_key(cid) + "-admin", vault_dir):
                raise fail(ValueError("Sign in to the server first, so it lists its tools for you."), 422)
            try:
                tools, changes = conn_types.discover(c, vault_dir)
                update["tools"] = tools
                ok = not any(t.get("changed") for t in tools if t.get("treat") != "off")
                offered = sum(t["treat"] != "off" for t in tools)
                msg = (f"Listed {len(tools)} tools ({offered} offered)." + (" " + "; ".join(changes) + "." if changes else "")
                       + ("" if ok else " Review the changed tools, then save to approve them."))
            except upstream.UpstreamError as exc:
                ok, msg = False, f"Couldn't list the tools: {exc}"
        update["status"] = conn_types.status("ready" if ok else "attention", msg, by)
        if c["type"] == "mcp" and not ok and "tools" in update:
            update["status"]["reason"] = "tools"
        return connector_out(store.save_connector(update))

    @app.post("/api/connectors/{cid}/oauth/start")
    def connector_oauth_start(cid: str, request: Request) -> dict[str, str]:
        """The admin signs in to an OAuth MCP server once, so the service can list its tools."""
        require_admin()
        c = store.connector(cid)
        if c is None or c["type"] != "mcp":
            raise fail(NotFound(f"No MCP connector {cid!r}."), 404)
        try:
            url = mcp_sign_ins.start((c.get("settings") or {}).get("server") or {}, conn_types.secret_key(cid) + "-admin",
                                     str(request.base_url), vault_dir, f"connector:{cid}")
        except Exception as exc:
            raise fail(ValueError(f"Couldn't start the sign-in: {exc}"), 422)
        return {"url": url}

    @app.get("/api/templates")
    def templates() -> list[dict[str, str]]:
        return [{"key": k, "name": n, "description": d} for k, (n, _, d) in TEMPLATES.items()]

    @app.get("/api/sample-sets")
    def sample_sets() -> list[str]:
        return list(SAMPLE_SETS)

    # -------------------------------------------------------------- agents

    @app.get("/api/agents")
    def list_agents() -> list[dict[str, Any]]:
        return [agent_summary(n) for n in store.names()]

    @app.post("/api/agents")
    def create_agent(body: NewAgent) -> dict[str, Any]:
        if body.start == "blank":
            raw = blank(body.name, body.description)
        elif body.start in TEMPLATES:
            raw = yaml.safe_load(TEMPLATES[body.start][1].read_text())
            raw.update(name=body.name, description=body.description or raw.get("description", ""))
            for conn in (raw.get("connections") or {}).values():   # start on the workspace's first matching account
                match = next((c for c in store.connections() if c["service"] == conn["service"]), None)
                if match:
                    conn["account"] = match["id"]
        else:
            raise fail(ValueError(f"Unknown way to start: {body.start}"))
        sample = str(SAMPLE_SETS[body.sample_set]) if body.sample_set in SAMPLE_SETS else None
        try:
            store.create(raw, owner=store.workspace()["user"]["name"], sample_data=sample)
        except Conflict as exc:
            raise fail(exc, 409)
        return get_agent(body.name)

    # -------------------------------------------------------------- drafting with Claude

    drafts = author.Drafts()

    def need_claude() -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise fail(ValueError("The service has no Claude API key (ANTHROPIC_API_KEY), so it can't draft agents. "
                                  "Restart it with a key, or start from a template."), 422)

    def workspace_text() -> str:
        return author.describe_workspace(store.connections(), connectors_map(), SAMPLE_SETS)

    def checker(raw: dict[str, Any]) -> list[dict[str, str]]:
        return author.problems(raw, store.accounts(), connectors_map())

    @app.post("/api/agents/describe")
    def describe_agent(body: DescribeAgent) -> dict[str, Any]:
        """Starts drafting a new agent from a description. Poll /api/drafts/<job>."""
        need_claude()
        if not body.description.strip():
            raise fail(ValueError("Describe what the agent should do."), 422)
        wanted = body.name.strip()
        if wanted and (wanted in store.names()):
            raise fail(Conflict(f"There's already an agent called {wanted!r}."), 409)
        sample_hint = (f"Use the sample set {body.sample_set!r} for its test data." if body.sample_set in SAMPLE_SETS
                       else "Pick the sample set that fits best, and name it in a <sample_set> tag before <agent>.")
        request = ("Build an agent for this description, written by the builder:\n<description>\n" + body.description.strip()
                   + "\n</description>\n" + (f"Name it {wanted!r}. " if wanted else "") + sample_hint)
        owner = store.workspace()["user"]["name"]

        def fix_name(raw: dict[str, Any]) -> None:
            raw["name"] = wanted or author.unique_name(str(raw.get("name") or "new-agent"), set(store.names()))

        def work(job: dict[str, Any]) -> dict[str, Any]:
            out = drafts.draft(job, request, workspace_text(), checker, fix_name)
            raw = out.pop("raw")
            chosen = (out.pop("sample_set", "") or "").strip().strip("'\"")
            picked = body.sample_set if body.sample_set in SAMPLE_SETS else next((n for n in SAMPLE_SETS if n.lower() == chosen.lower()), None)
            job["stage"] = "Saving the draft"
            raw["name"] = wanted or author.unique_name(raw["name"], set(store.names()))
            store.create(raw, owner=owner, sample_data=str(SAMPLE_SETS[picked]) if picked else None)
            store.set_ai_note(raw["name"], {"kind": "created", "request": body.description.strip(), "at": time.time(), **out,
                                            "cost_usd": job["cost_usd"]})
            return {"agent": raw["name"], **out}

        return {"job": drafts.start("create", work)}

    @app.post("/api/agents/{name}/refine")
    def refine_agent(name: str, body: Refine) -> dict[str, Any]:
        """Starts changing an agent's draft as instructed. Poll /api/drafts/<job>."""
        need_claude()
        try:
            current = store.draft(name)
        except NotFound as exc:
            raise fail(exc, 404)
        if not body.instruction.strip():
            raise fail(ValueError("Say what to change."), 422)
        request = ("Here is the agent's current definition:\n```yaml\n" + yaml.safe_dump(current, sort_keys=False, allow_unicode=True)
                   + "```\n\nChange it as the builder asks, and keep everything else as it is:\n<change>\n" + body.instruction.strip()
                   + "\n</change>\nIn <summary>, say what you changed.")

        def fix_name(raw: dict[str, Any]) -> None:
            raw["name"] = name

        def work(job: dict[str, Any]) -> dict[str, Any]:
            out = drafts.draft(job, request, workspace_text(), checker, fix_name)
            raw = out.pop("raw")
            out.pop("sample_set", None)
            job["stage"] = "Saving the draft"
            store.save_draft_with_undo(name, raw)
            store.set_ai_note(name, {"kind": "changed", "request": body.instruction.strip(), "at": time.time(), **out,
                                     "cost_usd": job["cost_usd"]})
            return {"agent": name, **out}

        return {"job": drafts.start("refine", work)}

    @app.post("/api/agents/{name}/refine/undo")
    def undo_refine(name: str) -> dict[str, Any]:
        try:
            store.undo_ai(name)
        except NotFound as exc:
            raise fail(exc, 404)
        return get_agent(name)

    @app.post("/api/agents/{name}/suggest")
    def suggest_text(name: str, body: Suggest) -> dict[str, Any]:
        """Claude writes one box of one Ask step. The editor puts it in the box; nothing else changes."""
        need_claude()
        step = author._get(body.draft, body.path)
        if not isinstance(step, dict) or step.get("kind") != "ask":
            raise fail(ValueError("Pick an Ask step first."), 422)
        try:
            return author.suggest(drafts._client(), body.draft, body.path, body.field)
        except author.DraftError as exc:
            raise fail(exc, 422)
        except Exception as exc:
            raise fail(ValueError(author._friendly(exc)), 502)

    @app.post("/api/agents/{name}/ai-note/clear")
    def clear_ai_note(name: str) -> dict[str, str]:
        try:
            store.set_ai_note(name, None)
        except NotFound as exc:
            raise fail(exc, 404)
        return {"cleared": name}

    @app.get("/api/drafts/{job}")
    def draft_job(job: str) -> dict[str, Any]:
        out = drafts.get(job)
        if out is None:
            raise fail(NotFound(f"No drafting job {job!r}."), 404)
        return out

    @app.get("/api/agents/{name}")
    def get_agent(name: str) -> dict[str, Any]:
        try:
            meta, raw = store.meta(name), store.draft(name)
        except NotFound as exc:
            raise fail(exc, 404)
        sets = {str(v): k for k, v in SAMPLE_SETS.items()}
        return {"meta": {**meta, "sample_set": sets.get(meta.get("sample_data") or "", None if not meta.get("sample_data") else "Custom"),
                         "can_undo_ai": store.can_undo_ai(name)},
                "draft": raw, "has_changes": store.has_unpublished_changes(name), "feedback": feedback(raw),
                "summary": agent_summary(name)}

    @app.put("/api/agents/{name}")
    def save_agent(name: str, body: Draft) -> dict[str, Any]:
        try:
            store.save_draft(name, body.draft)
        except NotFound as exc:
            raise fail(exc, 404)
        except Conflict as exc:
            raise fail(exc, 409)
        return {"feedback": feedback(body.draft), "has_changes": store.has_unpublished_changes(name)}

    @app.delete("/api/agents/{name}")
    def delete_agent(name: str) -> dict[str, Any]:
        """Deletes the agent, its versions and its run history; refused while one of its runs is in progress."""
        try:
            store.meta(name)
            removed = runs.delete_for(name)
        except NotFound as exc:
            raise fail(exc, 404)
        except Conflict as exc:
            raise fail(exc, 409)
        store.delete(name)
        return {"deleted": name, "runs_deleted": removed}

    @app.get("/api/agents/{name}/references")
    def refs(name: str, step: str | None = None) -> list[dict[str, str]]:
        return analysis.references(store.draft(name), step)

    @app.post("/api/agents/{name}/publish")
    def publish(name: str, body: Publish) -> dict[str, Any]:
        fb = analysis.check(store.draft(name), store.accounts(), connectors_map())
        if not fb["ok"]:
            raise fail(ValueError("Fix the errors before publishing: " + "; ".join(e["message"] for e in fb["errors"][:3])), 422)
        version = store.publish(name, body.note)
        return {"version": version, **get_agent(name)}

    @app.put("/api/agents/{name}/test-data")
    def test_data(name: str, body: TestData) -> dict[str, Any]:
        meta = store.meta(name)
        store.set_test_data(name, str(SAMPLE_SETS[body.sample_set]) if body.sample_set in SAMPLE_SETS else None, meta.get("replay"))
        return get_agent(name)

    @app.get("/api/agents/{name}/emails")
    def emails(name: str, source: str = "sample") -> list[dict[str, str]]:
        if source == "live":                     # the latest emails in the real inbox, to pick the one that starts the run
            from ..runtime.gmail_api import LiveGmail
            account = next((a for a in gmail_accounts(store.draft(name)) if signed_in(a)), None)
            if account is None:
                raise fail(ValueError("None of this agent's Gmail connections is signed in to Google."), 422)
            return [{k: e[k] for k in ("id", "from", "date", "subject")} for e in LiveGmail(account).search(None, [], 14, limit=25)]
        data = store.meta(name).get("sample_data")
        path = Path(data) / "emails.json" if data else None
        if not path or not path.exists():
            return []
        return [{k: e[k] for k in ("id", "from", "date", "subject")} for e in json.loads(path.read_text())]

    # -------------------------------------------------------------- runs

    @app.post("/api/agents/{name}/runs")
    def start_run(name: str, body: RunRequest) -> dict[str, Any]:
        if not body.scripted and not os.environ.get("ANTHROPIC_API_KEY"):
            raise fail(ValueError("The service has no Claude API key (ANTHROPIC_API_KEY), so model steps can't run. "
                                  "Use scripted answers, or restart the service with a key."), 422)
        live = body.source == "live"
        trigger_email = None
        raw = store.version(name, body.version)
        missing = [a or f"(no {svc} account picked)" for svc, a in live_accounts(raw, live) if not signed_in(a)]
        if missing:
            raise fail(ValueError("Sign these connections in first (Connections): " + ", ".join(missing)), 422)
        if live:
            from ..runtime.gmail_api import credentials
            for account in gmail_accounts(raw):
                try:
                    credentials(account)                 # loads the token, refreshing it if it has expired
                except Exception as exc:
                    raise fail(ValueError(f"The Google sign-in for {account} doesn't work any more ({exc}). "
                                          "Sign in again on Connections."), 422)
            if body.email_id and raw.get("trigger", {}).get("kind") == "email":
                from ..runtime.gmail_api import LiveGmail
                trigger_email = LiveGmail(gmail_accounts(raw)[0]).email(body.email_id)
        try:
            return runs.start(name, version=body.version, inputs=body.inputs, email_id=body.email_id,
                              scripted=body.scripted, started_by=store.workspace()["user"]["name"], live=live, trigger_email=trigger_email)
        except (runner.RunError, ValidationError, NotFound) as exc:
            raise fail(exc, 422)
        except Exception as exc:                 # compile errors on an old version, a busy port ...
            raise fail(ValueError(f"The run couldn't start: {exc}"), 422)

    @app.get("/api/runs")
    def list_runs(agent: str | None = None) -> list[dict[str, Any]]:
        out = []
        for r in runs.list(agent)[:100]:
            d = runs.detail(r["id"])
            out.append({k: d.get(k) for k in ("id", "agent", "version", "status", "started_at", "ended_at", "started_by",
                                              "trigger", "scripted", "source", "cost_usd", "duration", "error", "gate")})
        return out

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        try:
            return runs.detail(run_id)
        except NotFound as exc:
            raise fail(exc, 404)

    @app.get("/api/runs/{run_id}/stream")
    async def run_stream(run_id: str, request: Request) -> StreamingResponse:
        async def gen():
            last = None
            while not await request.is_disconnected():
                d = runs.detail(run_id)
                payload = json.dumps(d, default=str)
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                if d["status"] not in ("running", "waiting"):
                    break
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/runs/{run_id}/approve")
    def approve(run_id: str, body: Approval) -> dict[str, Any]:
        try:
            return runs.approve(run_id, body.choice, body.ids)
        except runner.RunError as exc:
            raise fail(exc, 409)

    @app.post("/api/runs/{run_id}/stop")
    def stop(run_id: str) -> dict[str, Any]:
        try:
            return runs.stop(run_id)
        except runner.RunError as exc:
            raise fail(exc, 409)

    @app.post("/api/runs/{run_id}/conductor")
    def conductor_ui(run_id: str) -> dict[str, str]:
        try:
            return runs.conductor_ui(run_id)
        except NotFound as exc:
            raise fail(exc, 404)
        except runner.RunError as exc:
            raise fail(exc, 409)

    app.router.on_shutdown.append(runs.close)

    # -------------------------------------------------------------- account sign-in: Google, GitHub token, MCP OAuth

    def _connector_of(conn: dict[str, Any]) -> dict[str, Any] | None:
        return store.connector(conn.get("connector"))

    @app.get("/api/google/status")
    def google_status() -> dict[str, Any]:
        return google.status(store.connector("google"), vault_dir)

    @app.post("/api/connections/{cid}/google/start")
    def google_start(cid: str, request: Request) -> dict[str, str]:
        conn = store.accounts().get(cid)
        if conn is None:
            raise fail(NotFound(f"No connection {cid!r}."), 404)
        try:
            return {"url": sign_ins.start(conn, str(request.base_url), google.client_config(_connector_of(conn), vault_dir))}
        except ValueError as exc:
            raise fail(exc, 422)

    @app.post("/api/connections/{cid}/google/sign-out")
    def google_sign_out(cid: str) -> dict[str, Any]:
        vault.delete(cid, vault_dir)
        conn = store.accounts().get(cid)
        if conn:
            store.save_connection({**conn, "signed_in_as": None, "signed_in_at": None})
        return connection_out(store.accounts()[cid])

    @app.post("/api/connections/{cid}/github/token")
    def github_token(cid: str, body: GitHubToken) -> dict[str, Any]:
        """Checks a personal access token with GitHub, then keeps it in the vault. It is never sent back."""
        from ..runtime.github_api import GitHubError, whoami
        conn = store.accounts().get(cid)
        if conn is None:
            raise fail(NotFound(f"No connection {cid!r}."), 404)
        if conn["service"] != "github":
            raise fail(ValueError(f"{conn['label']} isn't a GitHub connection."), 422)
        token = body.token.strip()
        if not token:
            raise fail(ValueError("Paste a token first."), 422)
        api = ((_connector_of(conn) or {}).get("settings") or {}).get("api_url") or "https://api.github.com"
        try:
            login = whoami(token, api)
        except GitHubError as exc:
            raise fail(ValueError(f"GitHub didn't accept that token ({exc})."), 422)
        vault.save(cid, {"token": token, "api_url": api}, vault_dir)
        store.save_connection({**conn, "account": conn.get("account") or login, "signed_in_as": login, "signed_in_at": time.time()})
        return connection_out(store.accounts()[cid])

    @app.post("/api/connections/{cid}/mcp/start")
    def mcp_start(cid: str, request: Request) -> dict[str, str]:
        conn = store.accounts().get(cid)
        connector = _connector_of(conn or {})
        if conn is None or connector is None or connector["type"] != "mcp":
            raise fail(NotFound(f"No MCP connection {cid!r}."), 404)
        try:
            url = mcp_sign_ins.start((connector.get("settings") or {}).get("server") or {}, cid, str(request.base_url), vault_dir, f"account:{cid}")
        except Exception as exc:
            raise fail(ValueError(f"Couldn't start the sign-in: {exc}"), 422)
        return {"url": url}

    @app.post("/api/connections/{cid}/sign-out")
    def sign_out(cid: str) -> dict[str, Any]:
        return google_sign_out(cid)

    def sign_in_return(request: Request) -> RedirectResponse | None:
        """Google and MCP servers send the browser back to the root URL with ?code=…&state=… (or ?error=…)."""
        q = request.query_params
        if "state" not in q or ("code" not in q and "error" not in q):
            return None
        from urllib.parse import quote
        if mcp_sign_ins.owns(q["state"]):
            if "error" in q:
                return RedirectResponse(f"/connections?sign_in_error={quote(q['error'])}")
            try:
                key, purpose, _tools = mcp_sign_ins.finish(q["state"], q["code"])
            except Exception as exc:
                return RedirectResponse(f"/connections?sign_in_error={quote(str(exc))}")
            kind, ident = purpose.split(":", 1)
            if kind == "connector":
                return RedirectResponse(f"/connections/connectors/{ident}?signed_in=1")
            conn = store.accounts()[ident]
            store.save_connection({**conn, "signed_in_as": conn.get("signed_in_as") or "signed in", "signed_in_at": time.time()})
            return RedirectResponse(f"/connections?signed_in={ident}")
        if "error" in q:
            return RedirectResponse(f"/connections?sign_in_error={quote(q['error'])}")
        try:
            cid, email = sign_ins.finish(q["state"], q["code"], vault_dir)
        except Exception as exc:
            return RedirectResponse(f"/connections?sign_in_error={quote(str(exc))}")
        conn = store.accounts()[cid]
        domains = (_connector_of(conn) or {}).get("domains") or []
        if domains and email.rsplit("@", 1)[-1].lower() not in domains:
            vault.delete(cid, vault_dir)
            return RedirectResponse("/connections?sign_in_error=" + quote(f"{email} isn't in {', '.join(domains)}: the admin only allows accounts there."))
        store.save_connection({**conn, "account": conn.get("account") or email, "signed_in_as": email, "signed_in_at": time.time()})
        return RedirectResponse(f"/connections?signed_in={cid}")

    @app.get("/api/approvals")
    def approvals() -> list[dict[str, Any]]:
        return [{"run": r["id"], "agent": r["agent"], "started_at": r["started_at"], "gate": r["gate"]}
                for r in runs.list() if r["status"] == "waiting" and r.get("gate")]

    # -------------------------------------------------------------- the web app

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str, request: Request):
            if path == "":
                back = sign_in_return(request)
                if back is not None:
                    return back
            return FileResponse(WEB_DIST / "index.html")

    return app
