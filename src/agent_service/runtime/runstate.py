"""Per-run state on disk: the prototype's stand-in for the event store.

    <run dir>/steps/<step>.json    latest output of each Built-in step (what the gateway checks
                                   run-dependent limits against, e.g. "only emails cited by group_trips")
    <run dir>/history.jsonl        every recorded output (and, for Built-in and rule steps, its inputs), in order
    <run dir>/events.jsonl         Conductor's event log, copied here when the run ends (the temp copy gets cleaned up)
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


def record_step(step: str, output: Any, inputs: Any = None) -> None:
    """The step's latest output, plus every output in order in history.jsonl (a step can run many times).
    `inputs`, when given, is what the step was given (a Built-in step's stdin, a rule's data), for the step inspector."""
    (run_dir() / "steps" / f"{step}.json").write_text(json.dumps(output, indent=1))
    entry = {"step": step, "output": output, **({"inputs": inputs} if inputs is not None else {})}
    with (run_dir() / "history.jsonl").open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def step_output(step: str) -> Any | None:
    path = run_dir() / "steps" / f"{step}.json"
    return json.loads(path.read_text()) if path.exists() else None


STEP_ENV = "AGENT_SERVICE_STEP"          # the step a connection call is made for, set per gateway server and Built-in step


RESULT_CAP = 200_000                     # characters of a call's result kept for the step inspector


def log_call(connection: str, action: str, args: dict[str, Any], outcome: str, detail: str = "", result: Any = None,
             **extra: Any) -> None:
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ts": time.time(), "step": os.environ.get(STEP_ENV, ""),
             "connection": connection, "action": action, "args": args, "outcome": outcome, "detail": detail,
             **{k: v for k, v in extra.items() if v is not None}}
    if result is not None:
        text = result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
        entry["result"] = text[:RESULT_CAP] + (f"\n[{len(text) - RESULT_CAP} more characters not kept]" if len(text) > RESULT_CAP else "")
    with (run_dir() / "gateway.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")
