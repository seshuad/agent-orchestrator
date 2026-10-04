"""Connectors: the backend systems a workspace can reach, set up once by a workspace admin.

    <home>/connectors.json         [{id, type, name, settings, offered, tools, who, domains, status}]
    <home>/vault/connector-<id>    the connector's secrets: an OAuth client secret, a shared token

Three layers decide what an agent can do with a system:

    connector   (admin)    app credentials, the most any account may be granted (`offered`), who may connect
    account     (builder)  one signed-in account under a connector, with permissions within `offered`
    step        (builder)  actions and limits within the account's permissions, checked by the gateway

Types:
    google        Gmail, Google Sheets, Google Calendar through one OAuth client
    github        GitHub's REST API with a fine-grained token per account
    mcp           any MCP server; each of its tools is marked read (Ask steps), act (Act steps) or off
    bigquery      BigQuery, as a service account, gcloud, or the machine's credentials
    gcs           Cloud Storage, the same ways
    microsoft365  SharePoint through Microsoft Graph, as an Entra ID app (Sites.Selected); email through an SMTP server
    trino         a Trino server (e.g. Dataproc's Trino component), as one user: read queries
    dataproc      Spark SQL as Dataproc jobs, on a cluster or Dataproc Serverless: read queries
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..runtime import upstream, vault
from .connections import SERVICES

TYPES: dict[str, dict[str, Any]] = {
    "google": {"name": "Google Workspace", "icon": "mail", "services": ["gmail", "google-sheets", "google-calendar"],
               "reach": "Gmail · Google Sheets · Google Calendar"},
    "github": {"name": "GitHub", "icon": "code", "services": ["github"], "reach": "Issues · pull requests · files, read only"},
    "mcp": {"name": "MCP server", "icon": "plug", "services": ["mcp"], "reach": "Any system with an MCP server"},
    "bigquery": {"name": "BigQuery", "icon": "database", "services": ["bigquery"], "reach": "BigQuery: read queries, optionally inserts"},
    "gcs": {"name": "Cloud Storage", "icon": "folder", "services": ["gcs"], "reach": "Cloud Storage: read files, optionally write new ones"},
    "trino": {"name": "Trino", "icon": "database", "services": ["trino"], "reach": "Trino: read queries"},
    "dataproc": {"name": "Spark SQL (Dataproc)", "icon": "database", "services": ["spark-sql"], "reach": "Spark SQL on Dataproc: read queries"},
    "microsoft365": {"name": "Microsoft 365", "icon": "folder", "services": ["sharepoint", "smtp"],
                     "reach": "SharePoint files and lists · email through SMTP"},
}

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def secret_key(cid: str) -> str:
    return f"connector-{cid}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "connector"


def default_offered(ctype: str) -> dict[str, list[str]]:
    return {svc: list(SERVICES[svc]["permissions"]) for svc in TYPES[ctype]["services"] if svc in SERVICES}


# ------------------------------------------------------------------ what an account under a connector can be granted

def mcp_permissions(connector: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """An MCP connector's permissions: one for all its read tools, one per act tool."""
    tools = connector.get("tools") or []
    reads = [t["name"] for t in tools if t.get("treat") == "read"]
    perms: dict[str, dict[str, Any]] = {}
    if reads:
        perms["read"] = {"label": "Read", "actions": reads, "scope": ", ".join(reads),
                         "detail": "Ask steps can call these tools, within each step's limits."}
    for t in tools:
        if t.get("treat") == "act":
            perms[t["name"]] = {"label": t["name"].replace("_", " ").capitalize(), "actions": [t["name"]], "scope": t["name"],
                                "detail": (t.get("description") or "") + " Act steps only."}
    return perms


def catalog(service: str, connector: dict[str, Any] | None) -> dict[str, Any]:
    """What an account of `service` under `connector` can be granted: the service's permissions within what the admin offered."""
    if service == "mcp":
        name = (connector or {}).get("name", "MCP server")
        off = [t["name"] for t in (connector or {}).get("tools", []) if t.get("treat") == "off"]
        return {"name": name, "icon": "plug", "permissions": mcp_permissions(connector or {}),
                "never": (", ".join(off) + ": not offered by the admin.") if off else "Only the tools the admin marked read or act."}
    base = SERVICES[service]
    offered = ((connector or {}).get("offered") or {}).get(service)
    perms = {k: v for k, v in base["permissions"].items() if offered is None or k in offered}
    return {**base, "permissions": perms}


def tool_of(connector: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((t for t in connector.get("tools") or [] if t["name"] == name), None)


# ------------------------------------------------------------------ public view (never a secret)

def public(connector: dict[str, Any], vault_dir: Path, accounts: list[dict[str, Any]]) -> dict[str, Any]:
    secret = vault.load(secret_key(connector["id"]), vault_dir) or {}
    out = {**connector, "type_name": TYPES[connector["type"]]["name"], "icon": TYPES[connector["type"]]["icon"],
           "reach": reach(connector), "secret_set": bool(secret), "secret_set_at": secret.get("_set_at"),
           "accounts": len([a for a in accounts if a.get("connector") == connector["id"]])}
    if connector["type"] == "microsoft365":     # which of its two secrets are in the vault (never the secrets)
        out["secrets_set"] = {k: bool(secret.get(k)) for k in ("client_secret", "smtp_password")}
    if connector["type"] == "mcp":
        out["permissions"] = mcp_permissions(connector)
        out["admin_signed_in"] = bool((vault.load(secret_key(connector["id"]) + "-admin", vault_dir) or {}).get("tokens"))
    return out


def reach(connector: dict[str, Any]) -> str:
    if connector["type"] == "bigquery":
        st = connector.get("settings") or {}
        return f"BigQuery · {st.get('billing_project') or 'no project yet'}" + (f" · {', '.join(st.get('allowed') or [])}" if st.get("allowed") else "")
    if connector["type"] == "gcs":
        allowed = (connector.get("settings") or {}).get("allowed") or []
        return "Cloud Storage · " + (", ".join(allowed) if allowed else "no buckets yet")
    if connector["type"] == "trino":
        st = connector.get("settings") or {}
        host = (st.get("server") or "").split("//")[-1] or "no server yet"
        return f"Trino · {host}" + (f" · {', '.join(st.get('allowed') or [])}" if st.get("allowed") else "")
    if connector["type"] == "dataproc":
        st = connector.get("settings") or {}
        where = f"cluster {st.get('cluster')}" if st.get("mode") == "cluster" else "Serverless"
        return f"Spark SQL · {where} · {st.get('region') or 'no region yet'}" + (f" · {', '.join(st.get('allowed') or [])}" if st.get("allowed") else "")
    if connector["type"] == "microsoft365":
        st = connector.get("settings") or {}
        where = [", ".join(st.get("allowed") or []) or ("SharePoint" if st.get("client_id") else "")]
        if (st.get("smtp") or {}).get("host"):
            where.append(f"email via {st['smtp']['host']}")
        return "Microsoft 365 · " + (" · ".join(w for w in where if w) or "not set up yet")
    if connector["type"] == "mcp":
        server = (connector.get("settings") or {}).get("server") or {}
        where = server.get("url", "").split("//")[-1].split("/")[0] if server.get("transport") == "url" else server.get("command", "")
        return f"MCP server · {where}" if where else "MCP server"
    return TYPES[connector["type"]]["reach"]


def sign_in_kind(connector: dict[str, Any] | None) -> str:
    """How an account under this connector signs in: google, token, oauth (MCP), shared (the connector's credential) or none."""
    if connector is None:
        return "none"
    if connector["type"] == "google":
        return "google"
    if connector["type"] == "github":
        return "token"
    if connector["type"] in ("bigquery", "gcs", "microsoft365", "trino", "dataproc"):
        return "shared"
    kind = ((connector.get("settings") or {}).get("auth") or {}).get("kind", "none")
    return {"oauth": "oauth", "bearer": "shared", "header": "shared"}.get(kind, "none")


# ------------------------------------------------------------------ tests

def status(state: str, message: str, by: str) -> dict[str, Any]:
    return {"state": state, "message": message, "tested_at": time.time(), "tested_by": by}


def test_google(connector: dict[str, Any], vault_dir: Path) -> tuple[bool, str]:
    """Asks Google's token endpoint to redeem a made-up code: "invalid_grant" means it accepted the client itself."""
    import urllib.error
    import urllib.parse
    import urllib.request
    client_id = (connector.get("settings") or {}).get("client_id", "").strip()
    secret = (vault.load(secret_key(connector["id"]), vault_dir) or {}).get("client_secret", "")
    if not client_id or not secret:
        return False, "Enter the OAuth client ID and secret first."
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": "agent-orchestrator-test", "client_id": client_id,
                                   "client_secret": secret, "redirect_uri": "http://127.0.0.1/"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(GOOGLE_TOKEN_URL, data=body), timeout=20)
        return True, "Google accepted the client."
    except urllib.error.HTTPError as exc:
        err = json.loads(exc.read() or b"{}").get("error", "")
        if err in ("invalid_grant", "redirect_uri_mismatch"):
            return True, "Google accepted the client ID and secret."
        if err in ("invalid_client", "unauthorized_client"):
            return False, "Google didn't accept the client ID and secret (invalid_client)."
        return False, f"Google answered {exc.code}: {err or 'an error'}."
    except urllib.error.URLError as exc:
        return False, f"Couldn't reach Google: {exc.reason}"


def test_github(connector: dict[str, Any]) -> tuple[bool, str]:
    import urllib.error
    import urllib.request
    api = ((connector.get("settings") or {}).get("api_url") or "https://api.github.com").rstrip("/")
    try:
        urllib.request.urlopen(urllib.request.Request(api + "/meta", headers={"User-Agent": "agent-orchestrator"}), timeout=20)
        return True, f"Reached {api}."
    except urllib.error.HTTPError as exc:
        return False, f"{api} answered {exc.code}."
    except urllib.error.URLError as exc:
        return False, f"Couldn't reach {api}: {exc.reason}"


def test_bigquery(connector: dict[str, Any], vault_dir: Path) -> tuple[bool, str]:
    """Who it signs in as, and a free dry run against each allowed dataset."""
    import os
    os.environ.setdefault("AGENT_SERVICE_VAULT", str(vault_dir))
    from ..runtime import bigquery_api
    st = connector.get("settings") or {}
    if not st.get("billing_project"):
        return False, "Enter the billing project first."
    up = {**st, "connector": connector["id"]}
    try:
        who = bigquery_api.identity(up)
        live = bigquery_api._Live(up)
        live.dry_run("SELECT 1", {})
        seen = []
        for ref in st.get("allowed") or []:
            project, dataset, _ = bigquery_api.scope_of(ref, st["billing_project"])
            tables = [t.table_id for t in live.client.list_tables(f"{project}.{dataset}", max_results=100)]
            seen.append(f"{project}.{dataset} ({len(tables)} tables)")
    except Exception as exc:
        return False, f"BigQuery refused: {str(exc).splitlines()[0][:240]}"
    return True, f"Signed in as {who}; billing {st['billing_project']}." + (f" Can see {', '.join(seen)}." if seen else " No data allowed yet.")


def test_gcs(connector: dict[str, Any], vault_dir: Path) -> tuple[bool, str]:
    """Who it signs in as, and a listing of each allowed bucket or prefix."""
    import os
    os.environ.setdefault("AGENT_SERVICE_VAULT", str(vault_dir))
    from ..runtime import gcs_api
    st = connector.get("settings") or {}
    if not st.get("allowed"):
        return False, "List the buckets (or bucket/prefix) agents may use first."
    try:
        return True, gcs_api.identity_and_check({**st, "connector": connector["id"]})
    except Exception as exc:
        return False, f"Cloud Storage refused: {str(exc).splitlines()[0][:240]}"


def test_microsoft(connector: dict[str, Any], vault_dir: Path) -> tuple[bool, str]:
    """SharePoint (if its app is set): signs in as the app and lists each allowed folder or list. Email (if its server is
    set): connects and signs in, sending nothing. Both must work for the connector to be ready."""
    import os
    os.environ.setdefault("AGENT_SERVICE_VAULT", str(vault_dir))
    from ..runtime import sharepoint_api, smtp_api
    st = connector.get("settings") or {}
    up = {**st, "connector": connector["id"]}
    sharepoint, smtp = bool(st.get("tenant_id") or st.get("client_id")), bool((st.get("smtp") or {}).get("host"))
    if not sharepoint and not smtp:
        return False, "Set up SharePoint (the Entra ID app), email (the SMTP server), or both, first."
    notes, ok = [], True
    if sharepoint:
        try:
            notes.append("SharePoint: " + sharepoint_api.identity_and_check(up))
        except Exception as exc:
            ok = False
            notes.append(f"SharePoint refused: {str(exc).splitlines()[0][:240]}")
    if smtp:
        try:
            notes.append("Email: " + smtp_api.check(up))
        except Exception as exc:
            ok = False
            notes.append(f"Email failed: {type(exc).__name__ if not str(exc) else str(exc).splitlines()[0][:240]}")
    return ok, " ".join(notes)


def test_sql_engine(connector: dict[str, Any], vault_dir: Path) -> tuple[bool, str]:
    """Trino: SHOW CATALOGS as the connector's user. Spark SQL: a SELECT 1 job on the cluster or as a serverless batch
    (which takes a minute or two, and costs a few cents)."""
    import os
    os.environ.setdefault("AGENT_SERVICE_VAULT", str(vault_dir))
    from ..runtime import sql_engines
    service = "trino" if connector["type"] == "trino" else "spark-sql"
    try:
        return True, sql_engines.check_connection(service, {**(connector.get("settings") or {}), "connector": connector["id"]})
    except Exception as exc:
        return False, f"{sql_engines.NAME[service]} failed: {str(exc).splitlines()[0][:300] or type(exc).__name__}"


def mcp_credentials(connector: dict[str, Any], vault_dir: Path, account: str | None = None) -> dict[str, Any]:
    """Keyword arguments for upstream.session: the shared secret, or whose OAuth tokens to use."""
    kind = ((connector.get("settings") or {}).get("auth") or {}).get("kind", "none")
    if kind in ("bearer", "header"):
        return {"secret": (vault.load(secret_key(connector["id"]), vault_dir) or {}).get("token")}
    if kind == "oauth":
        return {"oauth_key": account or secret_key(connector["id"]) + "-admin", "vault_dir": vault_dir}
    return {}


def discover(connector: dict[str, Any], vault_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Lists the server's tools and merges them with the admin's choices. Returns (tools, what changed).

    A tool the admin hasn't seen starts as "off". A tool whose description or schema changed keeps its choice but
    is flagged `changed`: the gateway refuses it (its pin no longer matches) until an admin saves it again."""
    s = connector.get("settings") or {}
    listed = upstream.list_tools(s.get("server") or {}, s.get("auth") or {}, **mcp_credentials(connector, vault_dir))
    known = {t["name"]: t for t in connector.get("tools") or []}
    tools, changes = [], []
    for t in listed:
        p = upstream.pin(t)
        old = known.get(t["name"])
        entry = {**t, "pin": p, "limitable": upstream.limitable(t["input_schema"])}
        if old is None:
            entry.update(treat="off", limits=[], new=True, approved_pin=None)
            if connector.get("tools") is not None and known:
                changes.append(f"{t['name']} is new")
        else:
            entry.update(treat=old.get("treat", "off"), limits=[x for x in old.get("limits", []) if x in entry["limitable"]],
                         approved_pin=old.get("approved_pin"), new=False)
            if old.get("approved_pin") and old["approved_pin"] != p and old.get("treat") != "off":
                entry["changed"] = True
                changes.append(f"{t['name']} changed its description or arguments")
        tools.append(entry)
    for name in set(known) - {t["name"] for t in listed}:
        changes.append(f"{name} is gone")
    return tools, changes


def approve_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Saving the connector approves each tool as listed now: its pin is what the gateway will accept."""
    return [{**{k: v for k, v in t.items() if k not in ("new", "changed")}, "approved_pin": t.get("pin")} for t in tools]


# ------------------------------------------------------------------ seeding and moving older workspaces over

def seed(vault_dir: Path, by: str, client_file: Path | None) -> list[dict[str, Any]]:
    """Google Workspace and GitHub. The Google OAuth client comes from the old client_secret.json, if there is one."""
    google: dict[str, Any] = {"id": "google", "type": "google", "name": "Google Workspace", "settings": {"client_id": ""},
                              "offered": default_offered("google"), "who": "builders", "domains": [],
                              "status": {"state": "setup", "message": "Enter the OAuth client to sign accounts in to Google."},
                              "created_by": by, "created_at": time.time()}
    if client_file and client_file.exists():
        data = json.loads(client_file.read_text())
        kind, conf = next(iter(data.items()))
        google["settings"] = {"client_id": conf.get("client_id", ""), "client_kind": kind}
        vault.save(secret_key("google"), {"client_secret": conf.get("client_secret", ""), "_set_at": time.time()}, vault_dir)
        google["status"] = {"state": "ready", "message": f"Imported from {client_file.name}. Test it to check.", "tested_at": None}
    github = {"id": "github", "type": "github", "name": "GitHub", "settings": {"api_url": "https://api.github.com"},
              "offered": default_offered("github"), "who": "builders", "domains": [],
              "status": {"state": "ready", "message": "Each account adds its own token."}, "created_by": by, "created_at": time.time()}
    return [google, github]


CONNECTOR_OF = {"gmail": "google", "google-sheets": "google", "google-calendar": "google", "github": "github"}
