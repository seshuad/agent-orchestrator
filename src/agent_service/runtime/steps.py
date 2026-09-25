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
                text, error = await gateway.mcp_call(conn, session, usable, tool, args)
                if error:
                    raise gateway.Refused(f"{tool} failed: {text[:300]}")
                done.append({"tool": tool, "arguments": args, "result": text[:500]})
        return done

    return {"called": asyncio.run(run()), "would_call": []}


# ------------------------------------------------------------------ debugging

def show(data: dict) -> dict:
    """Show a value in the run's log. Changes nothing; the value is passed on as `value`."""
    return {"value": data.get("value")}


# ------------------------------------------------------------------ command line

def main() -> None:
    p = argparse.ArgumentParser(prog="agent-service-steps")
    p.add_argument("operation", choices=["tidy", "lookup", "filter-rows", "compare", "three-way-match", "create-events", "add-rows", "show", "call-tools"])
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
    p.add_argument("--arguments", help="call-tools: argument -> template over each record, as JSON.")
    a = p.parse_args()
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
    elif a.operation == "call-tools":
        try:
            out = call_tools(a.tool, json.loads(a.arguments or "{}"), a.dry_run == "true", data)
        except gateway.Refused as exc:
            sys.exit(f"Refused: {exc}")
    else:
        out = create_events(a.calendar, json.loads(a.templates), [f for f in a.match_fields.split(",") if f],
                            a.dry_run == "true", data)
    record_step(a.step, out)
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
