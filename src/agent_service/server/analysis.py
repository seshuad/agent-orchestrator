"""Design-time feedback for the editor, recomputed on every save.

    check(raw)              errors pinned to fields, warnings, and the compiled YAML if it compiles
    references(raw, step)   what a step's inputs can point at, with types, for the pickers
    graph(block, agent)     a Free-form block's steps in the order their data sets, its loops, and what runs together

Errors block publishing; warnings don't. Whether a person approves before an agent changes anything is the
builder's choice: an Approve step is available, never required. Where one might matter, the Act step gets a
suggestion on its own panel.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .. import definition
from ..compiler import CompileError, _slug, compile_agent, data_rows, output_fields, parallel_groups
from ..definition import ActStep, ApproveStep, AskStep, BranchBlock, BuiltInStep, FreeFormBlock, ParallelBlock
from ..runtime.cel import Rule, RuleError
from .connections import check_accounts

UNTRUSTED = {"gmail", "github", "mcp", "sharepoint"}     # services whose content other people wrote (BigQuery is your own data)
RUN_BUILT_INS = {"started"}        # run.* values every run has, besides its run options


def _loc(loc: tuple[Any, ...]) -> str:
    """Pydantic's error location, without the discriminator tags it adds for step kinds."""
    kinds = {"ask", "built-in", "free-form", "branch", "approve", "act"}
    return ".".join(str(p) for p in loc if p not in kinds and not (isinstance(p, str) and p.endswith("Step") or str(p).endswith("Block")))


def _sql_problems(raw: dict[str, Any]) -> list[dict[str, str]]:
    """Trino and Spark SQL steps' queries, parsed in their dialect on every save: the SQL parses, and it only reads.
    (Which tables it may read is checked when it runs, against the connector's and the step's limits.)"""
    from ..runtime import sql_engines
    errors = []

    def walk(steps: list[dict[str, Any]], path: str) -> None:
        for i, s in enumerate(steps or []):
            op = s.get("operation") or {}
            for kind in ("trino", "spark-sql"):
                sql = str((op.get(kind) or {}).get("sql") or "") if isinstance(op.get(kind), dict) else ""
                if sql.strip():
                    params = {k: 0 for k in (s.get("takes") or {})}
                    try:
                        sql_engines.parse(sql_engines.bind(sql, params, sql_engines.DIALECT[kind]), sql_engines.DIALECT[kind])
                    except sql_engines.QueryRefused as exc:
                        errors.append({"path": f"{path}.{i}.operation.{kind}.sql", "message": str(exc)})
            walk(s.get("steps") or [], f"{path}.{i}.steps")
    walk(raw.get("steps") or [], "steps")
    return errors


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
        for j, r in enumerate(s.get("rules_first") or []):
            sites.append((f"{path}.rules_first.{j}.when", r.get("when", "")))
        if s.get("pre_select"):
            sites.append((f"{path}.pre_select", s["pre_select"]))
        for j, op in enumerate((s.get("operation") or {}).get("cel") or []):
            if not isinstance(op, dict) or len(op) != 1:
                continue
            (kind, c), = op.items()
            base = f"{path}.operation.cel.{j}.{kind}"
            if kind == "keep" and isinstance(c, str):
                sites.append((base, c))
            elif kind == "add_fields" and isinstance(c, dict):
                sites.extend((f"{base}.{n}", e) for n, e in c.items() if isinstance(e, str))
            elif kind == "check" and isinstance(c, list):
                sites.extend((f"{base}.{k}.rule", x.get("rule", "")) for k, x in enumerate(c) if isinstance(x, dict))
            elif isinstance(c, dict):
                for key in ("key", "keep_highest", "by", "other_key", "together"):
                    if c.get(key):
                        sites.append((f"{base}.{key}", c[key]))
                for n, e in (c.get("group_by") or {}).items():
                    sites.append((f"{base}.group_by.{n}", e))
                for n, e in (c.get("totals") or {}).items():
                    m = re.match(r"^\s*(?:count|sum|avg|min|max)\s*\((.*)\)\s*$", e or "", re.S)
                    if m and m.group(1).strip():
                        sites.append((f"{base}.totals.{n}", m.group(1)))
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
    if (raw.get("trigger") or {}).get("when"):
        sites.append(("trigger.when", raw["trigger"]["when"]))
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


def _flat_values(values: Any) -> list[str]:
    return [x for v in values for x in (v if isinstance(v, list) else [v])]


def _policy(agent: definition.Agent) -> list[dict[str, str]]:
    """Human approval is available, never required: whether a person checks before an Act step is the builder's call.
    Where one might matter, the Act step gets a suggestion on its own panel (not a warning): it emails text a model
    wrote, or it acts on values from content other people wrote, with no Approve step before it."""
    warnings = []
    reads_untrusted = any(getattr(s, "uses", None) and agent.connections[s.uses.connection].service in UNTRUSTED
                          and not isinstance(s, ActStep) for s in agent.all_steps())     # sending email reads nothing
    approved = False
    for i, s in enumerate(agent.steps):
        if isinstance(s, ApproveStep):
            approved = True
        if isinstance(s, ActStep) and s.send_email is not None and not approved:
            model_steps = {x.id for x in agent.all_steps() if isinstance(x, (AskStep, FreeFormBlock))
                           or (isinstance(x, BranchBlock) and x.decide == "model")}
            refs = [v for v in _flat_values(s.takes.values()) + [s.send_email.get("for_each") or ""]]
            if any(r.rstrip("?").split(".")[0] in model_steps for r in refs if r):
                warnings.append({"path": f"steps.{i}", "message": "This email includes text a model wrote. If a person should read it "
                                 "before it goes out, add an Approve step before this one."})
                continue
        if isinstance(s, ActStep) and reads_untrusted and not approved:
            warnings.append({"path": f"steps.{i}", "message": "This acts on values from content other people wrote (email, GitHub, "
                             "SharePoint, MCP tools). If a person should check them first, add an Approve step before this one."})
    return warnings


def check(raw: dict[str, Any], accounts: dict[str, dict[str, Any]] | None = None,
          connectors: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
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
        return {"ok": False, "errors": errors, "warnings": [], "suggestions": [], "compiled": None}
    suggestions = _policy(agent)
    errors += _run_option_refs(raw)
    errors += _sql_problems(raw)
    if accounts is not None:
        errors += check_accounts(raw, accounts, connectors)
    compiled = None
    try:
        compiled = compile_agent(agent).all_yaml()
    except CompileError as exc:
        if not str(exc).startswith("Unknown run option"):      # that one is already pinned to its field above
            errors.append({"path": "", "message": str(exc)})
    warnings: list[dict[str, str]] = []
    for s in agent.all_steps():
        if isinstance(s, FreeFormBlock) and not s.before_finishing:
            warnings.append({"path": "", "message": f"{s.name} has no Before finishing rules: the planner decides alone when it's done."})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "suggestions": suggestions, "compiled": compiled}


# ------------------------------------------------------------------ references for the pickers

def _type_of_returns(step: Any) -> list[tuple[str, str]]:
    if isinstance(step, AskStep):
        return [(n, f.type) for n, f in step.returns.items()]
    if isinstance(step, BuiltInStep) and step.op == "javascript":
        return [(n, f.type) for n, f in step.returns.items()]
    if isinstance(step, BuiltInStep) and step.op == "chart":
        return [("image", "chart"), ("title", "text")]
    if isinstance(step, BuiltInStep) and step.op in ("gcs-list", "sharepoint-list"):
        return [("files", "list of files"), ("count", "number")]
    if isinstance(step, BuiltInStep) and step.op == "sharepoint-items":
        rows = step.returns.get("rows")
        return [("rows", rows.type if rows else "list of records"), ("row_count", "number"), ("truncated", "yes/no")]
    if isinstance(step, BuiltInStep) and step.op in ("gcs-read", "sharepoint-read"):
        rows = step.returns.get("rows")
        return [("rows", rows.type if rows else "list of records"), ("row_count", "number"), ("truncated", "yes/no"),
                ("files", "list of files"), ("text", "text")]
    if isinstance(step, BuiltInStep) and step.op == "cel":
        declared = step.returns.get("items")
        out = [("items", declared.type if declared else "list of records"), ("notes", "list of text")]
        for o in step.operation.get("cel") or []:
            c = (o or {}).get("summarize") if isinstance(o, dict) else None
            if c and c.get("save_as"):
                out.append((c["save_as"], "list of records" if c.get("group_by") else "record"))
        return out
    if isinstance(step, BuiltInStep) and step.op in ("trino", "spark-sql"):
        rows = step.returns.get("rows")
        return [("rows", rows.type if rows else "list of records"), ("row_count", "number"), ("truncated", "yes/no"),
                ("elapsed_seconds", "number")]
    if isinstance(step, BuiltInStep) and step.op == "bigquery":
        rows = step.returns.get("rows")
        return [("rows", rows.type if rows else "list of records"), ("row_count", "number"), ("truncated", "yes/no"),
                ("bytes_billed", "number"), ("cost_usd", "number")]
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
    if agent.trigger.kind == "pubsub":
        refs += [{"ref": "trigger.message_id", "label": "Trigger › the Pub/Sub message", "type": "text"},
                 {"ref": "trigger.published_at", "label": "Trigger › when it was published", "type": "date & time with time zone"}]
        refs += [{"ref": f"trigger.{n}", "label": f"Trigger › message › {n}", "type": fd.type} for n, fd in (agent.trigger.message or {}).items()]
    if agent.trigger.kind == "email":
        refs += [{"ref": "trigger.email_id", "label": "Trigger › the email", "type": "text"},
                 {"ref": "trigger.sender_domain", "label": "Trigger › its sender's domain", "type": "text"}]
    for s in agent.steps:
        if s.id == step_id:
            if isinstance(s, BranchBlock) and s.for_each:
                refs += _item_refs(agent, s, refs)
            break
        if isinstance(s, ParallelBlock):
            if any(i.id == step_id for i in s.steps):    # inside: the item, and the block's steps before this one
                if s.for_each:
                    refs += _item_refs(agent, s, refs)
                    for i in s.steps:
                        if i.id == step_id:
                            break
                        refs += _step_refs(i)
                break
            if s.for_each:
                each = s.for_each.as_
                refs += [{"ref": f"{s.id}.results", "label": f"{s.name} › every {each}'s results", "type": "list of results"},
                         {"ref": f"{s.id}.count", "label": f"{s.name} › how many {each}s", "type": "number"}]
                for b in (i for i in s.steps if isinstance(i, BranchBlock) and i.decide == "model"):
                    refs += [{"ref": f"{s.id}.{b.id}.decisions", "label": f"{s.name} › {b.name} › every {each}'s decision", "type": "list of decisions"},
                             {"ref": f"{s.id}.{b.id}.counts", "label": f"{s.name} › {b.name} › how many took each path", "type": "record"}]
                    refs += [{"ref": f"{s.id}.{b.id}.by_path.{_slug(p.name)}", "label": f"{s.name} › {b.name} › the {each}s that took {p.name}",
                              "type": "list of decisions"} for p in b.paths]
            else:
                for i in s.steps:
                    refs += _step_refs(i)
            continue
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
        else:
            refs += _step_refs(s)
    return refs


def _step_refs(s: Any) -> list[dict[str, str]]:
    """What one step hands on, for the steps after it."""
    if isinstance(s, ApproveStep):
        return [{"ref": f"{s.id}.approved", "label": f"{s.name} › approved items", "type": "list"}]
    if isinstance(s, BranchBlock) and s.decide == "model" and s.for_each:
        return ([{"ref": f"{s.id}.decisions", "label": f"{s.name} › every {s.for_each.as_}'s decision", "type": "list of decisions"},
                 {"ref": f"{s.id}.counts", "label": f"{s.name} › how many took each path", "type": "record"}]
                + [{"ref": f"{s.id}.by_path.{_slug(p.name)}", "label": f"{s.name} › the {s.for_each.as_}s that took {p.name}",
                    "type": "list of decisions"} for p in s.paths])
    if isinstance(s, BranchBlock) and s.decide == "model":
        return [{"ref": f"{s.id}.path", "label": f"{s.name} › the path chosen", "type": "text"},
                {"ref": f"{s.id}.reason", "label": f"{s.name} › why", "type": "text"}]
    return [{"ref": f"{s.id}.{n}", "label": f"{s.name} › {n}", "type": t} for n, t in _type_of_returns(s)]


def _item_refs(agent: definition.Agent, step: Any, refs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Inside a block that runs for each item: the item, and its fields when the list's record type is known."""
    name = step.for_each.as_
    listed = next((r for r in refs if r["ref"] == step.for_each.over.rstrip("?")), None)
    kind = (listed or {}).get("type", "")
    record = agent.records.get(kind.removeprefix("list of ")) if kind.startswith("list of ") else None
    out = [{"ref": name, "label": f"Each {name}", "type": kind.removeprefix("list of ") or "value"}]
    out += [{"ref": f"{name}.{f}", "label": f"Each {name} › {f}", "type": fd.type} for f, fd in (record.fields.items() if record else [])]
    return out


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
