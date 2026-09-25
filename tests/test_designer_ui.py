"""The designer in a real browser (Chrome, via Playwright): the paths a builder takes.

Skipped unless the web app is built (cd web && npm run build) and Chrome is installed.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

DIST = Path(__file__).parent.parent / "web" / "dist" / "index.html"
pytestmark = [pytest.mark.skipif(not DIST.exists(), reason="the web app isn't built"),
              pytest.mark.skipif(shutil.which("conductor") is None, reason="conductor is not installed")]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    import uvicorn
    from agent_service.server.app import create_app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path_factory.mktemp("ws")), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(scope="module")
def page(base):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:                 # no Chrome on this machine
            pytest.skip(f"Chrome isn't available: {exc}")
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.errors = errors
        yield pg
        browser.close()


def test_home_lists_the_workspace_agents(page, base):
    page.goto(base + "/")
    page.wait_for_selector("text=travel-sync")
    assert page.locator("text=Northpeak Operations").count() >= 1 and page.locator("text=Seshu Adunuthula").count() >= 1
    assert page.locator("button:has-text('Run now')").count() == 2


def test_editor_shows_errors_and_blocks_publishing(page, base):
    page.goto(base + "/new")
    page.fill("input[aria-label='Name']", "trip-copy")
    page.click("button:has-text('Read, check, approve, act')")
    page.click("button:has-text('Create agent')")
    page.wait_for_selector("text=Ready")
    page.click(".side-row:has-text('Any trips found?')")
    page.fill("textarea[aria-label='CEL rule'] >> nth=0", "size(steps.find_and_check.trips == 0")
    page.wait_for_selector(".field-error", timeout=5000)
    assert page.locator("button:has-text('Publish')").is_disabled()
    page.fill("textarea[aria-label='CEL rule'] >> nth=0", "size(steps.find_and_check.trips) == 0")
    page.wait_for_selector("text=Ready", timeout=5000)
    page.click("button:has-text('Publish')")
    page.click(".dialog button:has-text('Publish')")
    page.wait_for_selector("text=Published v1", timeout=5000)


def test_blank_agent_and_the_add_menu(page, base):
    page.goto(base + "/new")
    page.fill("input[aria-label='Name']", "blank-one")
    page.click("button:has-text('Create agent')")
    page.wait_for_selector("text=Add a step: an agent needs at least one")
    page.click("button[aria-label='Add step']")
    page.click(".menu button:has-text('Free-form')")
    page.click("button:has-text('Add a step to this block')")
    assert page.locator(".menu button:has-text('Approve')").count() == 0      # only Ask and Built-in go inside
    page.click(".menu button:has-text('Ask')")
    page.wait_for_selector(".side-row:has-text('New Ask step')")


def test_run_now_and_approve_in_the_page(page, base):
    page.goto(base + "/")
    page.click("tr:has-text('invoice-check') button:has-text('Run now')")
    page.select_option("select[aria-label='Email']", "inv-northwind-2208")
    page.click("button:has-text('Scripted answers')")
    page.click("button:has-text('Start run')")
    page.wait_for_selector("text=Waiting for your approval", timeout=60000)
    page.click("button:has-text('Queue for payment')")
    page.wait_for_selector(".pill.succeeded >> text=Succeeded", timeout=60000)
    assert page.locator(".log-row:has-text('Refused')").count() >= 1
    assert page.errors == []


def test_typing_a_field_name_keeps_focus(page, base):
    page.goto(base + "/agents/travel-sync")
    page.wait_for_selector("text=Find and check bookings")
    page.click("button[aria-label='Add record type']")
    box = page.locator("input[aria-label='Field name']").first
    box.click()
    box.press("Meta+a")
    page.keyboard.type("leak_started_at", delay=20)          # one key at a time, as a person types
    assert box.input_value() == "leak_started_at"
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Field name"


def test_deleting_an_agent_from_home_and_the_editor(page, base):
    import httpx
    for name in ("delete-me", "delete-me-too"):
        httpx.post(base + "/api/agents", json={"name": name, "start": "read-check-approve-act", "sample_set": "Travel emails"})
    page.goto(base + "/")
    page.click("button[aria-label='Delete delete-me']")
    page.wait_for_selector("[role=dialog][aria-label='Delete delete-me?']")
    page.click("[role=dialog] button:has-text('Delete agent')")
    page.wait_for_selector("button[aria-label='Delete delete-me']", state="detached")
    assert page.locator("a:has-text('delete-me-too')").count() == 1

    page.goto(base + "/agents/delete-me-too")
    page.click("button[aria-label='Delete agent']")
    page.click("[role=dialog] button:has-text('Delete agent')")
    page.wait_for_url(base + "/")
    page.wait_for_selector("text=travel-sync")
    assert page.locator("text=delete-me").count() == 0 and not page.errors


def test_describe_it_drafts_an_agent_with_claude(page, base, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_server import FakeClaude, draft_answer
    from agent_service.server import author
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    step = """  - id: read_leaks
    kind: ask
    name: Read leak alerts
    model: claude-sonnet-5
    uses: {connection: gmail, actions: [search, open], senders: [northpeakwater.com]}
    instructions: Copy each leak exactly.
    task: Extract each leak.
    returns: {leaks: {type: list of Leak}}"""
    monkeypatch.setattr(author.Drafts, "_client", lambda self: FakeClaude([draft_answer(step)]))
    page.goto(base + "/new")
    page.click("button:has-text('Describe it')")
    assert page.input_value("select[aria-label='Test data']") == ""                  # Claude picks unless you do
    page.fill("textarea[aria-label='What should it do?']", "Log every water leak alert email.")
    page.click("button:has-text('Draft with Claude')")
    page.wait_for_url("**/agents/leak-log", timeout=20000)
    page.wait_for_selector("text=Drafted by Claude")
    assert page.locator("text=The Leaks sheet already exists.").count() == 1 and not page.errors


def test_help_writing_instructions_and_task(page, base, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_server import FakeClaude
    from agent_service.server import author
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(author.Drafts, "_client", lambda self: FakeClaude(["<text>Return the invoice in the email.</text>"]))
    page.goto(base + "/agents/invoice-check")
    page.click(".side-row:has-text('Read invoice')")
    page.click("button[aria-label='Help with instructions']")
    template = page.locator(".help-template").inner_text()
    assert "an Invoice (vendor, invoice_number" in template and "the email that started the run" in template
    before = page.input_value("textarea[aria-label='Task']")
    page.click("button[aria-label='Write task with AI']")
    page.wait_for_selector("text=Written by Claude")
    assert page.input_value("textarea[aria-label='Task']") == "Return the invoice in the email."
    page.click("button:has-text('Undo')")
    assert page.input_value("textarea[aria-label='Task']") == before and not page.errors
