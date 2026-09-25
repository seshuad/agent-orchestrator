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
        assert connection == "seshu-github"

    def search(self, repos, keywords, days, state=None, label=None):
        return [{"repo": "seshuad/agent-orchestrator", "number": 1, "kind": "issue", "title": "t", "state": "open", "author": "a",
                 "labels": [], "created_at": "2026-09-24T00:00:00Z", "updated_at": "2026-09-24T00:00:00Z"},
                {"repo": "someone/else", "number": 2, "kind": "issue", "title": "x", "state": "open", "author": "a",
                 "labels": [], "created_at": "2026-09-24T00:00:00Z", "updated_at": "2026-09-24T00:00:00Z"}]


def test_live_github_keeps_the_same_limits(run, monkeypatch):
    from agent_service.runtime import github_api
    monkeypatch.setattr(github_api, "LiveGitHub", FakeGitHub)
    gh = github(repos=["seshuad/agent-orchestrator"], source="live", account="seshu-github")
    assert [it["repo"] for it in gateway.call(gh, "github", "search", {"keywords": []})] == ["seshuad/agent-orchestrator"]
