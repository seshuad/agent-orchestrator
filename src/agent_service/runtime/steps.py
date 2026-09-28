"""The Built-in step library: fixed operations with no model, run as Conductor script steps.

    agent-service-steps <operation> --step <step name> [options]   input on stdin, JSON on stdout

Operations
    tidy              a list of records through an ordered pipeline: check, remove_duplicates,
                      filter, group, flag. Every condition is CEL.
    lookup            the first row of a sheet whose column matches any of the given values
    filter-rows       every row of a sheet whose column equals a value
    compare           two values: match, changed, missing, or nothing on file
    three-way-match   an invoice against its purchase order and delivery receipts
    create-events     calendar events from records, never twice, following dry run
    add-rows          one sheet row per record, following dry run
    show              a value, written to the run's log for debugging; passed on unchanged

Each run's output is recorded under the step's name, which is what the gateway checks
run-dependent limits against ("only emails cited by tidy_up").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

from . import gateway
from .cel import Rule, to_cel, to_python
from .runstate import record_step


def _norm(v: Any) -> str:
    return re.sub(r"[\s\-]", "", str(v)).upper()


# ------------------------------------------------------------------ tidy

def _check(records: list[dict], op: dict, notes: list[str]) -> list[dict]:
    required = op.get("required", [])
    ts = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)$")
    kept = []
    for r in records:
        missing = [f for f in required if r.get(f) in (None, "", [])]
        bad_ts = [f for f in op.get("timestamps", []) if not (isinstance(r.get(f), str) and ts.match(r[f]))]
        if missing or bad_ts:
            notes.append(f"Dropped a {op.get('record', 'record')} from {r.get('source_email', 'an email')}: "
                         + ", ".join([f"{f} missing" for f in missing] + [f"{f} has no time zone" for f in bad_ts]))
        else:
            kept.append(r)
    return kept


def _remove_duplicates(records: list[dict], op: dict, notes: list[str]) -> list[dict]:
    identity, rank = Rule("identity", op["identity"]), Rule("keep_highest", op["keep_highest"])
    best: dict[str, tuple[Any, dict]] = {}
    for r in records:
        act = {"b": to_cel(r, timestamps=True)}
        key, score = to_python(identity.evaluate_cel(act)), to_python(rank.evaluate_cel(act))
        if key not in best or score > best[key][0]:
            best[key] = (score, {**r, "key": key})
    if len(best) < len(records):
        notes.append(f"Removed {len(records) - len(best)} duplicate(s).")
    return [r for _, r in best.values()]


def _filter(records: list[dict], op: dict, run: dict, notes: list[str]) -> list[dict]:
    keep = Rule("keep", op["keep"])
    kept = [r for r in records if to_python(keep.evaluate_cel({"b": to_cel(r, True), "run": to_cel(run, True)}))]
    if len(kept) < len(records):
        notes.append(f"Left out {len(records) - len(kept)} record(s) that don't match: {op['keep']}")
    return kept


def _group(records: list[dict], op: dict) -> list[dict]:
    """Records join a group when the `together` rule holds for any pair (a starts before b)."""
    together = Rule("together", op["together"])
    records = sorted(records, key=lambda r: r["start"])
    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cel = [to_cel(r, True) for r in records]
    for j in range(len(records)):
        for i in range(j):
            if find(i) != find(j) and to_python(together.evaluate_cel({"a": cel[i], "b": cel[j]})):
                parent[find(j)] = find(i)
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(records):
        groups.setdefault(find(i), []).append(r)
    trips = []
    for members in groups.values():
        flights = [b for b in members if b.get("type") == "flight"]
        trips.append({
            # From the first booking's stable key, not the destination text a model wrote.
            "trip_id": hashlib.sha1(min(b["key"] for b in members).encode()).hexdigest()[:10],
            "destination": (flights or members)[0].get("destination"),
            "start": min(b["start"] for b in members),
            "end": max(b["end"] for b in members),
            "bookings": members,
            "flags": [],
        })
    return sorted(trips, key=lambda t: t["start"])


def _flag(trips: list[dict], op: dict, run: dict) -> list[dict]:
    rules = [(Rule(f"flag {i + 1}", r["when"]), r["note"]) for i, r in enumerate(op["rules"])]
    for t in trips:
        for rule, note in rules:
            if to_python(rule.evaluate_cel({"trip": to_cel(t, True), "run": to_cel(run, True)})):
                t["flags"].append(note)
    return trips


def tidy(operations: list[dict], data: dict) -> dict:
    run = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), **data.get("run", {})}
    items, notes, grouped = list(data["records"]), [], False
    for op in operations:
        kind = op["op"]
        if kind == "check":
            items = _check(items, op, notes)
        elif kind == "remove_duplicates":
            items = _remove_duplicates(items, op, notes)
        elif kind == "filter":
            items = _filter(items, op, run, notes)
        elif kind == "group":
            items, grouped = _group(items, op), True
        elif kind == "flag":
            if not grouped:
                raise ValueError("flag works on groups; put it after group.")
            items = _flag(items, op, run)
        else:
            raise ValueError(f"Unknown operation {kind!r}.")
    return {"trips" if grouped else "records": items, "notes": notes}


# ------------------------------------------------------------------ sheets

def _rows(sheet: str) -> list[dict]:
    return gateway.call(gateway.connect("google-sheets"), "google-sheets", "read", {"sheet": sheet})


def _flatten(values: Any) -> list[Any]:
    if isinstance(values, list):
        return [x for v in values for x in _flatten(v)]
    return [values]


def lookup(sheet: str, column: str, as_: str, data: dict) -> dict:
    wanted = {_norm(v) for v in _flatten(data.get("any_of", [])) if v not in (None, "")}
    row = next((r for r in _rows(sheet) if _norm(r.get(column, "")) in wanted), None)
    return {"found": row is not None, as_: row}


def filter_rows(sheet: str, column: str, as_: str, data: dict) -> dict:
    return {as_: [r for r in _rows(sheet) if _norm(r.get(column, "")) == _norm(data.get("equals", ""))]}


def compare(data: dict) -> dict:
    left, right = data.get("value"), data.get("on_file")
    if right in (None, ""):
        status = "nothing on file"
    elif left in (None, ""):
        status = "missing"
    else:
        status = "match" if _norm(left) == _norm(right) else "changed"
    return {"status": status}


def three_way_match(data: dict) -> dict:
    """Prices against the purchase order; quantities against the order and, for goods, what was received."""
    inv, po, receipts = data["invoice"], data["purchase_order"], data.get("receipts")
    po_lines = {_norm(l["description"]): l for l in po.get("lines", [])}
    received: dict[str, float] = {}
    for r in receipts or []:
        received[_norm(r["description"])] = received.get(_norm(r["description"]), 0) + r["quantity"]
    diffs = []
    for line in inv.get("lines", []):
        d = _norm(line["description"])
        ordered = po_lines.get(d)
        if ordered is None:
            diffs.append(f"{line['description']}: not on {po['po_number']}")
            continue
        if abs(line["unit_price"] - ordered["unit_price"]) > 0.005:
            diffs.append(f"{line['description']}: invoiced at {line['unit_price']:.2f}, ordered at {ordered['unit_price']:.2f}")
        if line["quantity"] > ordered["quantity"]:
            diffs.append(f"{line['description']}: invoiced {line['quantity']:g}, ordered {ordered['quantity']:g}")
        if not po.get("services") and line["quantity"] > received.get(d, 0):
            diffs.append(f"{line['description']}: invoiced {line['quantity']:g}, received {received.get(d, 0):g}")
    return {"passed": not diffs, "differences": diffs}


# ------------------------------------------------------------------ calendar

def _fill(template: str, record: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(record.get(m.group(1)) or ""), template).strip()


def create_events(calendar: str, templates: dict, match_fields: list[str], dry_run: bool, data: dict) -> dict:
    cal = gateway.connect("google-calendar")
    existing = gateway.call(cal, "google-calendar", "list_events", {"calendar": calendar})
    created, skipped, would = [], [], []
    for b in data.get("records", []):
        tpl = templates.get(b.get("type")) or templates.get("default")
        if tpl is None:
            skipped.append(f"{b.get('key')}: no event template for {b.get('type')}")
            continue
        event = {"title": _fill(tpl["title"], b), "start": _fill(tpl["starts"], b), "end": _fill(tpl["ends"], b),
                 "all_day": tpl.get("all_day", False), "details": _fill(tpl.get("details", ""), b), "agent_key": b.get("key")}
        # Never add twice: once for what this agent added, once for what's already on the calendar.
        # Only records with a stable key (from a remove-duplicates step) can be matched to what this agent added.
        if b.get("key") and any(e.get("agent_key") == b.get("key") for e in existing):
            skipped.append(f"{event['title']}: already added by this agent")
            continue
        clues = [str(b[f]) for f in match_fields if b.get(f)]
        if any(e["start"][:10] == event["start"][:10] and any(c in e["title"] for c in clues) for e in existing):
            skipped.append(f"{event['title']}: the calendar already has a matching event")
            continue
        if dry_run:
            would.append(f"{event['title']} ({event['start']} to {event['end']})")
        else:
            gateway.call(cal, "google-calendar", "create_event", {"calendar": calendar, "event": event})
            existing.append(event)
            created.append(event["title"])
    return {"created": created, "skipped": skipped, "would_create": would}


# ------------------------------------------------------------------ sheets: one row per record

def _fill_value(template: str, record: dict) -> Any:
    """A column's value from a record: "{field}" alone keeps the field's own value (a number stays a number)."""
    whole = re.fullmatch(r"\{(\w+)\}", template.strip())
    if whole:
        return record.get(whole.group(1))
    return _fill(template, record)


def add_rows(sheet: str, row: dict, dry_run: bool, data: dict) -> dict:
    conn = gateway.connect("google-sheets")
    added, would = [], []
    for record in data.get("records", []):
        values = {col: _fill_value(tpl, record) for col, tpl in row.items()}
        result = gateway.call(conn, "google-sheets", "append_row", {"sheet": sheet, "row": values, "dry_run": dry_run})
        (would if dry_run else added).append(result.get("would_add") or result.get("row"))
    return {"added": added, "would_add": would}


def call_tools(tool: str, arguments: dict, dry_run: bool, data: dict) -> dict:
    """An MCP connector's act tool, once per record, through the gateway's checks. A dry run lists the calls it would make.
    A refused or failed call stops the step: nothing after it is called."""
    import asyncio
    conn = gateway.connect("mcp")
    calls = [{arg: _fill_value(tpl, record) if isinstance(tpl, str) else tpl for arg, tpl in arguments.items()}
             for record in data.get("records", [])]
    if dry_run:
        return {"called": [], "would_call": [{"tool": tool, "arguments": c} for c in calls]}

    async def run() -> list[dict]:
        from . import upstream
        done = []
        async with conn.session() as session:
            listed = {t.name: upstream.tool_dict(t) for t in (await session.list_tools()).tools}
            usable = conn.usable(listed)
            for args in calls:
                try:
                    text, error = await asyncio.wait_for(gateway.mcp_call(conn, session, usable, tool, args), gateway.TOOL_TIMEOUT)
                except asyncio.TimeoutError:
                    raise gateway.Refused(f"{tool} got no answer in {int(gateway.TOOL_TIMEOUT)} seconds; later items weren't sent") from None
                if error:
                    raise gateway.Refused(f"{tool} failed: {text[:300]}")
                done.append({"tool": tool, "arguments": args, "result": text[:500]})
        return done

    return {"called": asyncio.run(run()), "would_call": []}


# ------------------------------------------------------------------ BigQuery

def bigquery(sql: str, data: dict) -> dict:
    """A fixed query, with the step's Takes as @parameters, through the gateway's checks."""
    conn = gateway.connect("bigquery")
    return gateway.call(conn, "bigquery", "query", {"sql": sql, "params": {k: v for k, v in data.items() if v is not None}})


def insert_rows(table: str, row: dict, dry_run: bool, data: dict) -> dict:
    conn = gateway.connect("bigquery")
    rows = [{col: _fill_value(tpl, rec) if isinstance(tpl, str) else tpl for col, tpl in row.items()} for rec in data.get("records", [])]
    return gateway.call(conn, "bigquery", "insert_rows", {"table": table, "rows": rows, "dry_run": dry_run})


# ------------------------------------------------------------------ JavaScript

JS_TIME_LIMIT = 2                 # seconds of JavaScript per step run
JS_MEMORY_LIMIT = 64 * 1024 * 1024
JS_OUTPUT_LIMIT = 2_000_000       # characters of JSON a step may return


class ScriptError(Exception):
    """The code threw, ran too long, or returned something the step doesn't declare. The message says which."""


def javascript(code: str, returns: list[str], data: dict) -> dict:
    """Runs the builder's code in QuickJS: a function body that gets `inputs` (the step's Takes) and returns an object
    with the fields in Returns. No files, network or processes: only its inputs, JSON in and JSON out, time and
    memory limited. Date is available; nothing else from outside."""
    import quickjs
    wrapper = ("function __main(json) {\n  const inputs = JSON.parse(json);\n"
               "  const out = (function (inputs) {\n" + code + "\n  })(inputs);\n"
               "  return JSON.stringify(out === undefined ? null : out);\n}")
    try:
        fn = quickjs.Function("__main", wrapper)
    except quickjs.JSException as exc:
        raise ScriptError(f"The JavaScript doesn't parse: {_js_message(exc)}") from None
    fn.set_time_limit(JS_TIME_LIMIT)
    fn.set_memory_limit(JS_MEMORY_LIMIT)
    try:
        text = fn(json.dumps(data, default=str))
    except quickjs.JSException as exc:
        msg = _js_message(exc)
        if "interrupted" in msg:
            raise ScriptError(f"The JavaScript ran for more than {JS_TIME_LIMIT} seconds and was stopped.") from None
        if "out of memory" in msg.lower():
            raise ScriptError(f"The JavaScript used more than {JS_MEMORY_LIMIT // (1024 * 1024)} MB and was stopped.") from None
        raise ScriptError(f"The JavaScript threw an error: {msg}") from None
    if text is None or len(text) > JS_OUTPUT_LIMIT:
        raise ScriptError("The JavaScript returned nothing." if text is None else "The JavaScript returned more than 2 MB.")
    out = json.loads(text)
    if not isinstance(out, dict):
        raise ScriptError(f"The JavaScript must return an object with {', '.join(returns) or 'its fields'}, like "
                          f"return {{ {returns[0] if returns else 'result'}: ... }}; it returned {type(out).__name__}.")
    missing = [r for r in returns if r not in out]
    if missing:
        raise ScriptError(f"The JavaScript's result has no {', '.join(missing)}. Return every field listed under Returns.")
    return {k: out[k] for k in returns} if returns else out


def _js_message(exc: Exception) -> str:
    lines = [l for l in str(exc).splitlines() if l.strip()]
    # QuickJS reports line numbers in the wrapper; the builder's code starts 4 lines in.
    return re.sub(r"<input>:(\d+)", lambda m: f"line {max(1, int(m.group(1)) - 3)}", " ".join(lines)).replace("at __main", "").strip()


# ------------------------------------------------------------------ deciding for each item

LABEL_FIELDS = ("title", "name", "subject", "label", "id", "number")


def _label(item: Any, index: int) -> str:
    """A short name for an item in the run log and memory: its number and title, or whatever names it."""
    if isinstance(item, dict):
        num = item.get("number") if item.get("number") not in (None, "") else item.get("id")
        text = next((str(item[f]) for f in ("title", "name", "subject", "label") if item.get(f) not in (None, "")), "")
        if num not in (None, "") or text:
            return (f"#{num} " if num not in (None, "") else "") + text[:80]
    if isinstance(item, (str, int, float)) and not isinstance(item, bool):
        return str(item)[:80]
    return f"Item {index + 1}"


def _at(value: Any, path: str) -> Any:
    for seg in [p for p in path.split(".") if p]:
        value = value.get(seg) if isinstance(value, dict) else None
    return value


def decide_prep(data: dict, item_keys: dict[str, str], for_step: str, memory: bool, max_cases: int) -> dict:
    """The items a Branch decides for, one by one: each with a key, a label, the fields memory matches on, and the
    confirmed past cases most like it."""
    items = data.get("items")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ScriptError(f"Deciding for each item needs a list, but got {type(items).__name__}.")
    from .memory import recall
    out = []
    for i, item in enumerate(items):
        keys = {**(data.get("fixed") or {}), **{k: _at(item, path) for k, path in item_keys.items()}}
        rec = recall(for_step, keys, max_cases) if memory else {"text": "", "cases": []}
        out.append({"key": str(i), "item": item, "label": _label(item, i), "keys": keys,
                    "memory": rec["text"], "recalled": len(rec["cases"])})
    return {"items": out, "count": len(out), "recalled": sum(x["recalled"] for x in out)}


def decide_collect(data: dict, paths: list[str]) -> dict:
    """Every item's decision, in the list's order. An item whose decision failed, or isn't one of the paths, takes the
    last path (the safe default) and says so."""
    outputs = data.get("outputs") or {}
    decisions, counts = [], {p: 0 for p in paths}
    for it in data.get("items") or []:
        out = outputs.get(it["key"]) if isinstance(outputs, dict) else None
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except json.JSONDecodeError:
                out = None
        out = out if isinstance(out, dict) else {}
        decided = out.get("path") in paths
        path = out["path"] if decided else paths[-1]
        reason = out.get("reason") if decided else ("It couldn't decide this one, so it took the safe default." if not out
                                                    else f"Its answer {out.get('path')!r} isn't one of the paths, so it took the safe default.")
        counts[path] += 1
        confidence = out.get("confidence") if decided and out.get("confidence") in ("sure", "leaning", "unsure") else "unsure"
        runner_up = out.get("runner_up") if out.get("runner_up") in paths and out.get("runner_up") != path else ""
        decisions.append({"label": it.get("label"), "item": it.get("item"), "path": path, "reason": reason or "",
                          "evidence": out.get("evidence") or [], "keys": it.get("keys") or {}, "decided": decided,
                          "confidence": confidence if out.get("confidence") or not decided else "sure", "runner_up": runner_up})
    slug = lambda name: re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    by_path = {slug(p): [d for d in decisions if d["path"] == p] for p in paths}    # for steps that follow one path
    return {"decisions": decisions, "counts": counts, "by_path": by_path, "undecided": sum(not d["decided"] for d in decisions)}


def _parsed(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def each_collect(data: dict, steps: list[str], paths: dict[str, list[str]]) -> dict:
    """After a Parallel block ran its steps for each item: every item's results (one field per step, empty for a step
    that didn't run for it), and for each model-decided Branch inside, its decisions, counts and items by path. An item
    that failed takes each Branch's last path (the safe default) and says so."""
    outputs = data.get("outputs") or {}
    slug = lambda name: re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    decided = {b: {"decisions": [], "counts": {p: 0 for p in ps}, "by_path": {slug(p): [] for p in ps}, "undecided": 0}
               for b, ps in paths.items()}
    results, failed = [], 0
    for it in data.get("items") or []:
        out = _parsed(outputs.get(it["key"]) if isinstance(outputs, dict) else None)
        ok = isinstance(out, dict)
        failed += not ok
        row = {"label": it.get("label"), "item": it.get("item"), "ran": ok,
               **{sid: _parsed(out.get(sid)) if ok else None for sid in steps}}
        results.append(row)
        for b, ps in paths.items():
            d = row.get(b)
            if ok and d is None:
                continue                                    # a path before it ended this item, or went round it
            d = d if isinstance(d, dict) else {}
            good = d.get("path") in ps
            path = d["path"] if good else ps[-1]
            confidence = d.get("confidence") if good and d.get("confidence") in ("sure", "leaning", "unsure") else "sure" if good else "unsure"
            recall = _parsed(out.get(f"{b}_recall")) if ok else None
            dec = {"label": it.get("label"), "item": it.get("item"), "path": path, "decided": good,
                   "reason": d.get("reason") if good else ("It couldn't decide this one, so it took the safe default." if not d
                                                           else f"Its answer {d.get('path')!r} isn't one of the paths, so it took the safe default."),
                   "evidence": d.get("evidence") or [], "keys": (recall or {}).get("keys") or {} if isinstance(recall, dict) else {},
                   "confidence": confidence, "runner_up": d.get("runner_up") if d.get("runner_up") in ps and d.get("runner_up") != path else "",
                   "index": len(results) - 1}
            decided[b]["decisions"].append(dec)
            decided[b]["counts"][path] += 1
            decided[b]["by_path"][slug(path)].append(dec)
            decided[b]["undecided"] += not good
    return {"results": results, "count": len(results), "failed": failed, **decided}


# ------------------------------------------------------------------ debugging

def show(data: dict) -> dict:
    """Show a value in the run's log. Changes nothing; the value is passed on as `value`."""
    return {"value": data.get("value")}


# ------------------------------------------------------------------ command line

def main() -> None:
    p = argparse.ArgumentParser(prog="agent-service-steps")
    p.add_argument("operation", choices=["tidy", "lookup", "filter-rows", "compare", "three-way-match", "create-events", "add-rows", "show", "call-tools", "javascript", "bigquery", "insert-rows", "memory-recall", "decide-prep", "decide-collect", "each-collect"])
    p.add_argument("--step", required=True, help="The step's name in the workflow; its output is recorded under it.")
    p.add_argument("--operations", help="tidy: the operations, as JSON.")
    p.add_argument("--sheet")
    p.add_argument("--column")
    p.add_argument("--as", dest="as_", help="The output field the result goes in.")
    p.add_argument("--calendar")
    p.add_argument("--templates", help="create-events: event templates by record type, as JSON.")
    p.add_argument("--match-fields", default="", help="create-events: fields that identify an existing event.")
    p.add_argument("--dry-run", choices=["true", "false"], default="true")
    p.add_argument("--row", help="add-rows: column -> template over each record, as JSON.")
    p.add_argument("--tool", help="call-tools: the MCP connector's act tool.")
    p.add_argument("--code-b64", help="javascript: the function body, base64.")
    p.add_argument("--sql-b64", help="bigquery: the query, base64.")
    p.add_argument("--max-cases", type=int, default=5, help="memory-recall: how many past cases.")
    p.add_argument("--for-step", help="memory-recall: the step whose past cases to recall.")
    p.add_argument("--table", help="insert-rows: the BigQuery table.")
    p.add_argument("--item-keys", default="{}", help="decide-prep: memory field -> path inside each item, as JSON.")
    p.add_argument("--memory", action="store_true", help="decide-prep: recall past cases for each item.")
    p.add_argument("--paths", help="decide-collect: the Branch's path names, in order; each-collect: each model Branch's, as JSON.")
    p.add_argument("--steps", help="each-collect: the block's step ids, as JSON.")
    p.add_argument("--returns", default="", help="javascript: the fields it returns, comma-separated.")
    p.add_argument("--arguments", help="call-tools: argument -> template over each record, as JSON.")
    a = p.parse_args()
    import os
    os.environ.setdefault("AGENT_SERVICE_STEP", a.step)      # connection calls are logged against this step
    data = json.loads(sys.stdin.read() or "{}")

    if a.operation == "tidy":
        out = tidy(json.loads(a.operations), data)
    elif a.operation == "lookup":
        out = lookup(a.sheet, a.column, a.as_, data)
    elif a.operation == "filter-rows":
        out = filter_rows(a.sheet, a.column, a.as_, data)
    elif a.operation == "compare":
        out = compare(data)
    elif a.operation == "three-way-match":
        out = three_way_match(data)
    elif a.operation == "show":
        out = show(data)
    elif a.operation == "add-rows":
        out = add_rows(a.sheet, json.loads(a.row), a.dry_run == "true", data)
    elif a.operation == "each-collect":
        out = each_collect(data, json.loads(a.steps), json.loads(a.paths or "{}"))
    elif a.operation in ("decide-prep", "decide-collect"):
        try:
            out = (decide_prep(data, json.loads(a.item_keys), a.for_step, a.memory, a.max_cases) if a.operation == "decide-prep"
                   else decide_collect(data, json.loads(a.paths)))
        except ScriptError as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            sys.exit(str(exc))
    elif a.operation == "memory-recall":
        from .memory import recall
        out = recall(a.for_step, data, a.max_cases)
    elif a.operation in ("bigquery", "insert-rows"):
        import base64
        try:
            out = (bigquery(base64.b64decode(a.sql_b64).decode(), data) if a.operation == "bigquery"
                   else insert_rows(a.table, json.loads(a.row or "{}"), a.dry_run == "true", data))
        except gateway.Refused as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            sys.exit(f"Refused: {exc}")
        except Exception as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            sys.exit(f"BigQuery failed: {str(exc).splitlines()[0][:400]}")
    elif a.operation == "javascript":
        import base64
        try:
            out = javascript(base64.b64decode(a.code_b64).decode(), [r for r in a.returns.split(",") if r], data)
        except ScriptError as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            sys.exit(str(exc))
    elif a.operation == "call-tools":
        try:
            out = call_tools(a.tool, json.loads(a.arguments or "{}"), a.dry_run == "true", data)
        except gateway.Refused as exc:
            sys.exit(f"Refused: {exc}")
    else:
        out = create_events(a.calendar, json.loads(a.templates), [f for f in a.match_fields.split(",") if f],
                            a.dry_run == "true", data)
    record_step(a.step, out, inputs=data)
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
