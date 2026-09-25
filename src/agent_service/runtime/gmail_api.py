"""Real Gmail, read-only (gmail.readonly), for runs on real accounts. The gateway decides what a step
may see; this only talks to the Gmail API. Email bodies are cleaned the way travel-sync does it:
HTML stripped, tracking links and filler characters removed, and very long bodies cut with a note.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from . import vault

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MAX_BODY_CHARS = 30_000
RETRIES = 5


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "td"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


_URL = re.compile(r"\[\]\([^)]*\)|https?://\S+")
_INVISIBLE = re.compile("[͏­​-‏⁠﻿]")


def clean_body(text: str, is_html: bool = False) -> str:
    if is_html:
        parser = _Text()
        parser.feed(text)
        text = "".join(parser.parts)
    text = _INVISIBLE.sub("", _URL.sub("", text))
    text = re.sub(r"[ \t|]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + f"\n[TRUNCATED: body exceeded {MAX_BODY_CHARS} characters]"
    return text


def credentials(connection: str):
    """The connection's token from the vault, refreshed (and saved back) if it has expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = vault.load(connection)
    if token is None:
        raise PermissionError(f"The connection {connection!r} isn't signed in to Google.")
    creds = Credentials.from_authorized_user_info(token, SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        vault.save(connection, {**token, **__import__("json").loads(creds.to_json())})
    return creds


class LiveGmail:
    """The same two operations the sample mailbox offers, against the real account."""

    def __init__(self, connection: str):
        from googleapiclient.discovery import build
        self.svc = build("gmail", "v1", credentials=credentials(connection), cache_discovery=False)

    @staticmethod
    def _summary(meta: dict[str, Any]) -> dict[str, Any]:
        h = {x["name"].lower(): x["value"] for x in meta.get("payload", {}).get("headers", [])}
        when = datetime.fromtimestamp(int(meta.get("internalDate", 0)) / 1000, timezone.utc).isoformat()
        return {"id": meta["id"], "from": h.get("from", ""), "subject": h.get("subject", ""), "date": when, "snippet": meta.get("snippet", "")}

    def search(self, domains: list[str] | None, keywords: list[str], newer_than_days: int | None, limit: int = 50) -> list[dict[str, Any]]:
        q = []
        if domains:
            q.append("(" + " OR ".join(f"from:{d}" for d in domains) + ")")
        if newer_than_days:
            q.append(f"newer_than:{newer_than_days}d")
        if keywords:
            q.append("(" + " OR ".join(f'"{k}"' if " " in k else k for k in keywords) + ")")
        resp = self.svc.users().messages().list(userId="me", q=" ".join(q), maxResults=limit).execute(num_retries=RETRIES)
        out = []
        for ref in resp.get("messages", []):
            meta = self.svc.users().messages().get(userId="me", id=ref["id"], format="metadata",
                                                   metadataHeaders=["From", "Subject"]).execute(num_retries=RETRIES)
            out.append(self._summary(meta))
        return out

    def email(self, message_id: str) -> dict[str, Any] | None:
        try:
            msg = self.svc.users().messages().get(userId="me", id=message_id, format="full").execute(num_retries=RETRIES)
        except Exception:
            return None
        plain, html = [], []

        def walk(part: dict[str, Any]) -> None:
            data = part.get("body", {}).get("data")
            if data:
                text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                (plain if part.get("mimeType") == "text/plain" else html if part.get("mimeType") == "text/html" else []).append(text)
            for child in part.get("parts", []):
                walk(child)

        walk(msg["payload"])
        body = clean_body("\n".join(plain)) if plain else clean_body("\n".join(html), is_html=True)
        return {**self._summary(msg), "body": body}

    def profile(self) -> str:
        return self.svc.users().getProfile(userId="me").execute(num_retries=RETRIES)["emailAddress"]
