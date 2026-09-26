"""The step inspector: everything about one run of one step, from the run's own records.

    inspect_step(evs, run_dir, step, n)   the n-th run (0-based) of `step` in the run

    Model steps (Ask, planner)  the system prompt (from the compiled workflow), the prompt as rendered with
                                its inputs, every tool call with its arguments, result and the gateway's
                                decision, schema repairs, the output, model, tokens, cost, time
    Built-in and rule steps     what they were given (recorded by the step) and what they returned
    Plumbing (collect, ...)     the value it set
    Approvals                   what was asked, the choices, what was chosen

Sources: Conductor's event log, <run dir>/history.jsonl (outputs and inputs the steps recorded),
<run dir>/gateway.jsonl (every connection call, by step), and <run dir>/workflow.yaml.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import yaml

from ..compiler import IN_GROUP

STARTS = {"agent_started", "parallel_agent_started", "script_started", "mcp_started", "set_started", "gate_presented"}
ENDS = {"agent_completed", "parallel_agent_completed", "agent_failed", "parallel_agent_failed", "script_completed",
        "script_failed", "mcp_completed", "mcp_failed", "set_completed", "gate_resolved"}


def _value(v: Any) -> Any:
    """Event payloads carry outputs as JSON, a Python repr, or already parsed."""
    if not isinstance(v, str):
        return v
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(v)
        except Exception:
            continue
    return v


def _base(name: str) -> str:
    return (name or "").removesuffix(IN_GROUP)


def runs_of(evs: list[dict[str, Any]], step: str) -> list[tuple[int, int]]:
    """(start index, end index) in the event log of each run of `step`, in order."""
    out, open_at = [], None
    for i, e in enumerate(evs):
        d = e.get("data") or {}
        if _base(d.get("agent_name", "")) != step:
            continue
        if e["type"] == "mcp_completed" and d.get("group_name"):
            continue                      # a scripted group member: its parallel_agent_completed follows
        if e["type"] in STARTS and open_at is None:
            open_at = i
        elif e["type"] in ENDS:
            out.append((open_at if open_at is not None else i, i))
            open_at = None
    return out


def _recorded(run_dir: Path, step: str, n: int) -> dict[str, Any] | None:
    path = run_dir / "history.jsonl"
    if not path.exists():
        return None
    mine = [h for h in (json.loads(l) for l in path.read_text().splitlines()) if h["step"] == step]
    return mine[n] if n < len(mine) else None


def _ts(call: dict[str, Any]) -> float:
    if call.get("ts"):
        return call["ts"]
    from datetime import datetime
    try:
        return datetime.strptime(call["at"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except Exception:
        return 0.0


def _calls(run_dir: Path, step: str, t0: float, t1: float) -> list[dict[str, Any]]:
    """The gateway's record of a step's calls: tagged with the step, or (older runs) made while it ran."""
    path = run_dir / "gateway.jsonl"
    if not path.exists():
        return []
    calls = [json.loads(l) for l in path.read_text().splitlines()]
    return [c for c in calls if c.get("step", step) == step and t0 - 1 <= _ts(c) <= t1 + 1]


def inspect_step(evs: list[dict[str, Any]], run_dir: Path, step: str, n: int) -> dict[str, Any]:
    spans = runs_of(evs, step)
    if n >= len(spans):
        raise LookupError(f"{step} didn't run {n + 1} time{'s' if n else ''} in this run.")
    a, b = spans[n]
    span = evs[a:b + 1]
    end = span[-1]
    d = end.get("data") or {}
    wf = yaml.safe_load((run_dir / "workflow.yaml").read_text()) if (run_dir / "workflow.yaml").exists() else {}
    agent = next((x for x in (wf or {}).get("agents", []) if x.get("name") == step), {})
    out: dict[str, Any] = {"step": step, "n": n, "runs": len(spans), "type": agent.get("type", "agent"),
                           "took": round(d.get("elapsed") or (end["timestamp"] - evs[a]["timestamp"]), 2),
                           "at": round(evs[a]["timestamp"] - evs[0]["timestamp"], 1) if evs else 0,
                           "error": d.get("message") if end["type"].endswith("failed") else None}
    kind = out["type"]
    if kind == "agent":
        prompts = [e["data"].get("rendered_prompt", "") for e in span if e["type"] == "agent_prompt_rendered"]
        tools, pending = [], {}
        for e in span:
            t = e["type"]
            ed = e.get("data") or {}
            if t == "agent_tool_start":
                entry = {"tool": ed.get("tool_name", "").split("__")[-1], "args": _value(ed.get("arguments")), "result": None, "truncated": None}
                tools.append(entry)
                pending[ed.get("tool_name")] = entry
            elif t == "agent_tool_complete" and ed.get("tool_name") in pending:
                pending.pop(ed.get("tool_name"))["result"] = ed.get("result")
            elif t == "agent_tool_output_truncated":
                for entry in reversed(tools):
                    if entry["tool"] == ed.get("tool_name", "").split("__")[-1]:
                        entry["truncated"] = {"kept": ed.get("kept_chars"), "of": ed.get("original_chars")}
                        break
        calls = _calls(run_dir, step, evs[a]["timestamp"], end["timestamp"])
        for entry in tools:                        # pair each tool call with the gateway's decision, in order
            match = next((c for c in calls if c["action"] in (entry["tool"], {"search_email": "search", "read_email": "open",
                          "search_github": "search", "read_issue": "open", "read_file": "read"}.get(entry["tool"]))), None)
            if match:
                calls.remove(match)
                entry["gateway"] = {"outcome": match["outcome"], "detail": match["detail"]}
                if match.get("result") is not None:
                    entry["result"] = match["result"]           # the full result; Conductor's log keeps a preview
        output = _value(d.get("output")) if "output" in d else (_recorded(run_dir, step, n) or {}).get("output")
        out.update(model=d.get("model") or agent.get("model"), tokens=d.get("tokens"), input_tokens=d.get("input_tokens"),
                   output_tokens=d.get("output_tokens"), cost=d.get("cost_usd"), system_prompt=agent.get("system_prompt"),
                   prompt=prompts[-1] if prompts else None, retried_prompts=prompts[:-1], tools=tools, output=output,
                   output_schema=agent.get("output"),
                   repairs=[e["data"].get("error", "") for e in span if e["type"] == "agent_parse_recovery"],
                   in_group=next((e["data"].get("group_name") for e in span if e["data"].get("group_name")), None),
                   rerunnable=bool(prompts))
    elif kind == "script":
        rec = _recorded(run_dir, step, n) or {}
        stdout = _value(d.get("stdout")) if d.get("stdout") else None
        out.update(inputs=rec.get("inputs"), output=rec.get("output", stdout), command=[agent.get("command"), *agent.get("args", [])],
                   stderr=(d.get("stderr") or "").strip() or None, exit_code=d.get("exit_code"),
                   calls=_calls(run_dir, step, evs[a]["timestamp"], end["timestamp"]),
                   scripted=agent.get("command") == "agent-service-replay", rerunnable=rec.get("inputs") is not None)
    elif kind == "mcp":
        rec = _recorded(run_dir, step, n) or {}
        out.update(inputs=rec.get("inputs"), output=rec.get("output"), tool=agent.get("tool"), rerunnable=False)
    elif kind == "set":
        out.update(output=_value(d.get("value_repr")), rerunnable=False)
    elif kind == "human_gate":
        shown = next((e["data"] for e in span if e["type"] == "gate_presented"), {})
        out.update(prompt=shown.get("prompt"), options=shown.get("option_details", []), chosen=d.get("selected_option"),
                   additional=d.get("additional_input"), rerunnable=False)
    return out


def outputs(evs: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    """Each step's latest output in a run, as expectations read it: steps.<id>.<field>. Approvals give {selected}."""
    out: dict[str, Any] = {}
    hist: dict[str, list[Any]] = {}
    if (run_dir / "history.jsonl").exists():
        for h in (json.loads(l) for l in (run_dir / "history.jsonl").read_text().splitlines()):
            hist.setdefault(h["step"], []).append(h["output"])
    for e in evs:
        d = e.get("data") or {}
        name = _base(d.get("agent_name", ""))
        if e["type"] == "agent_completed" and "output" in d:
            out[name] = _value(d["output"])
        elif e["type"] in ("script_completed", "mcp_completed", "parallel_agent_completed") and name in hist:
            out[name] = hist[name][-1]
        elif e["type"] == "script_completed" and d.get("stdout"):
            out[name] = _value(d["stdout"])
        elif e["type"] == "gate_resolved":
            out[name] = {"selected": d.get("selected_option"), "ids": (d.get("additional_input") or {}).get("ids")}
    for step, outs in hist.items():
        out.setdefault(step, outs[-1])
    return out
