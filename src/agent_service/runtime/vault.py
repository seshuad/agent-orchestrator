"""The prototype's credential vault: one token file per workspace connection.

    <workspace>/vault/<connection id>.json    an OAuth token (refresh token included), mode 0600

In the service the vault is a separate store only the connector gateway can read, and a run worker
never sees a token. Here the gateway runs inside the worker, so the worker is given the vault's
location (AGENT_SERVICE_VAULT) only for runs on real accounts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

VAULT_ENV = "AGENT_SERVICE_VAULT"


def _dir(vault: Path | None = None) -> Path:
    d = vault or (Path(os.environ[VAULT_ENV]) if os.environ.get(VAULT_ENV) else None)
    if d is None:
        raise RuntimeError("No vault: this run isn't on real accounts.")
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def save(connection: str, token: dict[str, Any], vault: Path | None = None) -> None:
    path = _dir(vault) / f"{connection}.json"
    path.write_text(json.dumps(token))
    path.chmod(0o600)


def load(connection: str, vault: Path | None = None) -> dict[str, Any] | None:
    path = _dir(vault) / f"{connection}.json"
    return json.loads(path.read_text()) if path.exists() else None


def delete(connection: str, vault: Path | None = None) -> None:
    (_dir(vault) / f"{connection}.json").unlink(missing_ok=True)
