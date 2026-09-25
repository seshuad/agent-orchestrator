"""Workspace connections: accounts connected once, with the permissions granted when connecting.

An agent's `connections` entry points at one of these by `account`. Each step then picks actions
on its own panel, and the service checks those actions are within what the account was granted.

    SERVICES   what each service can be granted, and the step actions each permission allows

In the service, connecting opens the provider's sign-in and the token goes to the vault, where only
the connector gateway can read it. In this prototype connections serve sample data, so connecting
records the account and permissions without a sign-in.
"""

from __future__ import annotations

from typing import Any

SERVICES: dict[str, dict[str, Any]] = {
    "gmail": {
        "name": "Gmail", "icon": "mail",
        "permissions": {
            "read": {"label": "Read email", "actions": ["search", "open"], "scope": "gmail.readonly",
                     "detail": "Search and open email. Steps set their own sender and date limits."},
        },
        "never": "Send, delete or label email: not offered.",
    },
    "google-sheets": {
        "name": "Google Sheets", "icon": "group",
        "permissions": {
            "read": {"label": "Read sheets", "actions": ["read"], "scope": "spreadsheets.readonly",
                     "detail": "Read rows. Steps name the sheets they may read."},
            "add rows": {"label": "Add rows", "actions": ["append_row"], "scope": "spreadsheets",
                         "detail": "Add rows at the end of a sheet. Only Act steps. Append-only: existing rows can't change."},
        },
        "never": "Change or delete existing rows: not offered.",
    },
    "google-calendar": {
        "name": "Google Calendar", "icon": "calendar",
        "permissions": {
            "create events": {"label": "Create and see events", "actions": ["create_event"], "scope": "calendar.events",
                              "detail": "Create events, and see existing ones so nothing is added twice."},
        },
        "never": "Change or delete existing events: not offered.",
    },
    "github": {
        "name": "GitHub", "icon": "code", "sign_in": "token",
        "permissions": {
            "read": {"label": "Read issues, pull requests and files", "actions": ["search", "open", "read"],
                     "scope": "read-only token: Issues, Pull requests, Contents",
                     "detail": "Search issues and pull requests, open one with its comments, and read files. Steps name the repositories they may read."},
        },
        "never": "Open, comment on, label, close or merge anything, or push code: not offered.",
    },
}

SEED: list[dict[str, Any]] = [
    {"id": "seshu-gmail", "service": "gmail", "label": "Seshu's Gmail", "account": "seshu.adunuthula@gmail.com", "permissions": ["read"]},
    {"id": "seshu-calendar", "service": "google-calendar", "label": "Seshu's calendar", "account": "seshu.adunuthula@gmail.com", "permissions": ["create events"]},
    {"id": "invoices-inbox", "service": "gmail", "label": "Invoices inbox", "account": "invoices@northpeak.co", "permissions": ["read"]},
    {"id": "finance-sheets", "service": "google-sheets", "label": "Finance sheets", "account": "finance@northpeak.co", "permissions": ["read", "add rows"]},
]
SEED_LINKS = {  # the example agents' connections -> the seeded accounts
    ("travel-sync", "gmail"): "seshu-gmail", ("travel-sync", "calendar"): "seshu-calendar",
    ("invoice-check", "gmail"): "invoices-inbox", ("invoice-check", "sheets"): "finance-sheets",
}


def _perms(conn: dict[str, Any], connectors: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    from .connectors import catalog
    connector = (connectors or {}).get(conn.get("connector") or "")
    if conn["service"] == "mcp" or connector is not None:
        return catalog(conn["service"], connector)["permissions"]
    return SERVICES.get(conn["service"], {}).get("permissions", {})


def allowed_actions(conn: dict[str, Any], connectors: dict[str, dict[str, Any]] | None = None) -> set[str]:
    """What steps may do with an account: its permissions, within what its connector's admin offers."""
    perms = _perms(conn, connectors)
    return {a for p in conn.get("permissions", []) for a in perms.get(p, {}).get("actions", [])}


def permission_text(conn: dict[str, Any], connectors: dict[str, dict[str, Any]] | None = None) -> str:
    perms = _perms(conn, connectors)
    return ", ".join(perms[p]["label"] for p in conn.get("permissions", []) if p in perms).lower() or "nothing"


def steps_using(raw: dict[str, Any], cid: str) -> list[tuple[str, dict[str, Any]]]:
    """(error path, step) for every step of an agent that uses its connection `cid`."""
    out = []

    def walk(steps: list[dict[str, Any]], path: str) -> None:
        for i, s in enumerate(steps or []):
            if (s.get("uses") or {}).get("connection") == cid:
                out.append((f"{path}.{i}", s))
            walk(s.get("steps") or [], f"{path}.{i}.steps")
    walk(raw.get("steps") or [], "steps")
    return out


def check_accounts(raw: dict[str, Any], accounts: dict[str, dict[str, Any]],
                   connectors: dict[str, dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """An agent's connections point at real accounts of the right service, and its steps stay within their permissions.
    For an MCP account, Ask steps may use only its read tools and Act steps only its act tools."""
    errors = []
    for cid, conn in (raw.get("connections") or {}).items():
        account_id = conn.get("account")
        if not account_id:
            errors.append({"path": f"connections.{cid}.account", "message": "Pick which connected account this uses."})
            continue
        account = accounts.get(account_id)
        if account is None:
            errors.append({"path": f"connections.{cid}.account", "message": f"There's no connection {account_id!r} in this workspace any more."})
            continue
        if account["service"] != conn.get("service"):
            what = SERVICES.get(account["service"], {}).get("name", "an MCP connector")
            errors.append({"path": f"connections.{cid}.account", "message": f"{account['label']} is {what}, not {conn.get('service')}."})
            continue
        allowed = allowed_actions(account, connectors)
        connector = (connectors or {}).get(account.get("connector") or "")
        for path, step in steps_using(raw, cid):
            for action in (step.get("uses") or {}).get("actions", []):
                if account["service"] == "mcp" and connector is not None:
                    from .connectors import tool_of
                    tool = tool_of(connector, action)
                    treat = (tool or {}).get("treat")
                    if tool is None or treat == "off":
                        builtin = action in {"search", "open", "read", "append_row", "create_event"}
                        errors.append({"path": f"{path}.uses.actions", "message": (
                            f"{action!r} is a built-in action, but this connection now uses the {connector['name']} MCP connector, "
                            f"whose actions are its tools. Pick this step's tools again." if builtin and tool is None
                            else f"{connector['name']} doesn't offer {action}" + (": an admin marks it Read or Act under Connectors." if tool else "."))})
                        continue
                    if step.get("kind") == "ask" and treat != "read":
                        errors.append({"path": f"{path}.uses.actions", "message": f"{action} changes things in {connector['name']}: "
                                       "only Act steps can use it."})
                        continue
                    if step.get("kind") == "act" and treat != "act":
                        errors.append({"path": f"{path}.uses.actions", "message": f"{action} only reads: use it in an Ask step."})
                        continue
                    if (tool or {}).get("changed"):
                        errors.append({"path": f"{path}.uses.actions", "message": f"{action} changed on the server since an admin "
                                       "approved it. An admin reviews it under Connectors."})
                        continue
                    for arg in ((step.get("uses") or {}).get("arg_limits") or {}):
                        if arg not in (tool or {}).get("limits", []) and not any(arg in (tool_of(connector, a) or {}).get("limits", [])
                                                                                for a in (step.get("uses") or {}).get("actions", [])):
                            errors.append({"path": f"{path}.uses.arg_limits", "message": f"{connector['name']} doesn't let steps limit {arg!r}."})
                if action not in allowed:
                    errors.append({"path": f"{path}.uses.actions",
                                   "message": f"{account['label']} isn't allowed to {action.replace('_', ' ')}. Add that permission to "
                                              f"the connection, or untick the action."})
    return errors
