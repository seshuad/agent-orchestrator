"""Pub/Sub triggers: a published agent whose trigger is a Pub/Sub subscription runs once per message.

    Listener(store, runs).start()      one background thread for the whole service

Every few seconds, for each agent whose published version has a Pub/Sub trigger (and isn't paused), it pulls from the
subscription with the Google Cloud credentials of the trigger's account (a BigQuery connector's: a service account
key, the gcloud account or the machine's default credentials; they need Pub/Sub Subscriber on the subscription).
Each message:

    its fields       its data as JSON (or {"data": text}), then its attributes for names the data doesn't have
    duplicate?       Pub/Sub delivers at least once: a message id already seen is acknowledged and left
    filter           the trigger's `when` (CEL over `message`): false, and it's acknowledged and skipped
    run              the published version, on real accounts, with the message's fields as trigger.<field>

Every message is acknowledged once handled, even when its run couldn't start (the reason is logged), so one bad
message doesn't come back forever. Each one is logged to agents/<name>/pubsub.jsonl; agents/<name>/pubsub.json keeps
the listener's state: paused, last pull, last error, and the message ids seen recently.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any, Callable

PULL_EVERY = 5               # seconds between rounds
SEEN_KEEP = 500              # message ids remembered, for duplicates
SCOPE = "https://www.googleapis.com/auth/pubsub"
API = "https://pubsub.googleapis.com/v1"


def fields_of(message: dict[str, Any]) -> dict[str, Any]:
    """A pulled message's fields: its data as JSON (or {"data": text}), plus its attributes the data doesn't name."""
    raw = base64.b64decode(message.get("data") or "").decode("utf-8", errors="replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {"data": raw}
    if not isinstance(data, dict):
        data = {"data": data}
    return {**(message.get("attributes") or {}), **data}


class Listener:
    def __init__(self, store: Any, runs: Any, session_for: Callable[[dict[str, Any]], Any] | None = None):
        self.store, self.runs = store, runs
        self.session_for = session_for or self._session
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, daemon=True, name="pubsub-listener")
        self.thread.start()

    def close(self) -> None:
        self.stop.set()

    # ------------------------------------------------------------------ state and log

    def state(self, name: str) -> dict[str, Any]:
        path = self.store._dir(name) / "pubsub.json"
        return json.loads(path.read_text()) if path.exists() else {"paused": False, "seen": []}

    def _save_state(self, name: str, state: dict[str, Any]) -> None:
        (self.store._dir(name) / "pubsub.json").write_text(json.dumps(state, indent=1))

    def log(self, name: str, limit: int = 50) -> list[dict[str, Any]]:
        path = self.store._dir(name) / "pubsub.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return [json.loads(l) for l in lines[-limit:]][::-1]

    def _record(self, name: str, entry: dict[str, Any]) -> None:
        with (self.store._dir(name) / "pubsub.jsonl").open("a") as f:
            f.write(json.dumps({"at": time.time(), **entry}) + "\n")

    def set_paused(self, name: str, paused: bool) -> dict[str, Any]:
        state = self.state(name)
        state["paused"] = paused
        self._save_state(name, state)
        return state

    # ------------------------------------------------------------------ pulling

    def _session(self, trigger: dict[str, Any]) -> Any:
        """An authorized HTTP session with the trigger account's Google Cloud credentials."""
        from google.auth.transport.requests import AuthorizedSession

        from ..runtime.bigquery_api import credentials
        account = self.store.accounts().get(trigger.get("account") or "")
        connector = self.store.connector((account or {}).get("connector"))
        if account is None or connector is None or connector.get("type") != "bigquery":
            raise PermissionError("Pick the Google Cloud account it pulls with: a BigQuery connection's account.")
        st = connector.get("settings") or {}
        return AuthorizedSession(credentials({"connector": connector["id"], "auth": st.get("auth") or {"kind": "gcloud"}}, [SCOPE]))

    def check(self, trigger: dict[str, Any]) -> dict[str, Any]:
        """Whether the subscription can be read with the trigger's account: its topic, or why not."""
        try:
            r = self.session_for(trigger).get(f"{API}/{trigger['subscription']}", timeout=20)
        except Exception as exc:
            return {"ok": False, "message": str(exc)}
        if r.status_code != 200:
            return {"ok": False, "message": (r.json().get("error") or {}).get("message", r.text)[:300]}
        return {"ok": True, "topic": r.json().get("topic"), "message": "Readable."}

    def _loop(self) -> None:
        while not self.stop.wait(PULL_EVERY):
            for name in list(self.store.names()):
                try:
                    self.poll(name)
                except Exception:
                    pass                                 # poll() records its own errors; never let one agent stop the rest

    def poll(self, name: str) -> int:
        """One pull for one agent, if its published version has a Pub/Sub trigger and isn't paused. Returns how many
        messages it handled."""
        meta = self.store.meta(name)
        if not meta.get("published"):
            return 0
        raw = self.store.version(name, meta["published"])
        trigger = raw.get("trigger") or {}
        if trigger.get("kind") != "pubsub":
            return 0
        state = self.state(name)
        if state.get("paused"):
            return 0
        state["last_pull_at"] = time.time()
        try:
            session = self.session_for(trigger)
            r = session.post(f"{API}/{trigger['subscription']}:pull", json={"maxMessages": 10}, timeout=30)
            if r.status_code != 200:
                raise RuntimeError((r.json().get("error") or {}).get("message", r.text)[:300])
            received = r.json().get("receivedMessages") or []
        except Exception as exc:
            state["last_error"] = str(exc)[:300]
            self._save_state(name, state)
            return 0
        state["last_error"] = None
        acks = []
        for item in received:
            self._handle(name, meta["published"], trigger, item.get("message") or {}, state)
            acks.append(item["ackId"])
        if acks:
            session.post(f"{API}/{trigger['subscription']}:acknowledge", json={"ackIds": acks}, timeout=30)
        self._save_state(name, state)
        return len(received)

    def _handle(self, name: str, version: int, trigger: dict[str, Any], message: dict[str, Any], state: dict[str, Any]) -> None:
        mid = message.get("messageId") or message.get("message_id") or ""
        if mid in state.setdefault("seen", []):
            self._record(name, {"message_id": mid, "outcome": "duplicate"})
            return
        state["seen"] = (state["seen"] + [mid])[-SEEN_KEEP:]
        fields = fields_of(message)
        entry = {"message_id": mid, "fields": fields}
        if trigger.get("when"):
            from ..runtime.cel import Rule
            try:
                passed = bool(Rule("trigger.when", trigger["when"]).evaluate({"message": fields}))
            except Exception as exc:
                self._record(name, {**entry, "outcome": "failed", "detail": f"The filter couldn't be checked: {exc}"})
                return
            if not passed:
                self._record(name, {**entry, "outcome": "skipped", "detail": "The filter is false for this message."})
                return
        try:
            run = self.runs.start(name, version=version, inputs={}, email_id=None, scripted=False, started_by="Pub/Sub",
                                  live=True, trigger="pubsub",
                                  trigger_message={"message_id": mid, "published_at": message.get("publishTime") or "", "fields": fields})
        except Exception as exc:
            self._record(name, {**entry, "outcome": "failed", "detail": str(exc)[:300]})
            return
        self._record(name, {**entry, "outcome": "started", "run": run["id"]})
