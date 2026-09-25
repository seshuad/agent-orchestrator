"""The CEL evaluator, as an MCP server Conductor calls from `type: mcp` steps.

    evaluate(expressions, data) -> {passed, failed, results}

    expressions  [{name, cel, require?}]: `require` marks a rule that must hold, and is the
                 message shown to the planner (and in the run log) when it doesn't
    data         the values the rules can read, e.g. {"trips": [...], "checks": [...]}
    passed       every `require` rule held
    failed       the messages of the `require` rules that didn't
    results      every expression's value, by name
    error        why a rule couldn't be evaluated (null when all were); the compiled workflow stops
                 the run on it, naming the rule, rather than guessing

In `data.steps`, a step that hasn't run is absent: rules test for it with `has(steps.<id>)`,
which every CEL implementation supports.

It holds no credentials and reaches nothing outside the run.
"""

from __future__ import annotations

from typing import Any

import os

from .cel import Rule, RuleError
from .runstate import RUN_DIR_ENV, record_step


def evaluate(expressions: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("steps"), dict):
        # Not-yet-run steps are absent; a script step's process details aren't part of its output.
        data = {**data, "steps": {k: {f: x for f, x in v.items() if f not in ("stdout", "stderr", "exit_code")}
                                  if isinstance(v, dict) else v
                                  for k, v in data["steps"].items() if v is not None}}
    results: dict[str, Any] = {}
    failed: list[str] = []
    try:
        for e in expressions:
            value = Rule(e["name"], e["cel"]).evaluate(data)
            results[e["name"]] = value
            if e.get("require") is not None:
                if not isinstance(value, bool):
                    raise RuleError(f"Rule {e['name']!r} must be true or false, got {value!r}.")
                if not value:
                    failed.append(e["require"])
    except RuleError as exc:
        return {"passed": False, "failed": failed, "results": results, "error": str(exc)}
    return {"passed": not failed, "failed": failed, "results": results, "error": None}


def main() -> None:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("cel-evaluator")

    @server.tool(name="evaluate", description="Evaluate CEL rules against data. Returns passed, failed and results.")
    def evaluate_tool(expressions: list[dict[str, Any]], data: dict[str, Any], step: str = "") -> dict[str, Any]:
        out = evaluate(expressions, data)
        if step and os.environ.get(RUN_DIR_ENV):
            record_step(step, out)      # what the rules decided, for the run viewer
        return out

    server.run("stdio")


if __name__ == "__main__":
    main()
