"""The workspace store: agents with a draft and published versions, as files on disk.

    <home>/workspace.json                   the workspace, its members, the signed-in user
    <home>/agents/<name>/draft.agent.yaml   what the designer is editing
    <home>/agents/<name>/v<N>.agent.yaml    published versions, never changed after publishing
    <home>/agents/<name>/meta.json          owner, published version, sample data, replay script
    <home>/runs/<run id>/                   one directory per run (see runner.py)

Every run is pinned to a version: a published version, or the draft for a test run. A new
workspace is seeded with the two example agents, published as v1.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .. import definition
from .connections import SEED, SEED_LINKS, permission_text

ROOT = Path(__file__).resolve().parents[3]          # the repository, for the seeded examples
EXAMPLES = ROOT / "examples"

WORKSPACE = {
    "name": "Northpeak Operations",
    "kind": "team",
    "user": {"name": "Seshu Adunuthula", "initials": "SA", "email": "seshu.adunuthula@gmail.com", "role": "Builder"},
    "members": [{"name": "Seshu Adunuthula", "initials": "SA", "role": "Builder"},
                {"name": "Priya Shah", "initials": "PS", "role": "Admin"}],
    "spend_limit_usd": 50.0,
}

SEEDS = [  # (definition, sample data, replay script, owner)
    ("travel-sync-free/travel-sync-free.agent.yaml", "travel-sync-free/sample-data", "travel-sync-free/replay-sample.yaml", "Seshu Adunuthula"),
    ("invoice-check/invoice-check.agent.yaml", "invoice-check/sample-data", "invoice-check/replay-northwind.yaml", "Priya Shah"),
]

SAMPLE_SETS = {  # name -> directory, offered when a new agent picks its test data
    "Travel emails": EXAMPLES / "travel-sync-free/sample-data",
    "Invoices and purchase orders": EXAMPLES / "invoice-check/sample-data",
    "Water alerts": EXAMPLES / "water-alerts/sample-data",
}


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)


@dataclass
class Store:
    home: Path

    def __post_init__(self) -> None:
        self.home = self.home.resolve()
        (self.home / "agents").mkdir(parents=True, exist_ok=True)
        (self.home / "runs").mkdir(exist_ok=True)
        if not (self.home / "workspace.json").exists():
            (self.home / "workspace.json").write_text(json.dumps(WORKSPACE, indent=1))
            self._seed_connections()
            self._seed()
        elif not (self.home / "connections.json").exists():
            self._seed_connections()             # a workspace from before connections were stored
            for name in self.names():
                raw = self.draft(name)
                if self._link(raw):
                    self.save_draft(name, raw)

    # -------------------------------------------------------------- workspace

    def workspace(self) -> dict[str, Any]:
        return json.loads((self.home / "workspace.json").read_text())

    def runs_root(self) -> Path:
        return self.home / "runs"

    def _seed_connections(self) -> None:
        now = time.time()
        self._write_connections([{**c, "connected_at": now, "connected_by": WORKSPACE["user"]["name"]} for c in SEED])

    def _link(self, raw: dict[str, Any]) -> bool:
        """Point an example agent's connections at the seeded accounts. True if anything changed."""
        changed = False
        for cid, conn in (raw.get("connections") or {}).items():
            account = SEED_LINKS.get((raw.get("name"), cid))
            if account and not conn.get("account"):
                conn["account"] = account
                changed = True
        return changed

    def _seed(self) -> None:
        for defn, data, replay, owner in SEEDS:
            raw = yaml.safe_load((EXAMPLES / defn).read_text())
            self._link(raw)
            self.create(raw, owner=owner, sample_data=str(EXAMPLES / data), replay=str(EXAMPLES / replay))
            self.publish(raw["name"], note="Seeded from the examples")

    # -------------------------------------------------------------- connections

    def connections(self) -> list[dict[str, Any]]:
        path = self.home / "connections.json"
        return json.loads(path.read_text()) if path.exists() else []

    def accounts(self) -> dict[str, dict[str, Any]]:
        return {c["id"]: c for c in self.connections()}

    def _write_connections(self, conns: list[dict[str, Any]]) -> None:
        (self.home / "connections.json").write_text(json.dumps(conns, indent=1))

    def save_connection(self, conn: dict[str, Any]) -> dict[str, Any]:
        conns = self.connections()
        existing = next((c for c in conns if c["id"] == conn["id"]), None)
        conn = {**(existing or {"connected_at": time.time(), "connected_by": self.workspace()["user"]["name"]}), **conn}
        self._write_connections([conn if c["id"] == conn["id"] else c for c in conns] if existing else conns + [conn])
        # agents that use it show the new permissions
        for name in self.names():
            raw = self.draft(name)
            touched = False
            for c in (raw.get("connections") or {}).values():
                if c.get("account") == conn["id"]:
                    c["permission"] = permission_text(conn)
                    touched = True
            if touched:
                self.save_draft(name, raw)
        return conn

    def delete_connection(self, cid: str) -> None:
        if cid not in self.accounts():
            raise NotFound(f"No connection {cid!r}.")
        self._write_connections([c for c in self.connections() if c["id"] != cid])

    # -------------------------------------------------------------- agents

    def _dir(self, name: str) -> Path:
        d = self.home / "agents" / name
        if not d.exists():
            raise NotFound(f"No agent called {name!r}.")
        return d

    def names(self) -> list[str]:
        return sorted(p.name for p in (self.home / "agents").iterdir() if (p / "meta.json").exists())

    def meta(self, name: str) -> dict[str, Any]:
        return json.loads((self._dir(name) / "meta.json").read_text())

    def _write_meta(self, name: str, meta: dict[str, Any]) -> None:
        (self._dir(name) / "meta.json").write_text(json.dumps(meta, indent=1))

    def create(self, raw: dict[str, Any], owner: str, sample_data: str | None = None, replay: str | None = None) -> str:
        name = raw["name"]
        if not name or not name.replace("-", "").isalnum() or name.lower() != name:
            raise Conflict("Agent names are lower case letters, digits and dashes, like travel-sync.")
        d = self.home / "agents" / name
        if d.exists():
            raise Conflict(f"There's already an agent called {name!r}.")
        d.mkdir(parents=True)
        (d / "draft.agent.yaml").write_text(_dump(raw))
        now = time.time()
        self._write_meta(name, {"name": name, "owner": owner, "created": now, "updated": now, "published": None,
                                "versions": [], "sample_data": sample_data, "replay": replay})
        return name

    def draft(self, name: str) -> dict[str, Any]:
        return yaml.safe_load((self._dir(name) / "draft.agent.yaml").read_text())

    def save_draft(self, name: str, raw: dict[str, Any]) -> None:
        if raw.get("name") != name:
            raise Conflict("An agent's name can't change once it's created.")
        (self._dir(name) / "draft.agent.yaml").write_text(_dump(raw))
        meta = self.meta(name)
        meta["updated"] = time.time()
        self._write_meta(name, meta)

    def set_test_data(self, name: str, sample_data: str | None, replay: str | None) -> None:
        meta = self.meta(name)
        meta["sample_data"], meta["replay"] = sample_data, replay
        self._write_meta(name, meta)

    def version(self, name: str, version: int | None) -> dict[str, Any]:
        """A published version, or the draft when `version` is None."""
        if version is None:
            return self.draft(name)
        path = self._dir(name) / f"v{version}.agent.yaml"
        if not path.exists():
            raise NotFound(f"{name} has no version {version}.")
        return yaml.safe_load(path.read_text())

    def publish(self, name: str, note: str = "") -> int:
        """Freeze the draft as the next version. The caller checks it compiles first."""
        definition.Agent.model_validate(self.draft(name))
        meta = self.meta(name)
        n = len(meta["versions"]) + 1
        shutil.copy(self._dir(name) / "draft.agent.yaml", self._dir(name) / f"v{n}.agent.yaml")
        meta["versions"].append({"version": n, "published_at": time.time(), "note": note})
        meta["published"] = n
        meta["updated"] = time.time()
        self._write_meta(name, meta)
        return n

    def has_unpublished_changes(self, name: str) -> bool:
        meta = self.meta(name)
        if not meta["published"]:
            return True
        return self.draft(name) != self.version(name, meta["published"])

    def delete(self, name: str) -> None:
        shutil.rmtree(self._dir(name))
