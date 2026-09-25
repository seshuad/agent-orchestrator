"""Sample accounts for test runs: a mailbox, spreadsheets and a calendar, as JSON files.

    <AGENT_SERVICE_SAMPLE_DATA>/emails.json          [{id, from, date, subject, body}]
    <AGENT_SERVICE_SAMPLE_DATA>/sheets/<Sheet>.json  [{column: value}]
    <AGENT_SERVICE_SAMPLE_DATA>/github.json          {issues: [{repo, number, kind, title, ..., body, comments}], files: {repo: {path: text}}}
    <run dir>/calendar.json                          events created during this run

Nothing here touches a real account. The calendar is written inside the run directory, so
every run starts from an empty calendar.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .runstate import run_dir

SAMPLE_DATA_ENV = "AGENT_SERVICE_SAMPLE_DATA"


def _root() -> Path:
    d = os.environ.get(SAMPLE_DATA_ENV)
    if not d:
        raise RuntimeError(f"{SAMPLE_DATA_ENV} is not set; test runs need sample data.")
    return Path(d)


def emails() -> list[dict[str, Any]]:
    return json.loads((_root() / "emails.json").read_text())


def domain_of(sender: str) -> str:
    return sender.rsplit("@", 1)[-1].strip(" >").lower()


def sender_matches(sender: str, domains: list[str]) -> bool:
    d = domain_of(sender)
    return any(d == x or d.endswith("." + x) for x in domains)


def search(domains: list[str] | None, keywords: list[str], newer_than_days: int | None) -> list[dict[str, Any]]:
    """Emails from the given sender domains (None: any sender), newest first."""
    since = datetime.now(timezone.utc) - timedelta(days=newer_than_days) if newer_than_days else None
    hits = []
    for e in emails():
        if domains is not None and not sender_matches(e["from"], domains):
            continue
        if since and datetime.fromisoformat(e["date"]) < since:
            continue
        text = f'{e["subject"]}\n{e["body"]}'.lower()
        if keywords and not any(k.lower() in text for k in keywords):
            continue
        hits.append(e)
    return sorted(hits, key=lambda e: e["date"], reverse=True)


def email(message_id: str) -> dict[str, Any] | None:
    return next((e for e in emails() if e["id"] == message_id), None)


def sheet(name: str) -> list[dict[str, Any]]:
    path = _root() / "sheets" / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else []


def append_row(name: str, row: dict[str, Any]) -> int:
    """Rows written during a run go to the run directory, never back into the sample data."""
    path = run_dir() / "sheets" / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    rows = json.loads(path.read_text()) if path.exists() else []
    rows.append(row)
    path.write_text(json.dumps(rows, indent=1))
    return len(rows)


def calendar_events() -> list[dict[str, Any]]:
    path = run_dir() / "calendar.json"
    return json.loads(path.read_text()) if path.exists() else []


def add_calendar_event(event: dict[str, Any]) -> None:
    events = calendar_events() + [event]
    (run_dir() / "calendar.json").write_text(json.dumps(events, indent=1))


# ------------------------------------------------------------------ GitHub

def _github() -> dict[str, Any]:
    path = _root() / "github.json"
    return json.loads(path.read_text()) if path.exists() else {"issues": [], "files": {}}


def _summary(it: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in it.items() if k not in ("body", "comments")}


def github_search(repos: list[str], keywords: list[str], newer_than_days: int | None, state: str | None = None,
                  label: str | None = None) -> list[dict[str, Any]]:
    """Issues and pull requests in the given repositories, most recently updated first. Keywords match the
    title, body and comments (as on GitHub); labels only through `label`."""
    since = datetime.now(timezone.utc) - timedelta(days=newer_than_days) if newer_than_days else None
    hits = []
    for it in _github()["issues"]:
        if it["repo"] not in repos or (state and it["state"] != state):
            continue
        if label and label.lower() not in [x.lower() for x in it.get("labels", [])]:
            continue
        if since and datetime.fromisoformat(it["updated_at"].replace("Z", "+00:00")) < since:
            continue
        text = " ".join([it["title"], it.get("body", "")] + [c["body"] for c in it.get("comments", [])]).lower()
        if keywords and not any(k.lower() in text for k in keywords):
            continue
        hits.append(_summary(it))
    return sorted(hits, key=lambda it: it["updated_at"], reverse=True)


def github_issue(repo: str, number: int) -> dict[str, Any] | None:
    return next((it for it in _github()["issues"] if it["repo"] == repo and it["number"] == number), None)


def github_file(repo: str, path: str) -> str | None:
    return _github().get("files", {}).get(repo, {}).get(path)
