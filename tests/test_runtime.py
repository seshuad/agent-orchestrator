"""The run worker's programs, without Conductor or a model: limits, gateway, CEL, Built-in steps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_service.runtime import gateway, limits, steps
from agent_service.runtime.cel import Rule, RuleError
from agent_service.runtime.cel_server import evaluate
from agent_service.runtime.runstate import record_step

EXAMPLES = Path(__file__).parent.parent / "examples"
TIDY_OPERATIONS = [
    {"op": "check", "record": "Booking", "timestamps": ["start", "end"],
     "required": ["type", "provider", "confirmation", "travelers", "start", "end", "destination", "source_email", "confidence"]},
    {"op": "remove_duplicates",
     "identity": "b.type + ':' + norm(b.confirmation) + ':' + (b.flight_number != null ? norm(b.flight_number) : date_of(b.start))",
     "keep_highest": "b.confidence == 'high' ? 3 : (b.confidence == 'medium' ? 2 : 1)"},
    {"op": "filter", "keep": "b.end > run.started"},
    {"op": "group", "into": "Trip", "together": "a.confirmation == b.confirmation || b.start - a.end <= duration('24h')"},
    {"op": "flag", "rules": [
        {"when": "size(trip.bookings.filter(b, b.type == 'flight')) == 1", "note": "Only one flight found; no return flight in email."},
        {"when": "trip.bookings.exists(b, !b.travelers.exists(t, is_me(t, run.my_name)))", "note": "Someone else's trip?"}]},
]


def booking(**kw):
    base = {"type": "flight", "provider": "United", "confirmation": "K7PQ2M", "travelers": ["RIVERA/ALEX"],
            "start": "2026-11-19T07:10:00-08:00", "end": "2026-11-19T15:28:00-05:00", "destination": "TPA",
            "address": None, "source_email": "f-united-receipt", "confidence": "high",
            "flight_number": "UA1523", "from_airport": "SFO", "hotel_name": None, "pick_up_location": None}
    return {**base, **kw}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_SERVICE_SIGNING_KEY", "test-key")
    return tmp_path


def use_sample(monkeypatch, agent):
    monkeypatch.setenv("AGENT_SERVICE_SAMPLE_DATA", str(EXAMPLES / agent / "sample-data"))


# ------------------------------------------------------------------ limits and gateway

def test_forged_token_is_refused(run):
    token = limits.mint({"connection": "gmail", "actions": ["search"]})
    body, _ = token.rsplit(".", 1)
    with pytest.raises(limits.LimitsError):
        gateway.connect("gmail", body + ".0000")


def test_flags_can_narrow_a_token_but_not_widen_it(run):
    token = limits.mint({"connection": "gmail", "actions": ["search", "open"], "only_message": "a"})
    assert gateway.connect("gmail", token, {"actions": ["open", "send"]}).limits["actions"] == ["open"]
    with pytest.raises(limits.LimitsError):
        gateway.connect("gmail", token, {"only_message": "b"})


def test_reader_sees_only_its_senders(run, monkeypatch):
    use_sample(monkeypatch, "travel-sync-free")
    token = limits.mint({"connection": "gmail", "actions": ["search", "open"], "senders": ["united.com"], "lookback_days": 180})
    gm = gateway.connect("gmail", token)
    hits = gateway.call(gm, "gmail", "search", {"keywords": []})
    assert hits and all("united.com" in e["from"].lower() for e in hits)
    with pytest.raises(gateway.Refused):
        gateway.call(gm, "gmail", "open", {"message_id": "f-chase-hilton"})
    log = [json.loads(l) for l in (run / "gateway.jsonl").read_text().splitlines()]
    assert [e["outcome"] for e in log] == ["allowed", "refused"]


def test_double_check_opens_only_cited_emails(run, monkeypatch):
    use_sample(monkeypatch, "travel-sync-free")
    token = limits.mint({"connection": "gmail", "actions": ["open"], "only_cited_by": "tidy_up"})
    gm = gateway.connect("gmail", token)
    with pytest.raises(gateway.Refused):                        # nothing cited yet
        gateway.call(gm, "gmail", "open", {"message_id": "f-united-receipt"})
    record_step("tidy_up", {"trips": [{"bookings": [{"source_email": "f-united-receipt"}]}]})
    assert gateway.call(gm, "gmail", "open", {"message_id": "f-united-receipt"})["id"] == "f-united-receipt"
    with pytest.raises(gateway.Refused):
        gateway.call(gm, "gmail", "open", {"message_id": "f-united-booking"})


def test_invoice_reader_opens_only_the_trigger_email(run, monkeypatch):
    use_sample(monkeypatch, "invoice-check")
    gm = gateway.connect("gmail", limits.mint({"connection": "gmail", "actions": ["open"], "only_message": "inv-acme-4471"}))
    assert gateway.call(gm, "gmail", "open", {"message_id": "inv-acme-4471"})
    with pytest.raises(gateway.Refused):
        gateway.call(gm, "gmail", "open", {"message_id": "inv-contoso-9913"})


def test_vendor_search_stays_in_the_senders_domain(run, monkeypatch):
    use_sample(monkeypatch, "invoice-check")
    gm = gateway.connect("gmail", limits.mint({"connection": "gmail", "actions": ["search", "open"], "from_domain": "northwindsupply.com"}))
    ids = [e["id"] for e in gateway.call(gm, "gmail", "search", {"keywords": ["toner"]})]
    assert "nw-order-confirmation" in ids and not any(i.startswith("inv-acme") for i in ids)


# ------------------------------------------------------------------ CEL

def test_invalid_cel_names_the_rule():
    with pytest.raises(RuleError, match="'broken'"):
        Rule("broken", "size(")


def test_is_me_matches_airline_name_formats():
    rule = Rule("me", "is_me(t, 'Alex Rivera')")
    assert rule.evaluate({"t": "RIVERA/ALEX"}) is True
    assert rule.evaluate({"t": "RIVERA/SAM"}) is False


def test_evaluator_reports_failed_requirements():
    out = evaluate([{"name": "bank", "require": "Run the bank check.", "cel": "has(steps.bank)"},
                    {"name": "n", "cel": "size(xs)"}], {"steps": {"bank": None}, "xs": [1, 2]})
    assert out == {"passed": False, "failed": ["Run the bank check."], "results": {"bank": False, "n": 2}, "error": None}


def test_evaluator_reports_a_broken_rule_instead_of_guessing():
    out = evaluate([{"name": "bad", "require": "x", "cel": "steps.bank.status == 'ok'"}], {"steps": {}})
    assert out["passed"] is False and "'bad'" in out["error"]


# ------------------------------------------------------------------ Built-in steps

def test_tidy_dedupes_groups_and_flags():
    records = [
        booking(),
        booking(confidence="medium", source_email="f-united-booking", flight_number="ua 1523"),   # same flight, other email
        booking(flight_number="UA2218", from_airport="TPA", destination="SFO", start="2026-11-23T08:30:00-05:00", end="2026-11-23T11:35:00-08:00"),
        booking(type="hotel", provider="Hilton", confirmation="3344", flight_number=None, hotel_name="Hilton Tampa",
                start="2026-11-19T16:00:00-05:00", end="2026-11-23T11:00:00-05:00", destination="Tampa", source_email="f-chase-hilton"),
        booking(confirmation="R2D2XY", travelers=["RIVERA/SAM"], flight_number="CX873", destination="HKG",
                start="2026-12-02T13:00:00-08:00", end="2026-12-03T19:00:00+08:00", source_email="f-united-sam-hkg"),
        booking(confirmation="OLD1", flight_number="UA9", start="2026-08-01T08:00:00-07:00", end="2026-08-01T12:00:00-05:00"),  # past
        booking(start="2026-11-19T07:10:00"),                                                                     # no time zone
    ]
    out = steps.tidy(TIDY_OPERATIONS, {"records": records, "run": {"my_name": "Alex Rivera"}})
    trips = out["trips"]
    assert len(trips) == 2
    tampa, hkg = trips
    assert tampa["destination"] == "TPA" and len(tampa["bookings"]) == 3 and tampa["flags"] == []
    assert next(b for b in tampa["bookings"] if b["flight_number"] in ("UA1523", "ua 1523"))["confidence"] == "high"
    assert hkg["flags"] == ["Only one flight found; no return flight in email.", "Someone else's trip?"]
    assert any("no time zone" in n for n in out["notes"]) and any("duplicate" in n for n in out["notes"])


def test_invoice_lookups_and_three_way_match(run, monkeypatch):
    use_sample(monkeypatch, "invoice-check")
    monkeypatch.setenv("AGENT_SERVICE_LIMITS_TOKEN", limits.mint(
        {"connection": "google-sheets", "actions": ["read"], "sheets": ["Vendors", "Purchase orders", "Receiving log"]}))
    vendor = steps.lookup("Vendors", "name", "vendor", {"any_of": ["northwind supply"]})
    assert vendor["found"] and vendor["vendor"]["bank_account"] == "US17 NWND 4400 1180"
    po = steps.lookup("Purchase orders", "po_number", "purchase_order", {"any_of": [None, ["PO 5531"]]})["purchase_order"]
    receipts = steps.filter_rows("Receiving log", "po_number", "receipts", {"equals": "PO-5531"})["receipts"]
    invoice = {"lines": [{"description": "Toner cartridge, black", "quantity": 40, "unit_price": 46.0}]}
    assert steps.three_way_match({"invoice": invoice, "purchase_order": po, "receipts": receipts}) == {
        "passed": False, "differences": ["Toner cartridge, black: invoiced 40, received 32"]}
    assert steps.compare({"value": "US90 QXRB 7781 0042", "on_file": "US61 CNTS 2090 3310"})["status"] == "changed"
    with pytest.raises(gateway.Refused):
        steps.lookup("Payment queue", "vendor", "row", {"any_of": ["x"]})


def test_create_events_never_adds_twice(run, monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_LIMITS_TOKEN", limits.mint(
        {"connection": "google-calendar", "actions": ["create_event"], "calendar": "Personal"}))
    tpl = {"flight": {"title": "Flight {flight_number} {from_airport} → {destination}", "starts": "{start}", "ends": "{end}"}}
    rec = {**booking(), "key": "flight:K7PQ2M:UA1523"}
    dry = steps.create_events("Personal", tpl, ["flight_number"], True, {"records": [rec]})
    assert dry["would_create"] and not dry["created"]
    live = steps.create_events("Personal", tpl, ["flight_number"], False, {"records": [rec]})
    assert live["created"] == ["Flight UA1523 SFO → TPA"]
    again = steps.create_events("Personal", tpl, ["flight_number"], False, {"records": [rec, {**rec, "type": "car", "key": "c"}]})
    assert again["created"] == [] and len(again["skipped"]) == 2


def test_records_without_a_key_are_all_added(run, monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_LIMITS_TOKEN", limits.mint(
        {"connection": "google-calendar", "actions": ["create_event"], "calendar": "Home"}))
    tpl = {"default": {"title": "Water: {estimated_gallons} gal at {property}", "starts": "{started_at}", "ends": "{started_at}"}}
    leaks = [{"property": "418 Alder Lane", "estimated_gallons": 164.56, "started_at": "2026-09-21T06:00:00-07:00"},
             {"property": "77 Birch Court", "estimated_gallons": 38.4, "started_at": "2026-09-23T23:30:00-07:00"}]
    out = steps.create_events("Home", tpl, [], False, {"records": leaks})
    assert out["created"] == ["Water: 164.56 gal at 418 Alder Lane", "Water: 38.4 gal at 77 Birch Court"]


def test_add_rows_writes_one_row_per_record(run, monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_LIMITS_TOKEN", limits.mint(
        {"connection": "google-sheets", "actions": ["append_row"], "sheets": ["Leak log"]}))
    leaks = [{"property": "418 Alder Lane", "estimated_gallons": 164.56}, {"property": "77 Birch Court", "estimated_gallons": 38.4}]
    row = {"where": "{property}", "gallons": "{estimated_gallons}", "note": "{estimated_gallons} gal at {property}"}
    dry = steps.add_rows("Leak log", row, True, {"records": leaks})
    assert dry["added"] == [] and dry["would_add"][0] == {"where": "418 Alder Lane", "gallons": 164.56, "note": "164.56 gal at 418 Alder Lane"}
    live = steps.add_rows("Leak log", row, False, {"records": leaks})
    assert len(live["added"]) == 2 and len(json.loads((run / "sheets" / "Leak log.json").read_text())) == 2


class FakeGmail:
    """Stands in for the Gmail API: the same shape of results as a real inbox."""
    mails = [{"id": "g1", "from": "Alerts <alerts@northpeakwater.com>", "subject": "Continuous Water Use", "date": "2026-09-24T12:00:00+00:00", "body": "164.56 gallons"},
             {"id": "g2", "from": "Bank <no-reply@bank.example>", "subject": "Statement", "date": "2026-09-24T12:00:00+00:00", "body": "private"}]

    def __init__(self, connection):
        self.connection = connection

    def search(self, domains, keywords, days, limit=50):
        return [m for m in self.mails if domains is None or any(m["from"].rstrip(">").endswith("@" + d) for d in domains)]

    def email(self, message_id):
        return next((m for m in self.mails if m["id"] == message_id), None)


def test_live_gmail_keeps_the_same_limits(run, monkeypatch):
    from agent_service.runtime import gmail_api
    monkeypatch.setattr(gmail_api, "LiveGmail", FakeGmail)
    token = limits.mint({"connection": "gmail", "actions": ["search", "open"], "senders": ["northpeakwater.com"],
                         "source": "live", "account": "alex-gmail"})
    gm = gateway.connect("gmail", token)
    assert [e["id"] for e in gateway.call(gm, "gmail", "search", {"keywords": []})] == ["g1"]
    with pytest.raises(gateway.Refused):                      # the bank email is outside the step's senders
        gateway.call(gm, "gmail", "open", {"message_id": "g2"})


def test_live_gmail_needs_an_account(run):
    with pytest.raises(limits.LimitsError):
        gateway.connect("gmail", limits.mint({"connection": "gmail", "actions": ["search"], "source": "live"}))


def test_show_passes_its_value_through():
    assert steps.show({"value": [{"property": "418 Alder Lane"}]}) == {"value": [{"property": "418 Alder Lane"}]}


# ------------------------------------------------------------------ GitHub

def github(**kw):
    return gateway.connect("github", limits.mint({"connection": "github", "actions": ["search", "open", "read"], **kw}))


def test_github_reads_only_its_repositories(run, monkeypatch):
    use_sample(monkeypatch, "github-issues")
    gh = github(repos=["northpeak/billing-api"])
    found = gateway.call(gh, "github", "search", {"keywords": []})
    assert {it["repo"] for it in found} == {"northpeak/billing-api"} and "body" not in found[0]
    assert [it["number"] for it in gateway.call(gh, "github", "search", {"keywords": ["10,000"], "state": "open"})] == [415, 412]
    assert [it["number"] for it in gateway.call(gh, "github", "search", {"keywords": [], "state": "open", "label": "BUG"})] == [415, 412, 409]
    issue = gateway.call(gh, "github", "open", {"repo": "northpeak/billing-api", "number": 412})
    assert issue["title"].startswith("Invoices over") and len(issue["comments"]) == 2
    assert "CODEOWNERS" not in gateway.call(gh, "github", "read", {"repo": "northpeak/billing-api", "path": "CODEOWNERS"})
    with pytest.raises(gateway.Refused, match="may not read the repository"):
        gateway.call(gh, "github", "open", {"repo": "northpeak/website", "number": 77})
    with pytest.raises(gateway.Refused):
        gateway.call(gh, "github", "read", {"repo": "northpeak/website", "path": "README.md"})


def test_github_actions_and_repositories_come_from_the_token(run, monkeypatch):
    use_sample(monkeypatch, "github-issues")
    gh = gateway.connect("github", limits.mint({"connection": "github", "actions": ["search"], "repos": ["northpeak/billing-api"]}))
    with pytest.raises(gateway.Refused, match="may not open"):
        gateway.call(gh, "github", "open", {"repo": "northpeak/billing-api", "number": 412})
    assert gateway.call(github(), "github", "search", {"keywords": []}) == []      # no repositories named: nothing in scope


class FakeGitHub:
    def __init__(self, connection):
        assert connection == "my-github"

    def search(self, repos, keywords, days, state=None, label=None):
        return [{"repo": "northpeak/billing-api", "number": 1, "kind": "issue", "title": "t", "state": "open", "author": "a",
                 "labels": [], "created_at": "2026-09-24T00:00:00Z", "updated_at": "2026-09-24T00:00:00Z"},
                {"repo": "someone/else", "number": 2, "kind": "issue", "title": "x", "state": "open", "author": "a",
                 "labels": [], "created_at": "2026-09-24T00:00:00Z", "updated_at": "2026-09-24T00:00:00Z"}]


def test_live_github_keeps_the_same_limits(run, monkeypatch):
    from agent_service.runtime import github_api
    monkeypatch.setattr(github_api, "LiveGitHub", FakeGitHub)
    gh = github(repos=["northpeak/billing-api"], source="live", account="my-github")
    assert [it["repo"] for it in gateway.call(gh, "github", "search", {"keywords": []})] == ["northpeak/billing-api"]


# ------------------------------------------------------------------ MCP connectors

import sys  # noqa: E402

ISSUES = {"transport": "command", "command": sys.executable, "args": [str(Path(__file__).parent / "fixtures/issues_server.py")]}


def issues_limits(actions, arg_limits=None, pins=None, treat=None):
    from agent_service.runtime import upstream
    listed = {t["name"]: t for t in upstream.list_tools(ISSUES, {"kind": "none"})}
    tools = {n: {"treat": (treat or {}).get(n, "read"), "pin": (pins or {}).get(n, upstream.pin(listed[n])), "limits": ["team"]}
             for n in ("list_issues", "get_issue", "create_issue")}
    return {"connection": "mcp", "actions": actions, "account": "alex-issues", "arg_limits": arg_limits or {},
            "upstream": {"connector": "issues", "name": "Issues", "server": ISSUES, "auth": {"kind": "none"}, "tools": tools}}


def test_mcp_tools_need_approval_and_an_unchanged_pin(run):
    import asyncio
    from agent_service.runtime import upstream
    conn = gateway.connect("mcp", limits.mint(issues_limits(["list_issues", "get_issue", "delete_issue"], pins={"get_issue": "old-pin"})))

    async def go():
        async with conn.session() as s:
            listed = {t.name: upstream.tool_dict(t) for t in (await s.list_tools()).tools}
            return conn.usable(listed)
    assert list(asyncio.run(go())) == ["list_issues"]           # get_issue changed since approval; delete_issue never offered
    refused = [json.loads(l)["detail"] for l in (run / "gateway.jsonl").read_text().splitlines()]
    assert "get_issue changed on the server since an admin approved it" in refused and "delete_issue isn't offered by the admin" in refused


def test_mcp_argument_limits(run):
    import asyncio
    from agent_service.runtime import upstream
    conn = gateway.connect("mcp", limits.mint(issues_limits(["list_issues"], {"team": ["ENG", "OPS"]})))

    async def go(args):
        async with conn.session() as s:
            listed = {t.name: upstream.tool_dict(t) for t in (await s.list_tools()).tools}
            return await gateway.mcp_call(conn, s, conn.usable(listed), "list_issues", args)
    text, _ = asyncio.run(go({"team": "ENG"}))
    assert "ENG-12" in text and "HR-8" not in text
    for args in ({"team": "HR"}, {}):                             # another team, or no team at all (which would list every team)
        with pytest.raises(gateway.Refused, match="team must be one of ENG, OPS"):
            asyncio.run(go(args))


def test_mcp_act_tool_per_record_and_dry_run(run, tmp_path, monkeypatch):
    log = tmp_path / "created.jsonl"
    monkeypatch.setenv("ISSUES_LOG", str(log))
    ISSUES["env"] = {"ISSUES_LOG": str(log)}
    try:
        monkeypatch.setenv("AGENT_SERVICE_LIMITS_TOKEN", limits.mint(issues_limits(["create_issue"], {"team": ["ENG"]}, treat={"create_issue": "act"})))
        records = {"records": [{"title": "Leak at 418 Alder Lane"}, {"title": "Leak at 12 Birch Road"}]}
        dry = steps.call_tools("create_issue", {"team": "ENG", "title": "{title}"}, True, records)
        assert [c["arguments"]["title"] for c in dry["would_call"]] == ["Leak at 418 Alder Lane", "Leak at 12 Birch Road"] and not log.exists()
        done = steps.call_tools("create_issue", {"team": "ENG", "title": "{title}"}, False, records)
        assert len(done["called"]) == 2 and [json.loads(l)["title"] for l in log.read_text().splitlines()] == ["Leak at 418 Alder Lane", "Leak at 12 Birch Road"]
        with pytest.raises(gateway.Refused):
            steps.call_tools("create_issue", {"team": "HR", "title": "{title}"}, False, records)
    finally:
        ISSUES.pop("env", None)


def test_the_gateway_serves_only_usable_tools_over_mcp(run, monkeypatch):
    import asyncio
    import os
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    token = limits.mint(issues_limits(["list_issues", "get_issue"], {"team": ["ENG"]}))
    env = {**os.environ, "AGENT_SERVICE_LIMITS_TOKEN": token}

    async def go():
        params = StdioServerParameters(command="agent-service-gateway", args=["--connection", "mcp"], env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            ok = await s.call_tool("list_issues", {"team": "ENG"})
            no = await s.call_tool("list_issues", {"team": "HR"})
            return names, ok.content[0].text, no.content[0].text, no.is_error
    names, ok, no, err = asyncio.run(go())
    assert names == ["list_issues", "get_issue"]
    assert ok.startswith('<result tool="list_issues" from="Issues">') and "ENG-15" in ok
    assert no.startswith("Refused: team must be one of ENG") and err


def test_live_gmail_gives_each_thread_its_own_connection(monkeypatch):
    """The Google client's HTTP layer isn't thread-safe; parallel read_email calls shared one and hung."""
    import threading
    from agent_service.runtime import gmail_api
    monkeypatch.setattr(gmail_api, "credentials", lambda c: object())
    built = []
    import googleapiclient.discovery
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **kw: built.append(kw["http"]) or object())
    import google_auth_httplib2
    monkeypatch.setattr(google_auth_httplib2, "AuthorizedHttp", lambda creds, http: http)
    g = gmail_api.LiveGmail("x")
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(g.svc)) for _ in range(3)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len({id(s) for s in seen}) == 3 and all(h.timeout == gmail_api.HTTP_TIMEOUT for h in built)
    assert g.svc is g.svc                       # one per thread, reused


# ------------------------------------------------------------------ JavaScript steps

def test_javascript_finds_the_oldest_open_issues():
    issues = [{"number": n, "title": f"Issue {n}", "state": s, "created_at": c} for n, s, c in
              [(1, "open", "2025-01-10T00:00:00Z"), (2, "closed", "2024-01-01T00:00:00Z"), (3, "open", "2024-06-01T00:00:00Z"),
               (4, "open", "2026-09-01T00:00:00Z"), (5, "open", "2023-03-15T00:00:00Z")]]
    code = """
      const open = inputs.issues.filter(i => i.state === 'open');
      const now = Date.parse('2026-09-26T00:00:00Z');
      const aged = open.map(i => ({ ...i, days_open: Math.floor((now - Date.parse(i.created_at)) / 86400000) }));
      aged.sort((a, b) => b.days_open - a.days_open);
      return { oldest: aged.slice(0, inputs.how_many) };"""
    out = steps.javascript(code, ["oldest"], {"issues": issues, "how_many": 2})
    assert [i["number"] for i in out["oldest"]] == [5, 3] and out["oldest"][0]["days_open"] == 1291


def test_javascript_errors_say_what_went_wrong():
    with pytest.raises(steps.ScriptError, match="threw an error: .*nope"):
        steps.javascript("throw new Error('nope')", ["x"], {})
    with pytest.raises(steps.ScriptError, match="more than 2 seconds"):
        steps.javascript("while (true) {}", ["x"], {})
    with pytest.raises(steps.ScriptError, match="has no oldest"):
        steps.javascript("return { top: [] }", ["oldest"], {})
    with pytest.raises(steps.ScriptError, match="must return an object"):
        steps.javascript("return 42", ["x"], {})
    with pytest.raises(steps.ScriptError, match="doesn't parse"):
        steps.javascript("return {", ["x"], {})
    out = steps.javascript("return { access: [typeof require, typeof fetch, typeof process, typeof std].join(' ') }", ["access"], {})
    assert out["access"] == "undefined undefined undefined undefined"            # only its inputs: no files, network or processes


def test_the_mcp_gateway_survives_a_broken_or_slow_upstream(run, monkeypatch):
    """A connector's server crashing or stalling mid-step must come back to the model as an error, not hang the run."""
    import asyncio
    import os
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from agent_service.runtime import upstream
    listed = {t["name"]: t for t in upstream.list_tools(ISSUES, {"kind": "none"})}
    spec = issues_limits(["get_issue", "crash", "slow"])
    spec["upstream"]["tools"].update({n: {"treat": "read", "pin": upstream.pin(listed[n]), "limits": []} for n in ("crash", "slow")})
    env = {**os.environ, "AGENT_SERVICE_LIMITS_TOKEN": limits.mint(spec), "AGENT_SERVICE_TOOL_TIMEOUT": "2"}

    async def go():
        params = StdioServerParameters(command="agent-service-gateway", args=["--connection", "mcp"], env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            crashed = await asyncio.wait_for(s.call_tool("crash", {}), 20)
            after_crash = await asyncio.wait_for(s.call_tool("get_issue", {"id": "ENG-12", "team": "ENG"}), 20)
            stalled = await asyncio.wait_for(s.call_tool("slow", {"seconds": 10}), 20)
            after_stall = await asyncio.wait_for(s.call_tool("get_issue", {"id": "ENG-12", "team": "ENG"}), 20)
            return crashed, after_crash, stalled, after_stall
    crashed, after_crash, stalled, after_stall = asyncio.run(go())
    assert crashed.is_error and "Issues failed" in crashed.content[0].text
    assert not after_crash.is_error and "ENG-12" in after_crash.content[0].text          # reconnected
    assert stalled.is_error and "no answer from Issues in 2 seconds" in stalled.content[0].text
    assert not after_stall.is_error


# ------------------------------------------------------------------ BigQuery (sample tables in DuckDB)

BQ_UP = {"connector": "bq", "name": "Warehouse", "auth": {"kind": "gcloud"}, "billing_project": "demo-project",
         "allowed": ["sales_processed"], "max_bytes_cap": "10GB"}


def test_bigquery_queries_are_checked_before_they_run(run, monkeypatch):
    use_sample(monkeypatch, "bigquery-sales")
    bq = gateway.connect("bigquery", limits.mint({"connection": "bigquery", "actions": ["query", "list_tables", "get_schema"],
                                                  "datasets": ["sales_processed"], "max_rows": 2, "max_bytes": "1MB", "upstream": BQ_UP}))
    out = gateway.call(bq, "bigquery", "query", {"sql": "SELECT region, COUNT(*) AS n FROM `demo-project.sales_processed.orders` "
                                                        "WHERE amount > @min GROUP BY region ORDER BY n DESC", "params": {"min": 100}})
    assert out["row_count"] == 2 and out["truncated"] and out["tables"] == ["demo-project.sales_processed.orders"]
    for sql, why in [("SELECT * FROM `demo-project.credit.customer_limits`", "may not read demo-project.credit.customer_limits"),
                     ("DELETE FROM sales_processed.orders WHERE true", "Only a single SELECT"),
                     ("SELECT 1; SELECT 2", "Only a single SELECT")]:
        with pytest.raises(gateway.Refused, match=why):
            gateway.call(bq, "bigquery", "query", {"sql": sql})
    assert [t["table"] for t in gateway.call(bq, "bigquery", "list_tables", {"dataset": "sales_processed"})] == \
        ["demo-project.sales_processed.monthly_trend", "demo-project.sales_processed.orders",
         "demo-project.sales_processed.revenue_by_product", "demo-project.sales_processed.revenue_by_region"]
    tight = gateway.connect("bigquery", limits.mint({"connection": "bigquery", "actions": ["query"], "max_bytes": "1KB", "upstream": BQ_UP}))
    with pytest.raises(gateway.Refused, match="over this step's limit of 1.0 KB"):
        gateway.call(tight, "bigquery", "query", {"sql": "SELECT * FROM `demo-project.sales_processed.orders`"})
    calls = [json.loads(l) for l in (run / "gateway.jsonl").read_text().splitlines()]
    assert calls[0]["connector"] == "bq" and "bytes_billed" in calls[0]                  # counted against the monthly budget


def test_a_step_cannot_read_beyond_its_connector():
    from agent_service.runtime.bigquery_api import Warehouse
    wh = Warehouse({"source": "sample", "datasets": ["sales_processed", "credit", "other-project.x"], "upstream": BQ_UP},
                   EXAMPLES / "bigquery-sales/sample-data")
    assert wh.allow == ["sales_processed"]                                                 # the admin allowed only sales_processed


def test_bigquery_budget_counts_this_months_queries(tmp_path):
    from agent_service import runner
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "gateway.jsonl").write_text(json.dumps({"ts": __import__("time").time(), "connector": "bq",
                                                               "bytes_billed": 1024 ** 4}) + "\n")      # 1 TiB = $6.25
    assert round(runner.month_spend("bq", tmp_path), 2) == 6.25 and runner.month_spend("other", tmp_path) == 0


def test_runs_start_conductor_with_prompt_caching_on(monkeypatch):
    """Conductor's Claude steps get Pydantic AI's automatic prompt caching through conductor_cached.py."""
    import shutil
    import subprocess
    from agent_service import runner
    if shutil.which("conductor") is None:
        pytest.skip("conductor is not installed")
    command = runner.conductor_command()
    assert command[-1].endswith("conductor_cached.py")
    probe = ("import runpy, sys; m = runpy.run_path(sys.argv[1]); assert m['enable_prompt_caching']();"
             "from conductor.providers._pydantic_ai import agent_builder as ab; from conductor.config.schema import AgentDef;"
             "s = ab._build_anthropic_model_settings(AgentDef(name='a', model='claude-sonnet-5', prompt='x'), None, None, None);"
             "print(s.get('anthropic_cache'))")
    out = subprocess.run([command[0], "-c", probe, command[1]], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "True", out.stderr
    monkeypatch.setenv("AGENT_SERVICE_PROMPT_CACHE", "0")
    assert runner.conductor_command() == ["conductor"]


def test_cel_numbers_mix_ints_and_doubles():
    assert Rule("r", "a / b > 500").evaluate({"a": 146683.71, "b": 208}) is True        # BigQuery: revenue double, orders int
    assert Rule("r", "a == 1").evaluate({"a": 1.0}) is True
    assert Rule("r", "7 / 2").evaluate({}) == 3                                         # whole numbers stay whole


def test_cel_operators_keep_add_check_summarize_sort():
    months = [{"month": m, "orders": o, "revenue": r} for m, o, r in [("2024-09", 64, 34518.15), ("2024-10", 67, 50888), ("2024-11", 54, 21483.49), ("2024-12", 66, 49537.93)]]
    out = steps.cel_pipeline([
        {"summarize": {"totals": {"avg": "avg(item.revenue)", "n": "count()"}, "save_as": "overall"}},
        {"add_fields": {"drop_pct": "(overall.avg - item.revenue) / overall.avg * 100", "aov": "item.revenue / item.orders"}},
        {"check": [{"rule": "item.aov < 700", "message": "order value high", "on_fail": "flag"},
                   {"rule": "size(items) >= 3", "message": "too few months", "once": True, "on_fail": "fail"}]},
        {"keep": "item.drop_pct >= double(run.threshold)"},
        {"sort": {"by": "item.drop_pct", "descending": True, "take": 1}},
    ], {"items": months, "run": {"threshold": 20}})
    assert [m["month"] for m in out["items"]] == ["2024-11"] and out["overall"]["n"] == 4
    assert "flags" not in out["items"][0] and any(n.startswith("Keep: left out 3") for n in out["notes"])
    with pytest.raises(steps.ScriptError, match="too few months"):
        steps.cel_pipeline([{"check": [{"rule": "size(items) >= 9", "message": "too few months", "once": True, "on_fail": "fail"}]}],
                           {"items": months})


def test_cel_operators_dedupe_match_group_link():
    issues = [{"n": 1, "author": "ana", "labels": ["bug"]}, {"n": 2, "author": "bo", "labels": []}, {"n": 3, "author": "ana", "labels": ["bug"]},
              {"n": 1, "author": "ana", "labels": ["bug"]}]
    out = steps.cel_pipeline([
        {"remove_duplicates": {"key": "item.n"}},
        {"match": {"with": "people", "key": "item.author", "other_key": "other.login", "as": "person"}},
        {"add_fields": {"team": "has(item.person) ? item.person.team : 'unknown'"}},
        {"summarize": {"group_by": {"team": "item.team"}, "totals": {"issues": "count()", "bugs": "count('bug' in item.labels)"}}},
        {"sort": {"by": "item.issues", "descending": True}},
    ], {"items": issues, "people": [{"login": "ana", "team": "core"}]})
    assert out["items"] == [{"team": "core", "issues": 2, "bugs": 2}, {"team": "unknown", "issues": 1, "bugs": 0}]
    assert out["notes"] == ["Remove duplicates: dropped 1.", "Match: 1 of 3 had nothing in people."]
    linked = steps.cel_pipeline([{"link": {"together": "a.conf == b.conf"}}], {"items": [{"conf": "X"}, {"conf": "Y"}, {"conf": "X"}]})
    assert [c["size"] for c in linked["items"]] == [2, 1]


def test_gcs_paths_scope_and_formats():
    from agent_service.runtime import gcs_api as g
    assert g.allowed(["landing/orders/", "other/x"], ["landing"]) == ["landing/orders/"]     # a step stays within the connector's
    assert g.within("landing/orders/2026/a.csv", ["landing/orders/"]) and not g.within("landing/ordersX/a.csv", ["landing/orders/"])
    with pytest.raises(g.StorageRefused):
        g.split("landing/../secrets/key.json")
    assert g.format_of("gs://bkt/x/data.parquet", None) == "parquet" and g.format_of("bkt/x/notes", "auto") == "text"
    rows = g.parse(b"id,amount,region\n1,12.50,West\n2,,East\n", "csv", 10, False)
    assert rows["rows"] == [{"id": 1, "amount": 12.5, "region": "West"}, {"id": 2, "amount": None, "region": "East"}]
    assert g.parse(b'{"a":1}\n{"a":2}\n{"a":3}\n', "jsonl", 2, False) == {"rows": [{"a": 1}, {"a": 2}], "row_count": 2, "truncated": True, "text": None}
    body, kind = g.serialize([{"a": 1, "b": [1, 2]}, {"a": 2, "c": "x"}], "csv")
    assert kind == "text/csv" and body.decode().splitlines() == ["a,b,c", "1,\"[1, 2]\",", "2,,x"]


def test_gcs_parquet_reads_through_duckdb(tmp_path):
    import duckdb
    from agent_service.runtime import gcs_api as g
    out = tmp_path / "t.parquet"
    duckdb.sql(f"COPY (SELECT 'South' AS region, 146683.71::DECIMAL(12,2) AS revenue, DATE '2026-10-01' AS day) TO '{out}' (FORMAT parquet)")
    assert g.parse(out.read_bytes(), "parquet", 10, False)["rows"] == [{"region": "South", "revenue": 146683.71, "day": "2026-10-01"}]


def test_javascript_dates_read_like_a_browser():
    code = ('const iso = (t) => isNaN(t) ? null : new Date(t).toISOString().slice(0, 16);'
            'return {a: iso(Date.parse("May 22, 2021")), b: iso(Date.parse("Sat, May 22, 2021")), c: iso(Date.parse("22 May 2021")),'
            ' d: iso(Date.parse("05/22/2021")), e: iso(Date.parse("May 22, 2021 10:30 PM")), f: new Date("2021-05-22T10:00:00Z").getUTCHours(),'
            ' g: iso(Date.parse("not a date")), h: new Date(2021, 0, 5).getMonth()};')
    assert steps.javascript(code, list("abcdefgh"), {}) == {"a": "2021-05-22T00:00", "b": "2021-05-22T00:00", "c": "2021-05-22T00:00",
                                                           "d": "2021-05-22T00:00", "e": "2021-05-22T22:30", "f": 10, "g": None, "h": 0}
