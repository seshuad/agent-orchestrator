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
run-dependent limits against ("only emails cited by group_trips").
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
from .runstate import record_step, run_dir


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


# ------------------------------------------------------------------ CEL operators: the operator iterates, CEL judges one item

AGGREGATES = re.compile(r"^\s*(count|sum|avg|min|max)\s*\((.*)\)\s*$", re.S)
CEL_OPS = ("keep", "add_fields", "check", "remove_duplicates", "sort", "summarize", "match", "link")


def cel_pipeline(ops: list[dict], data: dict) -> dict:
    """Run the CEL operators in order over `items` (the step's list), each one's result feeding the next.

    Every expression sees `item` (or `a` and `b`, for Link), `run`, the step's other inputs by name, and anything an
    earlier Summarize saved. Numbers mix freely (ints and doubles). Returns the items, notes on what each operator
    dropped or changed, and every saved value."""
    run = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), **(data.get("run") or {})}
    inputs = {k: v for k, v in data.items() if k not in ("items", "run")}
    items = [dict(x) if isinstance(x, dict) else {"value": x} for x in (data.get("items") or [])]
    notes: list[str] = []
    saved: dict[str, Any] = {}

    def rule(where: str, source: str) -> Rule:
        try:
            return Rule(where, source)
        except Exception as exc:
            raise ScriptError(str(exc)) from None

    def value(r: Rule, **names: Any) -> Any:
        act = {"run": run, **inputs, **saved, **names}
        try:
            return to_python(r.evaluate_cel({k: to_cel(v, timestamps=True) for k, v in act.items()}))
        except Exception as exc:
            raise ScriptError(f"{r.name}: {exc}") from None

    for n, op in enumerate(ops, 1):
        (kind, conf), = op.items()
        where = f"{n}. {kind.replace('_', ' ')}"
        if kind == "keep":
            r = rule(where, conf)
            kept = [x for x in items if value(r, item=x) is True]
            if len(kept) < len(items):
                notes.append(f"Keep: left out {len(items) - len(kept)} of {len(items)} (not {conf}).")
            items = kept
        elif kind == "add_fields":
            rules = [(name, rule(f"{where}: {name}", src)) for name, src in conf.items()]
            for x in items:
                for name, r in rules:                     # in order: a later field can use an earlier one
                    x[name] = value(r, item=x)
        elif kind == "check":
            for c in conf:
                r = rule(f"{where}: {c.get('name') or c['rule']}", c["rule"])
                message, on_fail = c.get("message") or c["rule"], c.get("on_fail", "drop")
                if c.get("once"):
                    if value(r, items=items) is not True:
                        if on_fail == "fail":
                            raise ScriptError(f"Check failed: {message}")
                        notes.append(f"Check: {message}")
                    continue
                failed = [x for x in items if value(r, item=x) is not True]
                if not failed:
                    continue
                if on_fail == "fail":
                    raise ScriptError(f"Check failed for {len(failed)} item(s): {message}")
                if on_fail == "flag":
                    for x in failed:
                        x.setdefault("flags", []).append(message)
                    notes.append(f"Check: flagged {len(failed)}: {message}")
                else:
                    ids = {id(x) for x in failed}
                    items = [x for x in items if id(x) not in ids]
                    notes.append(f"Check: dropped {len(failed)}: {message}")
        elif kind == "remove_duplicates":
            key = rule(f"{where}: key", conf["key"])
            best_r = rule(f"{where}: keep highest", conf["keep_highest"]) if conf.get("keep_highest") else None
            best: dict[str, tuple[Any, int, dict]] = {}
            for i, x in enumerate(items):
                k = json.dumps(value(key, item=x), sort_keys=True, default=str)
                score = value(best_r, item=x) if best_r else 0
                if k not in best or (best_r and score > best[k][0]):
                    best[k] = (score, best[k][1] if k in best else i, x)
            if len(best) < len(items):
                notes.append(f"Remove duplicates: dropped {len(items) - len(best)}.")
            items = [x for _, _, x in sorted(best.values(), key=lambda t: t[1])]      # in the order first seen
        elif kind == "sort":
            r = rule(f"{where}: by", conf["by"])
            keyed = [(value(r, item=x), i, x) for i, x in enumerate(items)]
            missing = [t for t in keyed if t[0] is None]
            keyed = sorted([t for t in keyed if t[0] is not None], key=lambda t: t[0], reverse=bool(conf.get("descending")))
            items = [x for _, _, x in keyed + missing]                                 # missing values last
            if conf.get("take"):
                if len(items) > int(conf["take"]):
                    notes.append(f"Sort and take: kept the first {int(conf['take'])} of {len(items)}.")
                items = items[: int(conf["take"])]
        elif kind == "summarize":
            source = items if conf.get("of", "items") == "items" else [dict(x) for x in (inputs.get(conf["of"]) or []) if isinstance(x, dict)]
            groups_by = [(name, rule(f"{where}: group by {name}", src)) for name, src in (conf.get("group_by") or {}).items()]
            totals = []
            for name, spec in (conf.get("totals") or {}).items():
                m = AGGREGATES.match(spec)
                if not m:
                    raise ScriptError(f"{where}: {name} must be count(), count(<rule>), sum(<expr>), avg, min or max; got {spec!r}")
                totals.append((name, m.group(1), rule(f"{where}: {name}", m.group(2)) if m.group(2).strip() else None))
            buckets: dict[str, tuple[dict, list[dict]]] = {}
            for x in source:
                key = {name: value(r, item=x) for name, r in groups_by}
                buckets.setdefault(json.dumps(key, sort_keys=True, default=str), (key, []))[1].append(x)
            if not buckets and not groups_by:
                buckets["{}"] = ({}, [])
            rows = []
            for key, members in buckets.values():
                row = dict(key)
                for name, fn, r in totals:
                    vals = [value(r, item=x) for x in members] if r else []
                    if fn == "count":
                        row[name] = sum(1 for v in vals if v is True) if r else len(members)
                    else:
                        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
                        row[name] = (sum(nums) if fn == "sum" else (sum(nums) / len(nums) if nums else None) if fn == "avg"
                                     else (min(nums) if nums else None) if fn == "min" else (max(nums) if nums else None))
                        if isinstance(row[name], float):
                            row[name] = round(row[name], 6)
                rows.append(row)
            if conf.get("save_as"):
                saved[conf["save_as"]] = rows if groups_by else rows[0]    # the list stays as it was
            else:
                items = rows
        elif kind == "match":
            others = [x for x in (inputs.get(conf["with"]) or []) if isinstance(x, dict)]
            key, other_key = rule(f"{where}: key", conf["key"]), rule(f"{where}: other key", conf["other_key"])
            index: dict[str, dict] = {}
            for o in others:
                index.setdefault(json.dumps(value(other_key, other=o), sort_keys=True, default=str), o)
            unmatched = 0
            for x in items:
                found = index.get(json.dumps(value(key, item=x), sort_keys=True, default=str))
                if found is not None:                 # absent when nothing matched: test it with has(item.<as>)
                    x[conf.get("as", "match")] = found
                unmatched += found is None
            if unmatched:
                notes.append(f"Match: {unmatched} of {len(items)} had nothing in {conf['with']}.")
        elif kind == "link":
            r = rule(f"{where}: together", conf["together"])
            parent = list(range(len(items)))

            def find(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            for j in range(len(items)):
                for i in range(j):
                    if find(i) != find(j) and value(r, a=items[i], b=items[j]) is True:
                        parent[find(j)] = find(i)
            clusters: dict[int, list[dict]] = {}
            for i, x in enumerate(items):
                clusters.setdefault(find(i), []).append(x)
            items = [{conf.get("as", "members"): members, "size": len(members)} for members in clusters.values()]
        else:
            raise ScriptError(f"Unknown CEL operator {kind!r}: use one of {', '.join(CEL_OPS)}.")
    return {"items": items, "notes": notes, **saved}


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


# ------------------------------------------------------------------ charts: a PNG drawn from checked rows, no model

BASE, HIGHLIGHT, REFERENCE, INK, GRID = "#4C6FA8", "#D1495B", "#3A3A36", "#3A3A36", "#E6E4DD"


def chart_spec(conf: dict, data: dict) -> dict:
    """A Vega-Lite spec from the step's settings: bars or a line over its rows, rows the highlight rule matches in a
    second color, and an optional dashed reference line (a value from Takes). A raw `spec` is used as given, with the
    rows as its data."""
    rows = [dict(r) for r in data.get("rows") or [] if isinstance(r, dict)]
    if conf.get("highlight"):
        from .cel import Rule
        rule = Rule("highlight", conf["highlight"])
        extra = {k: v for k, v in data.items() if k != "rows"}
        for r in rows:
            try:
                r["_highlight"] = bool(rule.evaluate({"row": r, **extra}))
            except Exception as exc:
                raise ScriptError(f"The highlight rule failed on a row: {exc}") from None
    if conf.get("spec"):
        return {**conf["spec"], "data": {"values": rows}}
    x, y = conf["x"], conf["y"]
    y_axis = {"title": conf.get("y_title", y.replace("_", " ")), "grid": True, "gridColor": GRID, "domain": False, "tickCount": 5}
    if conf.get("y_format"):
        y_axis["format"] = conf["y_format"]
    x_enc = {"field": x, "type": "ordinal", "sort": None, "title": conf.get("x_title", x.replace("_", " ")) or None,
             "axis": {"labelAngle": 0 if len(rows) <= 8 else -45, "domainColor": INK, "tickColor": INK}}
    y_enc = {"field": y, "type": "quantitative", "axis": y_axis}
    color = ({"condition": {"test": "datum._highlight", "value": HIGHLIGHT}, "value": BASE} if conf.get("highlight") else {"value": BASE})
    if conf.get("kind", "bar") == "line":
        layers = [{"mark": {"type": "line", "color": BASE, "strokeWidth": 2}, "encoding": {"x": x_enc, "y": y_enc}},
                  {"mark": {"type": "point", "filled": True, "size": 60}, "encoding": {"x": x_enc, "y": y_enc, "color": color}}]
    else:
        layers = [{"mark": {"type": "bar", "cornerRadiusEnd": 2, "width": {"band": 0.7}}, "encoding": {"x": x_enc, "y": y_enc, "color": color}}]
    ref = data.get(conf["reference"]) if conf.get("reference") else None
    notes = [conf["subtitle"]] if conf.get("subtitle") else []
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        # Its own one-row data: drawn once, not once per row. Its label goes in the subtitle, clear of the bars.
        layers.append({"data": {"values": [{}]}, "mark": {"type": "rule", "strokeDash": [5, 4], "color": REFERENCE, "strokeWidth": 1.5},
                       "encoding": {"y": {"datum": ref}}})
        notes.append(f"Dashed line: {conf.get('reference_label') or conf['reference'].replace('_', ' ')}")
    spec = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "width": conf.get("width", 560), "height": conf.get("height", 260),
            "data": {"values": rows}, "layer": layers,
            "config": {"view": {"stroke": None}, "font": "Helvetica, Arial, sans-serif",
                       "axis": {"labelColor": INK, "titleColor": INK, "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": "normal"},
                       "title": {"anchor": "start", "fontSize": 14, "color": INK, "subtitleColor": "#6B6B63"}}}
    if conf.get("title"):
        spec["title"] = {"text": conf["title"], **({"subtitle": ". ".join(n.rstrip(".") for n in notes) + "."} if notes else {})}
    return spec


def chart(step: str, conf: dict, data: dict) -> dict:
    """Draw the chart to <run dir>/charts/<step>.png (twice the pixels, for sharp screens and email)."""
    import vl_convert as vlc
    if not (data.get("rows") or []):
        raise ScriptError("There are no rows to chart.")
    png = vlc.vegalite_to_png(chart_spec(conf, data), scale=2)
    out = run_dir() / "charts" / f"{step}.png"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(png)
    return {"image": f"charts/{step}.png", "title": conf.get("title") or ""}


# ------------------------------------------------------------------ Cloud Storage: list, read, write new files

def _paths(value: Any) -> list[str]:
    """Paths from a step's input: one path, a list of them, or files from a listing (each with `path`)."""
    items = value if isinstance(value, list) else [value]
    out = []
    for x in items:
        if isinstance(x, dict) and x.get("path"):
            out.append(x["path"])
        elif isinstance(x, dict) and x.get("bucket") and x.get("name"):     # a Pub/Sub notification of a new object
            out.append(f"{x['bucket']}/{x['name']}")
        elif isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def gcs_list(conf: dict, data: dict) -> dict:
    """Files under the prefix (the step's setting, or its `prefix` input), optionally only those modified after a time
    (`modified_after`) and matching a name pattern (`match`, e.g. *.csv)."""
    import fnmatch
    conn = gateway.connect("gcs")
    prefix = data.get("prefix") or conf.get("prefix") or ""
    files = gateway.call(conn, "gcs", "list_objects", {"prefix": prefix, "modified_after": data.get("modified_after") or None,
                                                      "limit": int(conf.get("limit") or 1000)})
    if conf.get("match"):
        files = [f for f in files if fnmatch.fnmatch(f["path"].rsplit("/", 1)[-1], conf["match"])]
    return {"files": files, "count": len(files)}


def gcs_read(conf: dict, data: dict) -> dict:
    """One file, or several (a list of paths or a listing's files): their rows together, each marked with its _file."""
    conn = gateway.connect("gcs")
    paths = _paths(data.get("path") if data.get("path") is not None else data.get("files") if data.get("files") is not None else conf.get("path"))
    if data.get("bucket") and data.get("name") and not paths:
        paths = [f"{data['bucket']}/{data['name']}"]
    rows, files, truncated, text = [], [], False, None
    for path in paths:
        out = gateway.call(conn, "gcs", "read_object", {"path": path, "format": conf.get("format") or "auto"})
        rows += [{**r, "_file": out["path"]} if len(paths) > 1 else r for r in out["rows"]]
        files.append({"path": out["path"], "format": out["format"], "bytes": out["bytes"], "row_count": out["row_count"], "truncated": out["truncated"]})
        truncated = truncated or out["truncated"]
        if len(paths) == 1:
            text = out.get("text")
    return {"rows": rows, "row_count": len(rows), "truncated": truncated, "files": files, "text": text}


def write_objects(conf: dict, dry_run: bool, data: dict) -> dict:
    """A new file at the path template (filled from the step's inputs), holding its `content` input as JSON, JSON lines,
    CSV, text, or a chart's PNG. The gateway refuses a path outside the step's prefixes, or one that already exists."""
    from .gcs_api import serialize
    conn = gateway.connect("gcs")
    path = _fill_text(conf.get("path", ""), {k: v for k, v in data.items() if k != "content"})
    body, content_type = serialize(data.get("content"), conf.get("format") or "json")
    out = gateway.call(conn, "gcs", "write_object", {"path": path, "data": body, "content_type": content_type, "dry_run": dry_run})
    done = f"{out.get('path') or out.get('would_write')} ({len(body):,} bytes)"
    return {"created": [] if dry_run else [done], "would_create": [done] if dry_run else [], "skipped": [], "path": out.get("path") or out.get("would_write")}


# ------------------------------------------------------------------ email: one per run, or one per record

def _as_text(value: Any) -> str:
    """A value as it reads in an email: a list as lines, a record as "name: value" lines, numbers as written."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(f"- {_as_text(v)}" if not isinstance(v, (dict, list)) else _as_text(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_as_text(v)}" for k, v in value.items())
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _fill_text(template: str, record: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: _as_text(record.get(m.group(1))), template).strip()


def send_emails(spec: dict, dry_run: bool, data: dict) -> dict:
    """Fill the step's To, Cc, subject and body templates from its inputs (and each item's fields, one email per
    item), and send each through the gateway, which checks the recipients."""
    conn = gateway.connect("gmail")
    values = data.get("values") or {}
    records = data.get("records")
    sent, would = [], []
    for item in (records if records is not None else [{}]):
        rec = {**values, **(item if isinstance(item, dict) else {"item": item})}
        to = [a.strip() for t in spec.get("to") or [] for a in _fill_text(t, rec).split(",") if a.strip()]
        cc = [a.strip() for t in spec.get("cc") or [] for a in _fill_text(t, rec).split(",") if a.strip()]
        subject = _fill_text(spec.get("subject", ""), rec)
        body = _fill_text(spec.get("body", ""), rec)
        images = [c for c in data.get("charts") or [] if isinstance(c, str) and c]
        gateway.call(conn, "gmail", "send", {"to": to, "cc": cc, "subject": subject, "body": body, "images": images, "dry_run": dry_run})
        (would if dry_run else sent).append(f"To {', '.join(to)}: {subject}")
    return {"created": sent, "would_create": would, "skipped": [], "emails": len(sent) + len(would)}


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


# QuickJS's Date only reads ISO dates; browsers also read "May 22, 2021". So Date.parse and new Date(text) here also read
# "May 22, 2021", "Sat, May 22, 2021", "22 May 2021" and "05/22/2021" (US), with an optional "10:30 AM" or "18:05:09",
# as UTC. ISO text is read exactly as before. One line, so the builder's line numbers in errors stay right.
DATES = ("(function(){const N=Date,P=N.parse,M={jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,sept:8,oct:9,nov:10,dec:11};"
         "function q(s){if(typeof s!=='string')return P(s);let t=P(s);if(!isNaN(t))return t;s=s.trim().replace(/^(mon|tue|wed|thu|fri|sat|sun)[a-z]*\\.?,?\\s+/i,'');"
         "let m,y,mo,d,h=0,mi=0,se=0;const tm=s.match(/,?\\s+(\\d{1,2}):(\\d{2})(?::(\\d{2}))?\\s*([AaPp][Mm])?$/);"
         "if(tm){s=s.slice(0,tm.index);h=+tm[1];mi=+tm[2];se=+(tm[3]||0);if(tm[4]){const pm=/p/i.test(tm[4]);if(h===12)h=pm?12:0;else if(pm)h+=12}}"
         "const mon=(w)=>{w=w.toLowerCase();return M[w.slice(0,4)]!==undefined?M[w.slice(0,4)]:M[w.slice(0,3)]};"
         "if(m=s.match(/^([A-Za-z]+)\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?,?\\s+(\\d{4})$/)){mo=mon(m[1]);d=+m[2];y=+m[3]}"
         "else if(m=s.match(/^(\\d{1,2})\\s+([A-Za-z]+)\\.?,?\\s+(\\d{4})$/)){d=+m[1];mo=mon(m[2]);y=+m[3]}"
         "else if(m=s.match(/^(\\d{1,2})\\/(\\d{1,2})\\/(\\d{4})$/)){mo=+m[1]-1;d=+m[2];y=+m[3]}"
         "else return NaN;if(mo===undefined||d<1||d>31)return NaN;return N.UTC(y,mo,d,h,mi,se)}"
         "function D(...a){if(!new.target)return N();if(a.length===1&&typeof a[0]==='string')return new N(q(a[0]));return new N(...a)}"
         "D.prototype=N.prototype;D.parse=q;D.UTC=N.UTC;D.now=N.now;Date=D;})();")


def javascript(code: str, returns: list[str], data: dict) -> dict:
    """Runs the builder's code in QuickJS: a function body that gets `inputs` (the step's Takes) and returns an object
    with the fields in Returns. No files, network or processes: only its inputs, JSON in and JSON out, time and
    memory limited. Date is available; nothing else from outside."""
    import quickjs
    wrapper = (DATES + "function __main(json) {\n  const inputs = JSON.parse(json);\n"
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

def execute(argv: list[str], data: dict) -> dict:
    """One operation, from the same arguments a script step passes, on its inputs. Records the output (or the error)."""
    p = argparse.ArgumentParser(prog="agent-service-steps")
    p.add_argument("operation", choices=["tidy", "lookup", "filter-rows", "compare", "three-way-match", "create-events", "add-rows", "show", "call-tools", "javascript", "bigquery", "insert-rows", "memory-recall", "decide-prep", "decide-collect", "each-collect", "send-email", "chart", "cel", "gcs-list", "gcs-read", "write-object"])
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
    p.add_argument("--email", help="send-email: {to, cc, subject, body}, as JSON.")
    p.add_argument("--ops-b64", help="cel: the operators, JSON in base64.")
    p.add_argument("--gcs-b64", help="gcs-list, gcs-read, write-object: the settings, JSON in base64.")
    p.add_argument("--chart-b64", help="chart: its settings (kind, x, y, title, highlight, reference ...), JSON in base64.")
    p.add_argument("--returns", default="", help="javascript: the fields it returns, comma-separated.")
    p.add_argument("--arguments", help="call-tools: argument -> template over each record, as JSON.")
    a = p.parse_args(argv)
    import os
    os.environ.setdefault("AGENT_SERVICE_STEP", a.step)      # connection calls are logged against this step

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
    elif a.operation in ("gcs-list", "gcs-read", "write-object"):
        import base64
        conf = json.loads(base64.b64decode(a.gcs_b64).decode()) if a.gcs_b64 else {}
        try:
            out = (gcs_list(conf, data) if a.operation == "gcs-list" else gcs_read(conf, data) if a.operation == "gcs-read"
                   else write_objects(conf, a.dry_run == "true", data))
        except gateway.Refused as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(f"Refused: {exc}")
        except Exception as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(f"Cloud Storage failed: {str(exc).splitlines()[0][:400]}")
    elif a.operation == "cel":
        import base64
        try:
            out = cel_pipeline(json.loads(base64.b64decode(a.ops_b64).decode()), data)
        except ScriptError as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(str(exc))
    elif a.operation == "chart":
        import base64
        try:
            out = chart(a.step, json.loads(base64.b64decode(a.chart_b64).decode()), data)
        except ScriptError as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(str(exc))
    elif a.operation == "send-email":
        try:
            out = send_emails(json.loads(a.email), a.dry_run == "true", data)
        except gateway.Refused as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(f"Refused: {exc}")
    elif a.operation == "each-collect":
        out = each_collect(data, json.loads(a.steps), json.loads(a.paths or "{}"))
    elif a.operation in ("decide-prep", "decide-collect"):
        try:
            out = (decide_prep(data, json.loads(a.item_keys), a.for_step, a.memory, a.max_cases) if a.operation == "decide-prep"
                   else decide_collect(data, json.loads(a.paths)))
        except ScriptError as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(str(exc))
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
            raise StepFailed(f"Refused: {exc}")
        except Exception as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(f"BigQuery failed: {str(exc).splitlines()[0][:400]}")
    elif a.operation == "javascript":
        import base64
        try:
            out = javascript(base64.b64decode(a.code_b64).decode(), [r for r in a.returns.split(",") if r], data)
        except ScriptError as exc:
            record_step(a.step, {"error": str(exc)}, inputs=data)
            raise StepFailed(str(exc))
    elif a.operation == "call-tools":
        try:
            out = call_tools(a.tool, json.loads(a.arguments or "{}"), a.dry_run == "true", data)
        except gateway.Refused as exc:
            raise StepFailed(f"Refused: {exc}")
    else:
        out = create_events(a.calendar, json.loads(a.templates), [f for f in a.match_fields.split(",") if f],
                            a.dry_run == "true", data)
    record_step(a.step, out, inputs=data)
    return out



class StepFailed(Exception):
    """The step failed; the message says why (a script step exits with it, an MCP call returns it as an error)."""


def serve() -> None:
    """The step library as an MCP server, for Built-in steps in a parallel group (Conductor runs only model, set and MCP
    steps in groups). One tool, `run`, takes the same arguments as the script and the step's inputs."""
    from mcp.server.mcpserver import MCPServer
    server = MCPServer("agent-service-steps")

    @server.tool(name="run", description="Run one Built-in operation: its command-line arguments and its inputs.")
    def run(argv: list[str], data: Any = None) -> dict[str, Any]:
        inputs = json.loads(data) if isinstance(data, str) else (data or {})
        return execute(argv, inputs)

    server.run("stdio")


def main() -> None:
    if sys.argv[1:2] == ["--mcp"]:
        serve()
        return
    data = json.loads(sys.stdin.read() or "{}")
    try:
        out = execute(sys.argv[1:], data)
    except StepFailed as exc:
        sys.exit(str(exc))
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
