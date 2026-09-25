"""Signing a workspace connection in to Google (OAuth), for runs on real accounts.

The OAuth client belongs to the Google Workspace connector: its admin enters the client ID and secret
under Connections → Connectors (a client file from before connectors is imported once). Google sends
the browser back to the designer's own root URL with ?code=…&state=…. Only Gmail read-only is
requested for now.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from ..runtime import vault
from ..runtime.gmail_api import SCOPES as GMAIL_SCOPES

SCOPES = {"gmail": GMAIL_SCOPES}          # services that can be signed in for real so far

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")     # Google may return the scopes in a different order


def legacy_client_file() -> Path | None:
    """Where the OAuth client used to live, before the Google Workspace connector held it: imported once.
    AGENT_SERVICE_GOOGLE_CLIENT=none turns the import off (tests)."""
    if os.environ.get("AGENT_SERVICE_GOOGLE_CLIENT") == "none":
        return None
    for p in [os.environ.get("AGENT_SERVICE_GOOGLE_CLIENT"), Path.home() / ".config/agent-service/client_secret.json",
              Path.home() / ".config/travel-sync/client_secret.json"]:
        if p and Path(p).exists():
            return Path(p)
    return None


def client_config(connector: dict[str, Any] | None, vault_dir: Path) -> dict[str, Any] | None:
    """The Google Workspace connector's OAuth client, as google-auth-oauthlib expects it."""
    if not connector:
        return None
    settings = connector.get("settings") or {}
    secret = (vault.load(f"connector-{connector['id']}", vault_dir) or {}).get("client_secret")
    if not settings.get("client_id") or not secret:
        return None
    kind = settings.get("client_kind") or "web"
    return {kind: {"client_id": settings["client_id"], "client_secret": secret,
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}


def status(connector: dict[str, Any] | None = None, vault_dir: Path | None = None) -> dict[str, Any]:
    configured = vault_dir is not None and client_config(connector, vault_dir) is not None
    return {"configured": configured, "client_file": None, "client_type": ((connector or {}).get("settings") or {}).get("client_kind"),
            "live_services": list(SCOPES)}


class Pending:
    """Sign-ins in progress: state -> (connection, flow). Each state is single use and expires."""

    def __init__(self) -> None:
        self.items: dict[str, tuple[str, Any, float]] = {}

    def start(self, connection: dict[str, Any], origin: str, config: dict[str, Any] | None) -> str:
        from google_auth_oauthlib.flow import Flow
        if connection["service"] not in SCOPES:
            raise ValueError(f"{connection['service']} can't be signed in for real yet; only Gmail can.")
        if config is None:
            raise ValueError("The Google Workspace connector has no OAuth client yet. An admin sets it up under Connections → Connectors.")
        flow = Flow.from_client_config(config, scopes=SCOPES[connection["service"]], redirect_uri=origin.rstrip("/") + "/")
        state = secrets.token_urlsafe(24)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state, login_hint=connection.get("account"),
                                        include_granted_scopes="false")
        self.items = {k: v for k, v in self.items.items() if v[2] > time.time()}
        self.items[state] = (connection["id"], flow, time.time() + 600)
        return url

    def finish(self, state: str, code: str, vault_dir: Path) -> tuple[str, str]:
        """Swap the code for a token, keep it in the vault, and return (connection id, the address that signed in)."""
        from .. runtime.gmail_api import LiveGmail
        item = self.items.pop(state, None)
        if item is None or item[2] < time.time():
            raise ValueError("That sign-in link has expired or was already used. Start again from Connections.")
        connection_id, flow, _ = item
        flow.fetch_token(code=code)
        vault.save(connection_id, json.loads(flow.credentials.to_json()), vault_dir)
        os.environ["AGENT_SERVICE_VAULT"] = str(vault_dir)        # LiveGmail reads the token from the vault
        return connection_id, LiveGmail(connection_id).profile()
