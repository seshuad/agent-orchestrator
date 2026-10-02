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
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compiler import compile_agent
from .definition import Agent
from .runtime import limits

HEADER = ("# Compiled by agent-service. Do not edit: change the agent definition and compile again.\n"
          "# Every command below is a program the run worker ships.\n\n")


EMPTY = Path(__file__).parent / "empty-sample-data"     # for runs whose agent has no test data picked


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


def _warehouse(account_id: str | None, accounts: dict[str, dict[str, Any]], connectors: dict[str, dict[str, Any]],
               runs_root: Path) -> tuple[dict[str, Any], float | None]:
    """A BigQuery connector's settings for the gateway, and what's left of its monthly budget."""
    account = accounts.get(account_id or "")
    connector = connectors.get((account or {}).get("connector") or "")
    if account is None or connector is None:
        raise RunError("This agent's BigQuery connection isn't linked to a workspace account. Pick one in its Connections.")
    if (connector.get("status") or {}).get("state") == "attention":
        raise RunError(f"{connector['name']} needs an admin's attention (Connections → Connectors): {connector['status'].get('message', '')}")
    st = connector.get("settings") or {}
    up = {"connector": connector["id"], "name": connector["name"], "auth": st.get("auth") or {"kind": "gcloud"},
          "billing_project": st.get("billing_project"), "location": st.get("location"), "allowed": st.get("allowed") or [],
          "max_bytes_cap": st.get("max_bytes_cap")}
    budget = st.get("monthly_budget_usd")
    if budget in (None, ""):
        return up, None
    left = float(budget) - month_spend(connector["id"], runs_root)
    if left <= 0:
        raise RunError(f"{connector['name']} has used its monthly budget of ${float(budget):.2f}. An admin can raise it under Connectors.")
    return up, round(left, 4)


def message_inputs(agent: Agent, message: dict[str, Any] | None) -> dict[str, str]:
    """A Pub/Sub message as run inputs: its id, when it was published, and each field the trigger declares (from the
    message's JSON data, else its attributes). A test run without a message uses the trigger's sample."""
    if message is None:
        message = {"message_id": "sample", "published_at": "", "fields": dict(agent.trigger.sample or {})}
    fields = message.get("fields") or {}
    out = {"message_id": str(message.get("message_id") or ""), "published_at": str(message.get("published_at") or "")}
    for name, fd in (agent.trigger.message or {}).items():
        value = fields.get(name)
        if value is None:
            continue
        if fd.type == "yes/no":
            out[name] = "true" if value in (True, "true", "True", "1", 1) else "false"
        else:
            out[name] = str(value)
    return out


def month_spend(connector_id: str, runs_root: Path) -> float:
    """What a BigQuery connector's queries cost this calendar month, from every run's gateway log."""
    from .runtime.bigquery_api import cost_of
    start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d"))
    total = 0.0
    for log in runs_root.glob("*/gateway.jsonl"):
        if log.stat().st_mtime < start:
            continue
        for line in log.read_text().splitlines():
            c = json.loads(line)
            if c.get("connector") == connector_id and c.get("ts", 0) >= start:
                total += cost_of(c.get("bytes_billed"))
    return total


def _upstream(account_id: str, accounts: dict[str, dict[str, Any]], connectors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What the gateway needs to reach an MCP connector for this account: signed into the limits token, never from the model."""
    account = accounts.get(account_id)
    connector = connectors.get((account or {}).get("connector") or "")
    if account is None or connector is None:
        raise RunError(f"The MCP account {account_id!r} or its connector is gone. Pick another account in the agent's Connections.")
    if (connector.get("status") or {}).get("state") == "attention":
        raise RunError(f"{connector['name']} needs an admin's attention (Connections → Connectors): {connector['status'].get('message', '')}")
    settings = connector.get("settings") or {}
    return {"connector": connector["id"], "name": connector["name"], "server": settings.get("server") or {},
            "auth": settings.get("auth") or {"kind": "none"},
            "tools": {t["name"]: {"treat": t.get("treat"), "pin": t.get("approved_pin"), "limits": t.get("limits", [])}
                      for t in connector.get("tools") or [] if t.get("treat") in ("read", "act")}}


CACHED = Path(__file__).parent / "runtime" / "conductor_cached.py"


def conductor_command() -> list[str]:
    """How to start Conductor: through conductor_cached.py (prompt caching on for Claude steps) in Conductor's own
    Python, found from the `conductor` script's first line; plain `conductor` if that can't be found, or if
    AGENT_SERVICE_PROMPT_CACHE=0."""
    script = shutil.which("conductor")
    if os.environ.get("AGENT_SERVICE_PROMPT_CACHE", "1") == "0" or not script:
        return ["conductor"]
    try:
        first = Path(script).read_text(errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return ["conductor"]
    python = first[2:].strip() if first.startswith("#!") else ""
    return [python, str(CACHED)] if python and Path(python).exists() and "python" in Path(python).name else ["conductor"]


LIVE_SERVICES = {"gmail", "github", "bigquery"}    # services a run can use for real so far; the rest stay on sample data
ALWAYS_LIVE = {"mcp"}                  # an MCP connector has no sample data: its steps always reach the real system


def prepare(agent: Agent, *, sample_data: Path, runs_root: Path, inputs: dict[str, str] | None = None,
            email_id: str | None = None, replay: Path | None = None, replay_gates: bool = True,
            run_id: str | None = None, vault: Path | None = None, trigger_email: dict[str, Any] | None = None,
            live: bool | None = None, accounts: dict[str, dict[str, Any]] | None = None,
            connectors: dict[str, dict[str, Any]] | None = None, transform: Any = None,
            memory: list[dict[str, Any]] | None = None, trigger_message: dict[str, Any] | None = None) -> Prepared:
    """With `live` (default: when there's a `vault`), Gmail and GitHub steps use the real accounts their connections
    name; the rest stay on sample data. MCP steps always use the real system: `accounts` and `connectors` say which
    server, how it signs in and which tools its admin approved, and all of that goes into the signed limits token.
    `trigger_email` is the email that started the run ({id, from}), for email triggers on real accounts."""
    live = vault is not None if live is None else live
    compiled = compile_agent(agent, replay=replay is not None, replay_gates=replay_gates)
    if transform is not None:          # e.g. only one step, for re-running it on recorded inputs
        compiled = transform(compiled)
    run_dir = (runs_root / (run_id or f"{agent.name}-{time.strftime('%Y%m%d-%H%M%S')}")).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    workflow = run_dir / "workflow.yaml"
    workflow.write_text(compiled.yaml(HEADER))
    for name in compiled.files:                     # a Parallel block's steps, run once for each item
        (run_dir / name).write_text(compiled.file_yaml(name, HEADER))
    # Confirmed past cases, as they stand when the run starts: recall reads this copy, so a run is repeatable.
    (run_dir / "memory.json").write_text(json.dumps([c for c in memory or [] if c.get("status") in ("confirmed", "corrected")], default=str))

    inputs = dict(inputs or {})
    if agent.trigger.kind == "email":
        if not email_id:
            raise RunError("This agent starts when an email arrives: pick the email to run it on.")
        email = trigger_email or next((e for e in _emails(sample_data) if e["id"] == email_id), None)
        if email is None:
            raise RunError(f"There's no email {email_id!r} in the sample data.")
        inputs["email_id"] = email_id
        inputs["sender_domain"] = email["from"].rsplit("@", 1)[-1].strip(" >").lower()

    if agent.trigger.kind == "pubsub":
        inputs.update(message_inputs(agent, trigger_message))
    key = secrets.token_hex(32)
    env = {**os.environ, "AGENT_SERVICE_RUN_DIR": str(run_dir), "AGENT_SERVICE_SAMPLE_DATA": str(sample_data.resolve()),
           "AGENT_SERVICE_SIGNING_KEY": key}
    if replay is not None:
        env["AGENT_SERVICE_REPLAY"] = str(replay.resolve())
    if vault is not None:
        env["AGENT_SERVICE_VAULT"] = str(vault.resolve())
    for var, spec in compiled.limits.items():
        real = spec["connection"] in ALWAYS_LIVE or (live and spec["connection"] in LIVE_SERVICES)
        spec = {**spec, "source": "live" if real else "sample"}
        if spec["source"] == "live" and not spec.get("account"):
            raise RunError(f"A {spec['connection']} connection in this agent isn't linked to a workspace account; pick one in its Connections.")
        if spec["connection"] == "mcp":
            spec["upstream"] = _upstream(spec["account"], accounts or {}, connectors or {})
        if spec["connection"] == "bigquery":
            spec["upstream"], spec["budget_left_usd"] = _warehouse(spec.get("account"), accounts or {}, connectors or {}, runs_root)
            if vault is None:
                raise RunError("MCP connectors need the workspace vault.")
        compiled.limits[var] = spec
        env[var] = limits.mint(_resolve(spec, inputs), key.encode())
    (run_dir / "limits.json").write_text(json.dumps({k: _resolve(v, inputs) for k, v in compiled.limits.items()}, indent=1))
    if "started" in (compiled.workflow.get("workflow") or {}).get("input", {}) and "started" not in inputs:
        from datetime import datetime, timezone
        inputs["started"] = datetime.now(timezone.utc).isoformat(timespec="seconds")    # run.started
    command = conductor_command() + ["run", str(workflow)] + [x for k, v in inputs.items() for x in ("--input", f"{k}={v}")]
    return Prepared(run_dir, workflow, env, inputs, command)
