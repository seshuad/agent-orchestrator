"""Design-time feedback for the editor, recomputed on every save.

    check(raw)              errors pinned to fields, warnings, and the compiled YAML if it compiles
    references(raw, step)   what a step's inputs can point at, with types, for the pickers
    graph(block, agent)     a Free-form block's steps in the order their data sets, its loops, and what runs together

Errors block publishing; warnings don't. Whether a person approves before an agent changes anything is
the builder's choice: acting on other people's content with no Approve step first is a warning.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .. import definition
from ..compiler import CompileError, compile_agent, data_rows, output_fields, parallel_groups
from ..definition import ActStep, ApproveStep, AskStep, BranchBlock, BuiltInStep, FreeFormBlock
from ..runtime.cel import Rule, RuleError
from .connections import check_accounts

UNTRUSTED = {"gmail", "github"}    # services whose content other people wrote
RUN_BUILT_INS = {"started"}        # run.* values every run has, besides its run options


def _loc(loc: tuple[Any, ...]) -> str:
    """Pydantic's error location, without the discriminator tags it adds for step kinds."""
    kinds = {"ask", "built-in", "free-form", "branch", "approve", "act"}
    return ".".join(str(p) for p in loc if p not in kinds and not (isinstance(p, str) and p.endswith("Step") or str(p).endswith("Block")))


def _cel_sites(raw: dict[str, Any]) -> list[tuple[str, str]]:
    """Every CEL expression in a definition, with the field it lives in."""
    sites = []
    for rname, rec in (raw.get("records") or {}).items():
        if rec.get("identity"):
            sites.append((f"records.{rname}.identity", rec["identity"]))

    def step_sites(s: dict[str, Any], path: str) -> None:
        for j, r in enumerate(s.get("before_finishing") or []):
            sites.append((f"{path}.before_finishing.{j}.rule", r.get("rule", "")))
        for k, v in (s.get("returns") or {}).items() if s.get("kind") == "free-form" else []:
            sites.append((f"{path}.returns.{k}", v))
        for j, p in enumerate(s.get("paths") or []):
            if p.get("when"):
                sites.append((f"{path}.paths.{j}.when", p["when"]))
        if s.get("pre_select"):
            sites.append((f"{path}.pre_select", s["pre_select"]))
        for j, op in enumerate((s.get("operation") or {}).get("tidy") or []):
            (kind, conf), = op.items() if isinstance(op, dict) and len(op) == 1 else ((None, None),)
            if kind == "flag":
                for n, rule in enumerate(conf or []):
                    sites.append((f"{path}.operation.tidy.{j}.flag.{n}.when", rule.get("when", "")))
            elif isinstance(conf, dict):
                for key in ("keep", "together", "keep_highest"):
                    if conf.get(key):
                        sites.append((f"{path}.operation.tidy.{j}.{kind}.{key}", conf[key]))
        for n, inner in enumerate(s.get("steps") or []):
            step_sites(inner, f"{path}.steps.{n}")

    for i, s in enumerate(raw.get("steps") or []):
        step_sites(s, f"steps.{i}")
    return sites


def _run_option_refs(raw: dict[str, Any]) -> list[dict[str, str]]:
    """A step that reads a run option the agent doesn't have: the run would stop with a missing input."""
    options = {**(raw.get("run_options") or {}), **dict.fromkeys(RUN_BUILT_INS)}
    errors: list[dict[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for j, v in enumerate(value):
                walk(v, f"{path}.{j}")
        elif isinstance(value, str):
            for name in re.findall(r"(?<![\w.])run\.(\w+)(?!\w*\?)", value):
                if name not in options:
                    errors.append({"path": path, "message": f"There's no run option called {name!r} any more. "
                                   "Pick another, or add it back in Settings."})

    for i, s in enumerate(raw.get("steps") or []):
        walk(s, f"steps.{i}")
    return errors


def _policy(agent: definition.Agent) -> list[dict[str, str]]:
    """Human approval is the builder's choice, not the service's. An agent that changes something outside itself
    after reading content other people wrote, with no Approve step first, gets a warning (shown again on publish)."""
    warnings = []
    reads_untrusted = any(getattr(s, "uses", None) and agent.connections[s.uses.connection].service in UNTRUSTED
                          for s in agent.all_steps())
    approved = False
    for i, s in enumerate(agent.steps):
        if isinstance(s, ApproveStep):
            approved = True
        if isinstance(s, ActStep) and reads_untrusted and not approved:
            warnings.append({"path": f"steps.{i}", "message": f"{s.name} changes something outside the agent with no approval "
                             "first, using values from content other people wrote (email, GitHub). Add an Approve step if a person should check them."})
    return warnings


def check(raw: dict[str, Any], accounts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """With `accounts` (the workspace's connections), also checks each connection and each step's actions against them."""
    errors: list[dict[str, str]] = []
    for path, expr in _cel_sites(raw):
        try:
            Rule(path, expr)
        except RuleError as exc:
            where = str(exc).split("is not valid CEL: ", 1)[-1].strip().splitlines()
            errors.append({"path": path, "message": "This isn't valid CEL. Check the brackets and quotes near the ^: "
                           + " ".join(line.strip() for line in where if line.strip())})
    try:
        agent = definition.Agent.model_validate(raw)
    except ValidationError as exc:
        for e in exc.errors():
            errors.append({"path": _loc(e["loc"]), "message": e["msg"].removeprefix("Value error, ")})
        return {"ok": False, "errors": errors, "warnings": [], "compiled": None}
    policy_warnings = _policy(agent)
    errors += _run_option_refs(raw)
    if accounts is not None:
        errors += check_accounts(raw, accounts)
    compiled = None
    try:
        compiled = compile_agent(agent).yaml()
    except CompileError as exc:
        if not str(exc).startswith("Unknown run option"):      # that one is already pinned to its field above
            errors.append({"path": "", "message": str(exc)})
    warnings = list(policy_warnings)
    for s in agent.all_steps():
        if isinstance(s, FreeFormBlock) and not s.before_finishing:
            warnings.append({"path": "", "message": f"{s.name} has no Before finishing rules: the planner decides alone when it's done."})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "compiled": compiled}


# ------------------------------------------------------------------ references for the pickers

def _type_of_returns(step: Any) -> list[tuple[str, str]]:
    if isinstance(step, AskStep):
        return [(n, f.type) for n, f in step.returns.items()]
    if isinstance(step, BuiltInStep):
        types = {"trips": "list of Trip", "records": "list of records", "notes": "list of text", "found": "yes/no", "status": "text",
                 "passed": "yes/no", "differences": "list of text"}
        return [(n, types.get(n, "record")) for n in output_fields(step)]
    return []


def references(raw: dict[str, Any], step_id: str | None) -> list[dict[str, str]]:
    """What a step's inputs and rules can point at. Inside a Free-form block, any other step in it."""
    try:
        agent = definition.Agent.model_validate(raw)
    except ValidationError:
        return []
    refs: list[dict[str, str]] = []
    for n, opt in agent.run_options.items():
        refs.append({"ref": f"run.{n}", "label": f"Run option › {n}", "type": opt.type})
    if agent.trigger.kind == "email":
        refs += [{"ref": "trigger.email_id", "label": "Trigger › the email", "type": "text"},
                 {"ref": "trigger.sender_domain", "label": "Trigger › its sender's domain", "type": "text"}]
    for s in agent.steps:
        if s.id == step_id:
            break
        if isinstance(s, FreeFormBlock):
            if any(i.id == step_id for i in s.steps):
                refs += [{"ref": f"planner.{n}", "label": f"Planner › {n}", "type": f.type} for n, f in s.planner_returns.items()]
                if any(isinstance(i, AskStep) and i.repeat for i in s.steps):
                    refs.append({"ref": "planner.focus", "label": "Planner › focus", "type": "text"})
                refs += [{"ref": f"collected.{n}", "label": f"Collected › {n}", "type": "list"} for n in s.collect]
                for i in s.steps:
                    if i.id != step_id:
                        refs += [{"ref": f"{i.id}.{n}", "label": f"{i.name} › {n}", "type": t} for n, t in _type_of_returns(i)]
                break
            refs += [{"ref": f"{s.id}.{n}", "label": f"{s.name} › {n}", "type": "value"} for n in s.returns]
        elif isinstance(s, ApproveStep):
            refs.append({"ref": f"{s.id}.approved", "label": f"{s.name} › approved items", "type": "list"})
        else:
            refs += [{"ref": f"{s.id}.{n}", "label": f"{s.name} › {n}", "type": t} for n, t in _type_of_returns(s)]
    return refs


# ------------------------------------------------------------------ the Free-form graph

def _heads(value: Any) -> list[str]:
    vals = value if isinstance(value, list) else [value]
    return [v.rstrip("?").split(".")[0] for v in vals]


def graph(block: FreeFormBlock, agent: definition.Agent | None = None) -> dict[str, Any]:
    """Rows by data order (a step sits below everything it needs), solid 'needs' edges, dotted loops,
    and the rows the planner can run at the same time."""
    ids = [s.id for s in block.steps]
    sources = {name: [r.split(".")[0] for r in refs] for name, refs in block.collect.items()}
    edges = []
    for s in block.steps:
        for name, value in s.takes.items():
            optional = isinstance(value, str) and value.endswith("?")
            either = isinstance(value, list) and len(value) > 1
            for v in (value if isinstance(value, list) else [value]):
                head, rest = v.rstrip("?").split(".")[0], v.rstrip("?").split(".")[1:]
                producers = sources.get(rest[0], []) if head == "collected" and rest else ([head] if head in ids else [])
                for p in producers:
                    if p != s.id:
                        edges.append({"from": p, "to": s.id, "label": name, "optional": optional, "either": either})
    depth, _ = data_rows(block)
    loops = [{"from": s.repeat.usually_after, "to": s.id, "label": s.repeat.when or "run again"}
             for s in block.steps if isinstance(s, AskStep) and s.repeat and s.repeat.usually_after in ids]
    nodes = [{"id": s.id, "name": s.name, "kind": s.kind, "row": depth[s.id],
              "mark": ("focus" if isinstance(s, AskStep) and s.repeat else
                       "auto" if isinstance(s, BuiltInStep) and s.reruns_by_itself else None)} for s in block.steps]
    rules = " ".join(r.rule for r in block.before_finishing)
    for n in nodes:
        # A step a rule needs to have run: has(steps.<id>), not the guard !has(steps.<id>).
        if re.search(rf"(?<!!)has\(steps\.{n['id']}\)", rules):
            n["mark"] = "required"
    groups = [{"name": g["name"], "members": g["members"]} for g in parallel_groups(block, agent)] if agent else []
    return {"nodes": nodes, "edges": edges, "loops": loops, "groups": groups}
