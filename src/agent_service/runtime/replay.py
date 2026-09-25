"""A scripted stand-in for model steps and approvals, for testing whole workflows in Conductor.

    agent-service-replay <step>      prints the step's next scripted output as JSON
    agent-service-replay --mcp       the same, as an MCP tool (answer), for steps in a parallel group:
                                     Conductor runs only model, set and MCP steps in parallel

The script (AGENT_SERVICE_REPLAY, a YAML file) lists each step's outputs in the order they are
used: {plan: [{next: read_invoice, ...}, ...], approve_payment: [{selected: all}]}. Everything
else in the run is real: Conductor's routing, the Built-in steps, the CEL evaluator, the gateway.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .runstate import record_step, run_dir


class NoAnswer(Exception):
    pass


def answer(step: str) -> dict[str, Any]:
    """The step's next scripted output, recorded for the run log."""
    script = yaml.safe_load(Path(os.environ["AGENT_SERVICE_REPLAY"]).read_text())
    counter = run_dir() / "replay" / step
    counter.parent.mkdir(exist_ok=True)
    n = int(counter.read_text()) if counter.exists() else 0
    outputs = script.get(step, [])
    if n >= len(outputs):
        raise NoAnswer(f"The replay script has no output #{n + 1} for {step!r}.")
    counter.write_text(str(n + 1))
    record_step(step, outputs[n])
    return outputs[n]


def main() -> None:
    if sys.argv[1] == "--mcp":
        from mcp.server.mcpserver import MCPServer
        server = MCPServer("replay")

        @server.tool(name="answer", description="The step's next scripted output.")
        def answer_tool(step: str) -> dict[str, Any]:
            return answer(step)

        server.run("stdio")
        return
    try:
        json.dump(answer(sys.argv[1]), sys.stdout)
    except NoAnswer as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
