"""Signing a workspace connection in to Google (OAuth), for runs on real accounts.

The OAuth client is a Google Cloud "Desktop app" client, read from the first of:
    $AGENT_SERVICE_GOOGLE_CLIENT
    ~/.config/agent-service/client_secret.json
    ~/.config/travel-sync/client_secret.json     (travel-sync's, if you already set it up)

Desktop clients may redirect to the loopback address on any port, so Google sends the browser back
to the designer's own root URL with ?code=…&state=…. Only Gmail read-only is requested for now.
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


def client_secret() -> Path | None:
    for p in [os.environ.get("AGENT_SERVICE_GOOGLE_CLIENT"), Path.home() / ".config/agent-service/client_secret.json",
              Path.home() / ".config/travel-sync/client_secret.json"]:
        if p and Path(p).exists():
            return Path(p)
    return None


def status() -> dict[str, Any]:
    path = client_secret()
    kind = next(iter(json.loads(path.read_text())), None) if path else None
    return {"configured": path is not None and kind == "installed", "client_file": str(path) if path else None,
            "client_type": kind, "live_services": list(SCOPES)}


class Pending:
    """Sign-ins in progress: state -> (connection, flow). Each state is single use and expires."""

    def __init__(self) -> None:
        self.items: dict[str, tuple[str, Any, float]] = {}

    def start(self, connection: dict[str, Any], origin: str) -> str:
        from google_auth_oauthlib.flow import Flow
        if connection["service"] not in SCOPES:
            raise ValueError(f"{connection['service']} can't be signed in for real yet; only Gmail can.")
        path = client_secret()
        if path is None:
            raise ValueError("No Google OAuth client is set up. See the steps on the Connections page.")
        flow = Flow.from_client_secrets_file(str(path), scopes=SCOPES[connection["service"]], redirect_uri=origin.rstrip("/") + "/")
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
