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
from . import google
from .store import EXAMPLES, SAMPLE_SETS, Conflict, NotFound, Store
from ..runtime import vault

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
    service: str
    account: str
    label: str = ""
    permissions: list[str] = []
    force: bool = False                        # edit: remove permissions agents use


def _connection_fields(body: ConnectionIn, cid: str) -> dict[str, Any]:
    perms = [p for p in body.permissions if p in SERVICES[body.service]["permissions"]]
    return {"id": cid, "service": body.service, "account": body.account.strip(), "label": body.label.strip() or body.account.strip(),
            "permissions": perms}


def create_app(home: Path | None = None) -> FastAPI:
    store = Store(home or Path(os.environ.get("AGENT_SERVICE_HOME", ".workspace")))
    runs = Runs(store)
    vault_dir = store.home / "vault"
    os.environ["AGENT_SERVICE_VAULT"] = str(vault_dir)      # the service reads tokens here (sign-in, email pickers)
    sign_ins = google.Pending()

    def signed_in(cid: str | None) -> bool:
        return bool(cid) and vault.load(cid, vault_dir) is not None

    def gmail_accounts(raw: dict[str, Any]) -> list[str]:
        """The workspace accounts an agent's Gmail connections use."""
        return [c.get("account") for c in (raw.get("connections") or {}).values() if c.get("service") == "gmail"]

    def live_accounts(raw: dict[str, Any]) -> list[tuple[str, str | None]]:
        """(service, workspace account) for each of an agent's connections that runs on real accounts can use."""
        return [(c.get("service"), c.get("account")) for c in (raw.get("connections") or {}).values() if c.get("service") in runner.LIVE_SERVICES]
    app = FastAPI(title="Agent Orchestrator")

    def fail(exc: Exception, status: int = 400) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    def feedback(raw: dict[str, Any]) -> dict[str, Any]:
        out = analysis.check(raw, store.accounts())
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
        return {**c, "can_sign_in": c["service"] in google.SCOPES or c["service"] == "github",
                "sign_in": SERVICES[c["service"]].get("sign_in", "google"), "signed_in": signed_in(c["id"]), "service_name": SERVICES[c["service"]]["name"], "allowed": sorted(allowed_actions(c)), "used_by": used_by(c["id"])}

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

    @app.post("/api/connections")
    def add_connection(body: ConnectionIn) -> dict[str, Any]:
        if body.service not in SERVICES:
            raise fail(ValueError(f"Unknown service {body.service!r}."), 422)
        base = re.sub(r"[^a-z0-9]+", "-", (body.label or body.account).lower().replace("'", "")).strip("-") or body.service
        cid, n = base, 2
        while cid in store.accounts():
            cid, n = f"{base}-{n}", n + 1
        return connection_out(store.save_connection(_connection_fields(body, cid)))

    @app.put("/api/connections/{cid}")
    def edit_connection(cid: str, body: ConnectionIn) -> dict[str, Any]:
        existing = store.accounts().get(cid)
        if existing is None:
            raise fail(NotFound(f"No connection {cid!r}."), 404)
        if body.service != existing["service"]:
            raise fail(ValueError("A connection's service can't change: connect a new account instead."), 422)
        updated = _connection_fields(body, cid)
        needed = {(u["agent"], a) for u in used_by(cid) for a in u["actions"]}
        lost = sorted({f"{agent} ({a.replace('_', ' ')})" for agent, a in needed if a not in allowed_actions(updated)})
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

    @app.get("/api/agents/{name}")
    def get_agent(name: str) -> dict[str, Any]:
        try:
            meta, raw = store.meta(name), store.draft(name)
        except NotFound as exc:
            raise fail(exc, 404)
        sets = {str(v): k for k, v in SAMPLE_SETS.items()}
        return {"meta": {**meta, "sample_set": sets.get(meta.get("sample_data") or "", None if not meta.get("sample_data") else "Custom")},
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
        fb = analysis.check(store.draft(name), store.accounts())
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
        if live:
            raw = store.version(name, body.version)
            missing = [a or f"(no {svc} account picked)" for svc, a in live_accounts(raw) if not signed_in(a)]
            if missing:
                raise fail(ValueError("Sign these connections in first (Connections): " + ", ".join(missing)), 422)
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

    # -------------------------------------------------------------- Google sign-in

    @app.get("/api/google/status")
    def google_status() -> dict[str, Any]:
        return google.status()

    @app.post("/api/connections/{cid}/google/start")
    def google_start(cid: str, request: Request) -> dict[str, str]:
        conn = store.accounts().get(cid)
        if conn is None:
            raise fail(NotFound(f"No connection {cid!r}."), 404)
        try:
            return {"url": sign_ins.start(conn, str(request.base_url))}
        except ValueError as exc:
            raise fail(exc, 422)

    @app.post("/api/connections/{cid}/google/sign-out")
    def google_sign_out(cid: str) -> dict[str, Any]:
        vault.delete(cid, vault_dir)
        conn = store.accounts().get(cid)
        if conn:
            store.save_connection({**conn, "signed_in_as": None, "signed_in_at": None})
        return connection_out(store.accounts()[cid])

    # -------------------------------------------------------------- GitHub token

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
        try:
            login = whoami(token)
        except GitHubError as exc:
            raise fail(ValueError(f"GitHub didn't accept that token ({exc})."), 422)
        vault.save(cid, {"token": token}, vault_dir)
        store.save_connection({**conn, "signed_in_as": login, "signed_in_at": time.time()})
        return connection_out(store.accounts()[cid])

    @app.post("/api/connections/{cid}/sign-out")
    def sign_out(cid: str) -> dict[str, Any]:
        return google_sign_out(cid)

    def google_return(request: Request) -> RedirectResponse | None:
        """Google sends the browser back to the root URL with ?code=…&state=… (or ?error=…)."""
        q = request.query_params
        if "state" not in q or ("code" not in q and "error" not in q):
            return None
        from urllib.parse import quote
        if "error" in q:
            return RedirectResponse(f"/connections?google_error={quote(q['error'])}")
        try:
            cid, email = sign_ins.finish(q["state"], q["code"], vault_dir)
        except Exception as exc:
            return RedirectResponse(f"/connections?google_error={quote(str(exc))}")
        conn = store.accounts()[cid]
        store.save_connection({**conn, "signed_in_as": email, "signed_in_at": time.time()})
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
                back = google_return(request)
                if back is not None:
                    return back
            return FileResponse(WEB_DIST / "index.html")

    return app
