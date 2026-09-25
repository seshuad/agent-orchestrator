"""Per-run state on disk: the prototype's stand-in for the event store.

    <run dir>/steps/<step>.json    latest output of each Built-in step (what the gateway checks
                                   run-dependent limits against, e.g. "only emails cited by tidy_up")
    <run dir>/history.jsonl        every recorded output, in order
    <run dir>/gateway.jsonl        every connection call: who, what, allowed or refused, result size

The run viewer's checks come from gateway.jsonl, which records what steps actually did rather
than what a model said it did.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

RUN_DIR_ENV = "AGENT_SERVICE_RUN_DIR"


def run_dir() -> Path:
    d = os.environ.get(RUN_DIR_ENV)
    if not d:
        raise RuntimeError(f"{RUN_DIR_ENV} is not set; start runs with `agent-service run`.")
    path = Path(d)
    (path / "steps").mkdir(parents=True, exist_ok=True)
    return path


def record_step(step: str, output: Any) -> None:
    """The step's latest output, plus every output in order in history.jsonl (a step can run many times)."""
    (run_dir() / "steps" / f"{step}.json").write_text(json.dumps(output, indent=1))
    with (run_dir() / "history.jsonl").open("a") as f:
        f.write(json.dumps({"step": step, "output": output}) + "\n")


def step_output(step: str) -> Any | None:
    path = run_dir() / "steps" / f"{step}.json"
    return json.loads(path.read_text()) if path.exists() else None


def log_call(connection: str, action: str, args: dict[str, Any], outcome: str, detail: str = "") -> None:
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "connection": connection, "action": action,
             "args": args, "outcome": outcome, "detail": detail}
    with (run_dir() / "gateway.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")
