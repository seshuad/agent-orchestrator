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


# ------------------------------------------------------------------ Microsoft 365: SharePoint and SMTP

def _docx(paragraphs, table):
    import io
    import zipfile
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    p = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paragraphs)
    rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>" for r in table)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {w}><w:body>{p}<w:tbl>{rows}</w:tbl></w:body></w:document>")
    return buf.getvalue()


def _pdf(text):
    """A one-page PDF with a line of text, written by hand (pypdf reads; it doesn't typeset)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_sharepoint_paths_match_whole_segments_and_ignore_case():
    from agent_service.runtime import sharepoint_api as sp
    assert sp.within("finance/shared documents/Reports/q3.xlsx", ["Finance/Shared Documents/Reports"])
    assert not sp.within("Finance/Shared Documents/ReportsOld/q3.xlsx", ["Finance/Shared Documents/Reports"])
    assert sp.allowed(["Finance/Lists/Region Owners", "HR/Shared Documents"], ["Finance"]) == ["Finance/Lists/Region Owners"]
    for bad in ("Finance/../HR/x.docx", "Finance//x", ""):
        with pytest.raises(sp.StorageRefused):
            sp.parts(bad)
    assert sp.clean_fields({"Title": "East", "@odata.etag": "1", "_UIVersionString": "1.0", "ContentType": "Item", "Owner": "A"}) == {"Title": "East", "Owner": "A"}


def test_excel_word_and_pdf_files_read_as_rows_or_text():
    from agent_service.runtime import gcs_api as g
    xlsx = EXAMPLES / "bigquery-sales/sample-data/sharepoint/Finance/Shared Documents/Targets/regional-targets-q4-2026.xlsx"
    out = g.parse(xlsx.read_bytes(), "xlsx", 100, False)
    assert out["rows"][0] == {"region": "East", "month": "2026-10", "revenue_target": 41000, "orders_target": 98, "_sheet": "Q4 2026"}
    assert out["row_count"] == 16 and out["rows"][-1]["_sheet"] == "Notes"
    doc = g.parse(_docx(["Data quality checks", "Loads arrive by 07:00 UTC."], [["Region", "Owner"], ["East", "Avery"]]), "docx", 10, False)
    assert doc["text"] == "Data quality checks\nLoads arrive by 07:00 UTC.\nRegion | Owner\nEast | Avery" and doc["rows"] == []
    assert g.parse(_pdf("Quarterly targets"), "pdf", 10, False)["text"] == "Quarterly targets"
    with pytest.raises(g.StorageRefused, match="Excel"):
        g.parse(b"not a workbook", "xlsx", 10, False)
    assert g.format_of("bkt/a/b.XLSX", None) == "xlsx" and g.format_of("bkt/a/b.pdf", "auto") == "pdf"


def test_sharepoint_sample_files_lists_and_new_files(run, monkeypatch):
    use_sample(monkeypatch, "bigquery-sales")
    sp = gateway.connect("sharepoint", limits.mint({"connection": "sharepoint", "actions": ["list_objects", "read_object", "read_list", "write_object"],
                                                     "paths": ["Finance/Shared Documents/Targets", "Finance/Lists/Region Owners",
                                                               "Finance/Shared Documents/Reports"], "source": "sample"}))
    assert len(sp.list_objects("finance/shared documents/targets")) == 1          # names ignore case, as in SharePoint
    files = sp.list_objects("Finance/Shared Documents/Targets")
    assert [f["path"] for f in files] == ["Finance/Shared Documents/Targets/regional-targets-q4-2026.xlsx"] and files[0]["format"] == "xlsx"
    assert sp.read_object(files[0]["path"])["rows"][0]["region"] == "East"
    owners = sp.read_list("Finance/Lists/Region Owners")
    assert owners["row_count"] == 5 and owners["rows"][0] == {"Title": "East", "Owner": "Avery Chen", "OwnerEmail": "avery.chen@example.com", "Active": True}
    with pytest.raises(gateway.Refused, match="may not use Finance/Shared Documents/Policies"):
        sp.read_object("Finance/Shared Documents/Policies/data-quality-checks.md")
    with pytest.raises(gateway.Refused, match="read it as a list"):
        sp.read_object("Finance/Lists/Region Owners")
    assert sp.write_object("Finance/Shared Documents/Reports/x.csv", b"a\n1\n", "text/csv", True) == {
        "written": False, "would_write": "Finance/Shared Documents/Reports/x.csv", "bytes": 4}
    assert sp.write_object("Finance/Shared Documents/Reports/x.csv", b"a\n1\n", "text/csv", False)["written"]
    assert (run / "sharepoint/Finance/Shared Documents/Reports/x.csv").read_text() == "a\n1\n"
    with pytest.raises(gateway.Refused, match="never overwrite"):
        sp.write_object("Finance/Shared Documents/Reports/X.csv", b"b", "text/csv", False)     # SharePoint names ignore case
    reader = gateway.connect("sharepoint", limits.mint({"connection": "sharepoint", "actions": ["read_object"], "paths": ["Finance"], "source": "sample"}))
    with pytest.raises(gateway.Refused, match="may not read list"):
        reader.read_list("Finance/Lists/Region Owners")


class _Resp:
    def __init__(self, status, body=None, raw=b"", headers=None):
        self.status_code, self._body, self.text = status, body, json.dumps(body) if body is not None else ""
        self.headers = headers or {"content-type": "application/json"}
        self.raw = __import__("io").BytesIO(raw)
        self.raw.read = (lambda f: lambda n, decode_content=True: f(n))(self.raw.read)

    def json(self):
        return self._body

    def close(self):
        pass


class FakeGraph:
    """Microsoft Graph, as far as the connector uses it: one site, one library, a folder, a file and a list."""

    def __init__(self):
        self.headers, self.calls, self.uploaded = {}, [], {}

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, params, headers))
        path = url.replace("https://graph.microsoft.com/v1.0", "")
        if path == "/sites/contoso.sharepoint.com:/sites/Finance":
            return _Resp(200, {"id": "site-1"})
        if path.startswith("/sites/contoso.sharepoint.com:/sites/"):
            return _Resp(404, {"error": {"message": "not found"}})
        if path == "/sites/site-1/drives":
            return _Resp(200, {"value": [{"id": "drive-1", "name": "Documents", "webUrl": "https://contoso.sharepoint.com/sites/Finance/Shared%20Documents"}]})
        if path == "/drives/drive-1/root/children":
            return _Resp(200, {"value": []})
        if path == "/drives/drive-1/root:/Targets:/children":
            return _Resp(200, {"value": [{"name": "q4.csv", "size": 25, "lastModifiedDateTime": "2026-10-01T08:00:00Z", "file": {"mimeType": "text/csv"}},
                                         {"name": "old", "folder": {"childCount": 1}}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page"})
        if path == "/next-page":
            return _Resp(200, {"value": [{"name": "notes.txt", "size": 5, "lastModifiedDateTime": "2026-09-01T08:00:00Z", "file": {}}]})
        if path == "/drives/drive-1/root:/Targets/old:/children":
            return _Resp(200, {"value": [{"name": "q3.csv", "size": 10, "lastModifiedDateTime": "2026-07-01T08:00:00Z", "file": {}}]})
        if path == "/drives/drive-1/root:/Targets/q4.csv:":
            return _Resp(200, {"size": 25, "lastModifiedDateTime": "2026-10-01T08:00:00Z", "file": {}})
        if path == "/drives/drive-1/root:/Targets/q4.csv:/content":
            return _Resp(206, raw=b"region,target\nEast,41000\n")
        if path == "/sites/site-1/lists/Region%20Owners/items":
            return _Resp(200, {"value": [{"id": "1", "fields": {"Title": "East", "OwnerEmail": "a@example.com", "@odata.etag": "x", "ContentType": "Item"}}]})
        return _Resp(404, {"error": {"message": f"no {path}"}})

    def put(self, url, data=None, params=None, headers=None, **kw):
        self.calls.append(("PUT", url, params, headers))
        if url.endswith("/Reports/new.csv:/content"):
            if url in self.uploaded:
                return _Resp(409, {"error": {"message": "nameAlreadyExists"}})
            self.uploaded[url] = data
            return _Resp(201, {"webUrl": "https://contoso.sharepoint.com/sites/Finance/Shared%20Documents/Reports/new.csv"})
        return _Resp(403, {"error": {"message": "Access denied"}})


def test_live_sharepoint_signs_in_as_the_app_and_keeps_the_limits(run, monkeypatch, tmp_path):
    import base64 as b64
    import requests
    from agent_service.runtime import sharepoint_api, vault
    monkeypatch.setenv("AGENT_SERVICE_VAULT", str(tmp_path / "vault"))
    vault.save("connector-m365", {"client_secret": "s3cret"})
    claims = b64.urlsafe_b64encode(json.dumps({"app_displayname": "Agent Orchestrator", "roles": ["Sites.Selected"]}).encode()).decode().rstrip("=")
    posted = []

    def post(url, data=None, timeout=None):
        posted.append((url, data))
        return _Resp(200, {"access_token": f"h.{claims}.s", "expires_in": 3600})

    graph = FakeGraph()
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "Session", lambda: graph)
    sharepoint_api._TOKENS.clear()
    up = {"connector": "m365", "tenant_id": "tenant-1", "client_id": "app-1", "hostname": "contoso.sharepoint.com",
          "allowed": ["Finance/Shared Documents", "Finance/Lists/Region Owners"]}
    sp = gateway.connect("sharepoint", limits.mint({"connection": "sharepoint", "actions": ["list_objects", "read_object", "read_list", "write_object"],
                                                     "paths": ["Finance/Shared Documents/Targets", "Finance/Shared Documents/Reports", "Finance/Lists/Region Owners",
                                                               "HR/Shared Documents"], "source": "live", "upstream": up}))
    assert posted[0][0] == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token" and posted[0][1]["scope"] == "https://graph.microsoft.com/.default"
    assert sp.store.allow == ["Finance/Shared Documents/Targets", "Finance/Shared Documents/Reports", "Finance/Lists/Region Owners"]   # HR isn't the connector's
    files = sp.list_objects("Finance/Shared Documents/Targets", modified_after="2026-08-01")
    assert [f["path"] for f in files] == ["Finance/Shared Documents/Targets/q4.csv", "Finance/Shared Documents/Targets/notes.txt"]
    assert sp.read_object("Finance/Shared Documents/Targets/q4.csv")["rows"] == [{"region": "East", "target": 41000}]
    assert [c for c in graph.calls if c[1].endswith("/content")][0][3] == {"Range": "bytes=0-24"}
    assert sp.read_list("Finance/Lists/Region Owners")["rows"] == [{"id": "1", "Title": "East", "OwnerEmail": "a@example.com"}]
    out = sp.write_object("Finance/Shared Documents/Reports/new.csv", b"a\n", "text/csv", False)
    assert out["written"] and graph.calls[-1][2] == {"@microsoft.graph.conflictBehavior": "fail"}
    with pytest.raises(gateway.Refused, match="never overwrite"):
        sp.write_object("Finance/Shared Documents/Reports/new.csv", b"a\n", "text/csv", False)
    with pytest.raises(gateway.Refused, match="may not use HR"):
        sp.list_objects("HR/Shared Documents")
    assert sharepoint_api.identity_and_check(up) == ("Signed in as the app Agent Orchestrator (Sites.Selected). Can see "
                                                     "Finance/Shared Documents (0 files), Finance/Lists/Region Owners (1 items).")
    assert len(posted) == 1                                       # the token is reused until it expires


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.log = host, port, []

    def ehlo(self):
        self.log.append("ehlo")

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(("login", user, password))

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append((self.host, self.port, self.log, msg, to_addrs))
        return {}

    def noop(self):
        return (250, b"ok")

    def quit(self):
        pass


def test_smtp_sends_only_to_the_steps_recipients_through_the_relay(run, monkeypatch, tmp_path):
    import smtplib
    from agent_service.runtime import sampledata, vault
    monkeypatch.setenv("AGENT_SERVICE_VAULT", str(tmp_path / "vault"))
    vault.save("connector-m365", {"smtp_password": "pw"})
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    up = {"connector": "m365", "smtp": {"host": "smtp.office365.com", "security": "starttls", "username": "agents@example.com",
                                        "from_address": "agents@example.com"}}
    mail = gateway.connect("smtp", limits.mint({"connection": "smtp", "actions": ["send"], "recipients": ["@example.com"], "max_emails": 2,
                                                 "source": "live", "upstream": up}))
    with pytest.raises(gateway.Refused, match="may not send email to x@other.org"):
        mail.send(["a@example.com", "x@other.org"], "Hi", "Body")
    out = mail.send(["a@example.com"], "Sales load", "All loaded.", cc=["b@example.com"])
    assert out["status"] == "sent" and out["message_id"].endswith("@example.com>")
    [(host, port, log, msg, to)] = FakeSMTP.sent
    assert (host, port, to) == ("smtp.office365.com", 587, ["a@example.com", "b@example.com"])
    assert log == ["ehlo", "starttls", "ehlo", ("login", "agents@example.com", "pw")] and msg["From"] == "agents@example.com"
    mail.send(["a@example.com"], "Two", "x")
    with pytest.raises(gateway.Refused, match="already sent 2"):
        mail.send(["a@example.com"], "Three", "x")
    assert [m["subject"] for m in sampledata.outbox()] == ["Sales load", "Two"]
    test_run = gateway.connect("smtp", limits.mint({"connection": "smtp", "actions": ["send"], "recipients": ["@example.com"], "source": "sample"}))
    assert test_run.send(["a@example.com"], "Test", "x")["status"] == "sent (test run: not delivered)" and len(FakeSMTP.sent) == 2
    with pytest.raises(limits.LimitsError, match="no SMTP server"):
        gateway.connect("smtp", limits.mint({"connection": "smtp", "actions": ["send"], "source": "live", "upstream": {"connector": "m365"}}))
    from agent_service.runtime import smtp_api
    assert smtp_api.check(up) == "Reached smtp.office365.com:587 (STARTTLS, signed in as agents@example.com); email goes from agents@example.com."


# ------------------------------------------------------------------ Trino and Spark SQL (Dataproc)

def test_sql_checks_allow_one_select_within_the_steps_data():
    from agent_service.runtime import sql_engines as s
    trino = s.Names("trino", "iceberg", "sales")
    tree, tables = s.check("WITH x AS (SELECT * FROM orders WHERE region = @r) SELECT count(*) FROM x", {"r": "West's"}, "trino", trino, ["iceberg.sales"])
    assert tables == ["iceberg.sales.orders"]
    assert s.capped(tree, 10, "trino") == ("SELECT * FROM (WITH x AS (SELECT * FROM orders WHERE region = 'West''s') SELECT COUNT(*) FROM x) "
                                           "AS q LIMIT 11")
    assert "'@r'" in s.capped(s.check("SELECT * FROM orders WHERE note = '@r'", {}, "trino", trino, ["iceberg"])[0], 5, "trino")   # not a parameter
    for sql, why in [("SELECT * FROM hive.hr.salaries", "may not read hive.hr.salaries"), ("DELETE FROM orders", "this is DELETE"),
                     ("SELECT 1; SELECT 2", "2 statements"), ("SELECT * FROM orders WHERE x = @missing", "passes no missing"),
                     ("SELEC * FROM orders", "doesn't parse")]:
        with pytest.raises(s.QueryRefused, match=why):
            s.check(sql, {}, "trino", trino, ["iceberg.sales"])
    with pytest.raises(s.QueryRefused, match="in full"):
        s.check("SELECT * FROM orders", {}, "trino", s.Names("trino", None, None), ["iceberg"])
    spark = s.Names("spark-sql", None, None)
    assert s.check("SELECT * FROM sales.orders", {}, "spark-sql", spark, ["sales"])[1] == ["spark_catalog.sales.orders"]
    for sql in ("SELECT * FROM lake.sales.orders", "SELECT * FROM orders"):          # another catalog; the default schema
        with pytest.raises(s.QueryRefused, match="may not read"):
            s.check(sql, {}, "spark-sql", spark, ["sales"])
    for sql in ("CREATE TABLE sales.x AS SELECT 1", "INSERT OVERWRITE DIRECTORY 'gs://x/y' SELECT 1", "CACHE TABLE sales.orders"):
        with pytest.raises(s.QueryRefused, match="Only a single SELECT"):
            s.check(sql, {}, "spark-sql", spark, ["sales"])


def test_trino_and_spark_steps_run_on_sample_tables(run, monkeypatch):
    use_sample(monkeypatch, "bigquery-sales")
    trino = gateway.connect("trino", limits.mint({"connection": "trino", "actions": ["query", "list_tables", "get_schema"],
                                                   "datasets": ["iceberg.sales_processed"], "max_rows": 2, "source": "sample"}))
    out = trino.query("SELECT region, total_revenue FROM iceberg.sales_processed.revenue_by_region WHERE total_orders > @n "
                      "ORDER BY total_revenue DESC", {"n": 0})
    assert out["row_count"] == 2 and out["truncated"] and out["tables"] == ["iceberg.sales_processed.revenue_by_region"]
    assert out["rows"][0] == {"region": "West", "total_revenue": 151200.4}
    assert trino.list_tables("iceberg.sales_processed") == [{"table": f"iceberg.sales_processed.{t}"} for t in
                                                            ("monthly_trend", "revenue_by_product", "revenue_by_region")]
    assert {"name": "total_revenue", "type": "DOUBLE"} in trino.get_schema("iceberg.sales_processed.revenue_by_region")["columns"]
    with pytest.raises(gateway.Refused, match="may not read iceberg.hr.people"):
        trino.query("SELECT * FROM iceberg.hr.people")
    spark = gateway.connect("spark-sql", limits.mint({"connection": "spark-sql", "actions": ["query"], "datasets": ["sales_processed"], "source": "sample"}))
    rows = spark.query("SELECT substring(month, 1, 4) AS year, sum(total_orders) AS n FROM sales_processed.monthly_trend GROUP BY 1 ORDER BY 1")["rows"]
    assert rows[0]["year"].isdigit() and sum(r["n"] for r in rows) > 0
    with pytest.raises(gateway.Refused, match="may not get schema"):
        spark.get_schema("sales_processed.monthly_trend")


def test_a_step_naming_data_its_connector_doesnt_allow_is_told_so(run):
    from agent_service.runtime import sql_engines
    eng = sql_engines.Engine.__new__(sql_engines.Engine)
    eng.limits, eng.service, eng.up = {"datasets": ["sales"]}, "spark-sql", {"name": "Spark SQL (sales lake)", "allowed": ["sales_processed"]}
    eng.names = sql_engines.Names("spark-sql", None, None)
    eng.allow = eng._scope(True)
    eng.max_rows, eng.engine = 10, None
    with pytest.raises(sql_engines.QueryRefused) as exc:
        eng.query("SELECT * FROM sales.orders")
    assert str(exc.value) == ("This step may not read spark_catalog.sales.orders. It may read: nothing. This step's Uses name sales, but "
                              "Spark SQL (sales lake) only allows sales_processed: change the step's data to something within that, "
                              "or ask an admin to allow more.")


def test_live_trino_runs_capped_queries_as_the_connectors_user(run, monkeypatch, tmp_path):
    import trino as trino_client
    from agent_service.runtime import vault
    monkeypatch.setenv("AGENT_SERVICE_VAULT", str(tmp_path / "vault"))
    vault.save("connector-tr", {"password": "pw"})
    seen = {}

    class Cursor:
        description = [("region",), ("n",)]

        def execute(self, sql):
            seen["sql"] = sql

        def fetchall(self):
            return [("East", 3), ("West", 2), ("South", 1)]

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(trino_client.dbapi, "connect", lambda **kw: seen.update(kw) or Conn())
    up = {"connector": "tr", "server": "https://cluster-m.example.com:8443", "user": "agents", "auth": {"kind": "basic"},
          "catalog": "iceberg", "schema": "sales", "allowed": ["iceberg.sales"], "max_seconds": 60}
    t = gateway.connect("trino", limits.mint({"connection": "trino", "actions": ["query"], "datasets": ["iceberg.sales", "hive"], "max_rows": 2,
                                               "source": "live", "upstream": up, "agent": "lake-check"}))
    assert t.wh.allow == ["iceberg.sales"]                                     # hive isn't the connector's
    out = t.query("SELECT region, count(*) AS n FROM orders GROUP BY region")
    assert out["rows"] == [{"region": "East", "n": 3}, {"region": "West", "n": 2}] and out["truncated"] and seen["closed"]
    assert seen["sql"].endswith("SELECT * FROM (SELECT region, COUNT(*) AS n FROM orders GROUP BY region) AS q LIMIT 3")
    assert (seen["host"], seen["port"], seen["http_scheme"], seen["user"], seen["catalog"]) == ("cluster-m.example.com", 8443, "https", "agents", "iceberg")
    assert seen["auth"]._password == "pw" and seen["session_properties"] == {"query_max_run_time": "60s"}


class FakeDataproc:
    """Dataproc and Cloud Storage, as far as Spark SQL uses them: the query file goes up, the job or batch runs (after
    one PENDING check), and its JSON rows are there to read."""

    def __init__(self, end="SUCCEEDED", rows=None):
        self.end, self.rows, self.calls, self.files, self.checks = end, rows or [], [], {}, 0

    def post(self, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, params, json))
        if "/upload/" in url:
            self.files[params["name"]] = data
            return _Resp(200, {"name": params["name"]})
        if url.endswith("/batches"):
            return _Resp(200, {"name": "projects/demo-project/regions/us-central1/operations/op-1"})
        if url.endswith("/jobs:submit"):
            return _Resp(200, {"reference": json["job"]["reference"]})
        return _Resp(200, {})

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params, None))
        if "/batches/" in url or "/jobs/" in url:
            self.checks += 1
            done = self.checks > 1
            if "/jobs/" in url:
                state = {"SUCCEEDED": "DONE", "FAILED": "ERROR"}.get(self.end, self.end) if done else "RUNNING"
                return _Resp(200, {"status": {"state": state, "details": "Table or view not found: sales.nope"}})
            return _Resp(200, {"state": self.end if done else "PENDING", "stateMessage": "Table or view not found: sales.nope"})
        if url.endswith("/o"):
            return _Resp(200, {"items": [{"name": params["prefix"] + "_SUCCESS"}, {"name": params["prefix"] + "part-00000.json"}]})
        r = _Resp(200)
        r.text = "".join(__import__("json").dumps(x) + "\n" for x in self.rows)
        return r


def test_live_spark_sql_runs_as_a_serverless_batch_or_a_cluster_job(run, monkeypatch):
    import google.auth.transport.requests as gtr
    from agent_service.runtime import bigquery_api, sql_engines
    monkeypatch.setattr(bigquery_api, "credentials", lambda up, scopes=None: object())
    monkeypatch.setattr(sql_engines.time, "sleep", lambda s: None)
    fake = FakeDataproc(rows=[{"region": "East", "n": 3}, {"region": "West", "n": 2}])
    monkeypatch.setattr(gtr, "AuthorizedSession", lambda creds: fake)
    up = {"connector": "dp", "project": "demo-project", "region": "us-central1", "staging": "gs://stage-bkt/agent-sql", "mode": "serverless",
          "service_account": "spark@demo-project.iam.gserviceaccount.com", "subnetwork": "agent-orchestrator-us-central1",
          "properties": {"spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog"}, "allowed": ["sales"],
          "jars": ["gs://spark-lib/iceberg/iceberg-spark-runtime-3.5_2.13-1.6.1.jar"],
          "setup": ["CREATE DATABASE IF NOT EXISTS sales;", "CALL spark_catalog.system.register_table(table => 'sales.orders', metadata_file => 'gs://b/m.json')"]}
    spark = gateway.connect("spark-sql", limits.mint({"connection": "spark-sql", "actions": ["query"], "max_rows": 1, "source": "live",
                                                       "upstream": up, "agent": "lake-check"}))
    out = spark.query("SELECT region, count(*) AS n FROM sales.orders GROUP BY region")
    assert out["rows"] == [{"region": "East", "n": 3}] and out["truncated"]
    [(name, script)] = fake.files.items()
    assert name.startswith("agent-sql/ao-") and name.endswith("/query.sql")
    assert script.decode() == ("CREATE DATABASE IF NOT EXISTS sales;\n"
                               "CALL spark_catalog.system.register_table(table => 'sales.orders', metadata_file => 'gs://b/m.json');\n"
                               f"INSERT OVERWRITE DIRECTORY 'gs://stage-bkt/{name[:-len('/query.sql')]}/rows' USING json\n"
                               "SELECT * FROM (SELECT region, COUNT(*) AS n FROM sales.orders GROUP BY region) AS q LIMIT 2;\n")
    batch = next(c for c in fake.calls if c[1].endswith("/locations/us-central1/batches"))
    assert batch[3]["sparkSqlBatch"]["queryFileUri"] == f"gs://stage-bkt/{name}" and batch[3]["labels"]["agent"] == "lake-check"
    assert batch[3]["sparkSqlBatch"]["jarFileUris"] == ["gs://spark-lib/iceberg/iceberg-spark-runtime-3.5_2.13-1.6.1.jar"]
    assert batch[3]["environmentConfig"]["executionConfig"] == {"serviceAccount": "spark@demo-project.iam.gserviceaccount.com",
                                                                "subnetworkUri": "agent-orchestrator-us-central1"}
    assert batch[3]["runtimeConfig"]["properties"] == {"spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog"}
    fake.__init__(end="FAILED")
    with pytest.raises(gateway.Refused, match="Spark SQL failed: Table or view not found"):
        spark.query("SELECT * FROM sales.nope")
    fake.__init__(rows=[{"ok": 1}])
    cluster = gateway.connect("spark-sql", limits.mint({"connection": "spark-sql", "actions": ["query"], "source": "live",
                                                         "upstream": {**up, "mode": "cluster", "cluster": "etl-cluster"}}))
    assert cluster.query("SELECT 1 AS ok FROM sales.orders")["rows"] == [{"ok": 1}]
    job = next(c for c in fake.calls if c[1].endswith("/regions/us-central1/jobs:submit"))
    assert job[3]["job"]["placement"] == {"clusterName": "etl-cluster"} and job[3]["job"]["sparkSqlJob"]["queryFileUri"].endswith("/query.sql")
    fake.__init__(end="RUNNING")
    slow = gateway.connect("spark-sql", limits.mint({"connection": "spark-sql", "actions": ["query"], "source": "live",
                                                      "upstream": {**up, "max_seconds": 1}}))
    monkeypatch.setattr(sql_engines.time, "time", iter(range(0, 10_000, 5)).__next__)
    with pytest.raises(gateway.Refused, match="longer than 1 seconds, so it was cancelled"):
        slow.query("SELECT 1 AS ok FROM sales.orders")
    assert fake.calls[-1][1] == "https://dataproc.googleapis.com/v1/projects/demo-project/regions/us-central1/operations/op-1:cancel"
    with pytest.raises(limits.LimitsError, match="Fill in a staging folder"):
        gateway.connect("spark-sql", limits.mint({"connection": "spark-sql", "actions": ["query"], "source": "live",
                                                   "upstream": {"project": "p", "region": "r"}}))
