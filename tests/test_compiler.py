"""The agent format and the compiler, and whole runs in real Conductor with scripted model steps."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_service import definition
from agent_service.cli import _header
from agent_service.compiler import CompileError, compile_agent

ROOT = Path(__file__).parent.parent
AGENTS = {
    "invoice-check": ROOT / "examples/invoice-check/invoice-check.agent.yaml",
    "travel-sync": ROOT / "examples/travel-sync-free/travel-sync-free.agent.yaml",
}
needs_conductor = pytest.mark.skipif(shutil.which("conductor") is None, reason="conductor is not installed")


def raw(name: str) -> dict:
    return yaml.safe_load(AGENTS[name].read_text())


@pytest.mark.parametrize("name", AGENTS)
def test_checked_in_yaml_is_what_the_compiler_writes(name):
    """The compiled workflows in examples/ must not drift from their definitions."""
    path = AGENTS[name]
    compiled = compile_agent(definition.load(path)).yaml(_header(str(path.relative_to(ROOT))))
    assert path.with_name(path.name.replace(".agent.yaml", ".yaml")).read_text() == compiled


@needs_conductor
@pytest.mark.parametrize("name", AGENTS)
def test_conductor_accepts_the_compiled_workflow(name, tmp_path):
    compiled = compile_agent(definition.load(AGENTS[name]))
    wf = tmp_path / "workflow.yaml"
    wf.write_text(compiled.yaml())
    env = {**os.environ, **{v: "x" for v in compiled.limits},
           **{v: "x" for v in ("AGENT_SERVICE_RUN_DIR", "AGENT_SERVICE_SAMPLE_DATA", "AGENT_SERVICE_SIGNING_KEY")}}
    out = subprocess.run(["conductor", "validate", str(wf)], env=env, capture_output=True, text=True)
    assert "Validation Successful" in out.stdout, out.stdout + out.stderr


def test_one_limits_token_per_connection_use():
    limits = compile_agent(definition.load(AGENTS["invoice-check"])).limits
    assert limits["LIMITS_GMAIL_READ_INVOICE"] == {"connection": "gmail", "actions": ["open"], "only_message": "$input.email_id"}
    assert limits["LIMITS_GMAIL_SEARCH_VENDOR_EMAILS"]["from_domain"] == "$input.sender_domain"
    assert limits["LIMITS_SHEETS_ADD_TO_PAYMENT_QUEUE"]["actions"] == ["append_row"]


def test_ask_steps_cannot_be_given_write_actions():
    data = raw("invoice-check")
    data["steps"][0]["steps"][0]["uses"]["actions"] = ["open", "send"]
    with pytest.raises(ValidationError, match="Ask steps can only read"):
        definition.Agent.model_validate(data)


def test_the_first_approval_choice_must_change_nothing():
    data = raw("invoice-check")
    data["steps"][1]["choices"].reverse()
    with pytest.raises(ValidationError, match="first choice must pass nothing"):
        definition.Agent.model_validate(data)


def test_models_the_engine_cannot_drive_are_caught_at_compile_time():
    data = raw("travel-sync")
    data["steps"][0]["planning_model"] = "claude-opus-5-5"
    with pytest.raises(CompileError, match="forces a tool call"):
        compile_agent(definition.Agent.model_validate(data))


def test_unknown_references_are_caught_at_compile_time():
    data = raw("travel-sync")
    data["steps"][3]["takes"]["records"] = "approve_trip.approved[*].bookings"
    with pytest.raises(CompileError, match="approve_trip"):
        compile_agent(definition.Agent.model_validate(data))


# ------------------------------------------------------------------ whole runs, scripted model steps

def run_agent(name: str, tmp_path: Path, *args: str) -> tuple[Path, list[str]]:
    out = subprocess.run(["agent-service", "run", str(AGENTS[name]), *args], cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    run = Path(re.search(r"Run directory: (\S+)", out.stdout).group(1))
    events = sorted(Path(os.environ.get("TMPDIR", "/tmp"), "conductor").glob(f"conductor-{name}-*.events.jsonl"),
                    key=lambda p: p.stat().st_mtime)[-1]
    path = [json.loads(l)["data"].get("agent_name") for l in events.read_text().splitlines()
            if json.loads(l)["type"].endswith("_completed") and json.loads(l)["data"].get("agent_name")]
    return run, path


@needs_conductor
def test_invoice_check_northwind(tmp_path):
    run, path = run_agent("invoice-check", tmp_path, "--sample-data", str(ROOT / "examples/invoice-check/sample-data"),
                          "--email-id", "inv-northwind-2208", "--replay", str(ROOT / "examples/invoice-check/replay-northwind.yaml"))
    assert path.count("not_ready") == 1                    # three-way match asked for before a purchase order existed
    assert path.count("finish_check") == 2                 # finishing as "matched" too early was refused
    assert "search_vendor_emails > find_purchase_order > find_receipts" in " > ".join(path)
    finish = json.loads((run / "steps/finish_check.json").read_text())
    assert finish["passed"] and finish["results"]["outcome"] == "amounts differ" and finish["results"]["po_number"] == "PO-5531"
    assert path[-2:] == ["approve_payment", "add_to_payment_queue"]
    assert not (run / "sheets").exists()                   # dry run: nothing added to the Payment queue


@needs_conductor
def test_travel_sync_on_the_sample_emails(tmp_path):
    run, path = run_agent("travel-sync", tmp_path, "--sample-data", str(ROOT / "examples/travel-sync-free/sample-data"),
                          "--replay", str(ROOT / "examples/travel-sync-free/replay-sample.yaml"))
    assert path.count("tidy_up") == 4                      # re-ran by itself after every reader
    assert path.count("finish_check") == 2                 # finishing before double-checking was refused
    assert json.loads((run / "steps/approve_trips_preselect.json").read_text())["results"]["preselected"] == ["b0bde9c185"]
    events = json.loads((run / "steps/add_to_calendar.json").read_text())
    assert events["would_create"] == ["Hotel: Courtyard Chicago Downtown/River North (2026-10-20T16:00:00-05:00 to 2026-10-22T12:00:00-05:00)"]
    assert not (run / "calendar.json").exists()            # dry run: no events created


def test_gmail_servers_get_the_vault_for_real_accounts():
    wf = compile_agent(definition.load(AGENTS["travel-sync"])).workflow
    env = wf["workflow"]["runtime"]["mcp_servers"]["gmail-read-airline"]["env"]
    assert env["AGENT_SERVICE_VAULT"] == "${AGENT_SERVICE_VAULT:-}"      # empty for sample runs


def test_an_empty_look_up_is_an_error_when_saved():
    data = raw("invoice-check")
    data["steps"][0]["steps"][2]["operation"]["lookup"]["sheet"] = ""
    with pytest.raises(CompileError, match="pick the sheet"):
        compile_agent(definition.Agent.model_validate(data))
