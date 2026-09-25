"""agent-service: compile an agent definition, and run it the way a run worker would.

    agent-service compile <agent.yaml> [-o workflow.yaml] [--replay]
    agent-service run <agent.yaml> --sample-data DIR [--email-id ID] [--input name=value ...]
                      [--replay script.yaml]

`run` does what the control plane and a run worker do between them: compile the pinned
definition, make a run directory, fill in trigger values (the email's id and its sender's domain,
from the email's headers), mint one signed limits token per connection use, and start Conductor.
Model steps call the Claude API (ANTHROPIC_API_KEY); with --replay they are scripted instead.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import definition, runner
from .compiler import compile_agent

RUNS = Path(".runs")


def _header(agent_path: str) -> str:
    return (f"# Compiled by agent-service from {agent_path}. Do not edit: change the agent definition\n"
            f"# and compile again. Every command below is a program the run worker ships.\n\n")


def cmd_compile(a: argparse.Namespace) -> None:
    compiled = compile_agent(definition.load(a.agent), replay=a.replay)
    text = compiled.yaml(_header(a.agent))
    if a.output:
        Path(a.output).write_text(text)
        Path(a.output).with_suffix(".limits.json").write_text(json.dumps(compiled.limits, indent=1) + "\n")
        print(f"Wrote {a.output} and {Path(a.output).with_suffix('.limits.json')}")
    else:
        sys.stdout.write(text)


def cmd_run(a: argparse.Namespace) -> int:
    agent = definition.load(a.agent)
    inputs = dict(kv.split("=", 1) for kv in a.input)
    try:
        prepared = runner.prepare(agent, sample_data=Path(a.sample_data), runs_root=RUNS, inputs=inputs,
                                  email_id=a.email_id, replay=Path(a.replay) if a.replay else None)
    except runner.RunError as exc:
        sys.exit(str(exc))
    print(f"Run directory: {prepared.run_dir}\n$ {' '.join(prepared.command)}\n", flush=True)
    code = subprocess.call(prepared.command, env=prepared.env)
    _summary(prepared.run_dir)
    return code


def _summary(run: Path) -> None:
    log = run / "gateway.jsonl"
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    refused = [c for c in calls if c["outcome"] == "refused"]
    print(f"\nGateway: {len(calls)} call(s), {len(refused)} refused.")
    for c in refused:
        print(f"  refused {c['connection']} {c['action']} {c['args']}: {c['detail']}")
    for name in ("calendar.json",):
        if (run / name).exists():
            print(f"Calendar: {len(json.loads((run / name).read_text()))} event(s) created.")
    for sheet in sorted((run / "sheets").glob("*.json")) if (run / "sheets").exists() else []:
        print(f"Sheet {sheet.stem}: {len(json.loads(sheet.read_text()))} row(s) added.")
    print(f"Step outputs: {run / 'steps'}")


def main() -> None:
    p = argparse.ArgumentParser(prog="agent-service")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("compile", help="Compile an agent definition to Conductor YAML.")
    c.add_argument("agent")
    c.add_argument("-o", "--output")
    c.add_argument("--replay", action="store_true", help="Script model steps and approvals, for tests.")
    r = sub.add_parser("run", help="Compile and run an agent on sample data.")
    r.add_argument("agent")
    r.add_argument("--sample-data", required=True)
    r.add_argument("--email-id")
    r.add_argument("--input", action="append", default=[], help="A run option, as name=value.")
    r.add_argument("--replay", help="A replay script: run without a model or a person.")
    v = sub.add_parser("serve", help="Start the designer: the web app and its API.")
    v.add_argument("--port", type=int, default=8700)
    v.add_argument("--home", default=".workspace", help="Where agents and runs are stored.")
    a = p.parse_args()
    if a.command == "compile":
        cmd_compile(a)
    elif a.command == "serve":
        import uvicorn
        from .server.app import create_app
        print(f"Designer: http://127.0.0.1:{a.port}", flush=True)
        uvicorn.run(create_app(Path(a.home)), host="127.0.0.1", port=a.port, log_level="warning")
    else:
        sys.exit(cmd_run(a))


if __name__ == "__main__":
    main()
