"""Preparing a run: what the control plane and a run worker do between them before Conductor starts.

    prepare(agent, ...) -> Prepared(run_dir, workflow, env, inputs, command)

Compile the pinned definition, make a run directory, fill in trigger values (an email trigger's
message id and its sender's domain, from the email's headers, never from a model), and mint one
signed limits token per connection use. The command line runs the result in the terminal; the
server runs it with Conductor's web mode so approvals can be answered from the designer.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compiler import compile_agent
from .definition import Agent
from .runtime import limits

HEADER = ("# Compiled by agent-service. Do not edit: change the agent definition and compile again.\n"
          "# Every command below is a program the run worker ships.\n\n")


class RunError(Exception):
    """The run can't start; the message says why in the builder's terms."""


@dataclass
class Prepared:
    run_dir: Path
    workflow: Path
    env: dict[str, str]
    inputs: dict[str, str]
    command: list[str] = field(default_factory=list)


def _emails(sample_data: Path) -> list[dict[str, Any]]:
    path = sample_data / "emails.json"
    return json.loads(path.read_text()) if path.exists() else []


def _resolve(spec: dict[str, Any], inputs: dict[str, str]) -> dict[str, Any]:
    return {k: inputs[v.split(".", 1)[1]] if isinstance(v, str) and v.startswith("$input.") else v for k, v in spec.items()}


LIVE_SERVICES = {"gmail"}          # services a run can use for real so far; the rest stay on sample data


def prepare(agent: Agent, *, sample_data: Path, runs_root: Path, inputs: dict[str, str] | None = None,
            email_id: str | None = None, replay: Path | None = None, replay_gates: bool = True,
            run_id: str | None = None, vault: Path | None = None, trigger_email: dict[str, Any] | None = None) -> Prepared:
    """With `vault`, Gmail steps read the real accounts their connections name (the rest stay on sample data).
    `trigger_email` is the email that started the run ({id, from}), for email triggers on real accounts."""
    compiled = compile_agent(agent, replay=replay is not None, replay_gates=replay_gates)
    run_dir = (runs_root / (run_id or f"{agent.name}-{time.strftime('%Y%m%d-%H%M%S')}")).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    workflow = run_dir / "workflow.yaml"
    workflow.write_text(compiled.yaml(HEADER))

    inputs = dict(inputs or {})
    if agent.trigger.kind == "email":
        if not email_id:
            raise RunError("This agent starts when an email arrives: pick the email to run it on.")
        email = trigger_email or next((e for e in _emails(sample_data) if e["id"] == email_id), None)
        if email is None:
            raise RunError(f"There's no email {email_id!r} in the sample data.")
        inputs["email_id"] = email_id
        inputs["sender_domain"] = email["from"].rsplit("@", 1)[-1].strip(" >").lower()

    key = secrets.token_hex(32)
    env = {**os.environ, "AGENT_SERVICE_RUN_DIR": str(run_dir), "AGENT_SERVICE_SAMPLE_DATA": str(sample_data.resolve()),
           "AGENT_SERVICE_SIGNING_KEY": key}
    if replay is not None:
        env["AGENT_SERVICE_REPLAY"] = str(replay.resolve())
    if vault is not None:
        env["AGENT_SERVICE_VAULT"] = str(vault.resolve())
    for var, spec in compiled.limits.items():
        spec = {**spec, "source": "live" if vault is not None and spec["connection"] in LIVE_SERVICES else "sample"}
        if spec["source"] == "live" and not spec.get("account"):
            raise RunError("A Gmail connection in this agent isn't linked to a workspace account; pick one in its Connections.")
        compiled.limits[var] = spec
        env[var] = limits.mint(_resolve(spec, inputs), key.encode())
    (run_dir / "limits.json").write_text(json.dumps({k: _resolve(v, inputs) for k, v in compiled.limits.items()}, indent=1))
    command = ["conductor", "run", str(workflow)] + [x for k, v in inputs.items() for x in ("--input", f"{k}={v}")]
    return Prepared(run_dir, workflow, env, inputs, command)
