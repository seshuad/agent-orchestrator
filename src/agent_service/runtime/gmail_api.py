"""Real Gmail for runs on real accounts: reading (gmail.readonly) and, for accounts granted it, sending (gmail.send). The gateway decides what a step
may see; this only talks to the Gmail API. Email bodies are cleaned the way travel-sync does it:
HTML stripped, tracking links and filler characters removed, and very long bodies cut with a note.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from . import vault

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPE_OF = {"read": "https://www.googleapis.com/auth/gmail.readonly", "send": "https://www.googleapis.com/auth/gmail.send"}


def scopes_for(permissions: list[str]) -> list[str]:
    """What a sign-in asks Google for: only what the account's permissions need."""
    return [SCOPE_OF[p] for p in permissions if p in SCOPE_OF] or SCOPES
MAX_BODY_CHARS = 30_000
RETRIES = 2
HTTP_TIMEOUT = 30          # seconds per request: a stuck request fails, and the step can try again or carry on


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
    creds = Credentials.from_authorized_user_info(token, token.get("scopes") or SCOPES)    # what it was granted
    if not creds.valid:
        creds.refresh(Request())
        vault.save(connection, {**token, **__import__("json").loads(creds.to_json())})
    return creds


def html_body(text: str, cids: list[str]) -> str:
    """Plain text as simple HTML (paragraphs, "- " lines as a list), then each image."""
    import html
    out, items = [], []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        for line in lines:
            if line.startswith("- "):
                items.append(f"<li>{html.escape(line[2:])}</li>")
                continue
            if items:
                out.append("<ul>" + "".join(items) + "</ul>")
                items = []
            out.append(f"<p>{html.escape(line)}</p>")
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
    out += [f'<p><img src="cid:{c}" alt="chart" style="max-width:100%;width:600px;height:auto"></p>' for c in cids]
    return '<div style="font-family:Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:#222">' + "".join(out) + "</div>"


def compose(to: list[str], cc: list[str], subject: str, body: str, images: list[Any] | None = None) -> Any:
    """The email, for any way of sending it. With images (PNG files), it goes as HTML with each image inline under the
    text, and the plain text as the alternative."""
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["To"], msg["Subject"] = ", ".join(to), subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(body)
    if images:
        from email.utils import make_msgid
        cids = [make_msgid(domain="agent-service") for _ in images]
        msg.add_alternative(html_body(body, [c[1:-1] for c in cids]), subtype="html")
        html = msg.get_payload()[1]
        for path, cid in zip(images, cids):
            html.add_related(Path(path).read_bytes(), maintype="image", subtype="png", cid=cid,
                             filename=Path(path).name, disposition="inline")
    return msg


class LiveGmail:
    """The same two operations the sample mailbox offers, against the real account.

    A model can ask for several emails at once, and the gateway runs those calls on separate threads. The Google
    client's HTTP layer (httplib2) isn't safe to share between threads, and waits forever by default, so each
    thread gets its own connection, with a timeout."""

    def __init__(self, connection: str):
        import threading
        self.creds = credentials(connection)
        self.local = threading.local()

    @property
    def svc(self) -> Any:
        if not hasattr(self.local, "svc"):
            import httplib2
            from google_auth_httplib2 import AuthorizedHttp
            from googleapiclient.discovery import build
            http = AuthorizedHttp(self.creds, http=httplib2.Http(timeout=HTTP_TIMEOUT))
            self.local.svc = build("gmail", "v1", http=http, cache_discovery=False)
        return self.local.svc

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

    def send(self, to: list[str], cc: list[str], subject: str, body: str, images: list[Any] | None = None) -> str:
        """Send an email from the account; returns Gmail's message id."""
        raw = base64.urlsafe_b64encode(compose(to, cc, subject, body, images).as_bytes()).decode()
        try:
            return self.svc.users().messages().send(userId="me", body={"raw": raw}).execute(num_retries=RETRIES)["id"]
        except Exception as exc:
            if "insufficient" in str(exc).lower() or "403" in str(exc):
                raise PermissionError("This Gmail account wasn't signed in with permission to send. Sign in again on Connections.") from exc
            raise

    def profile(self) -> str:
        return self.svc.users().getProfile(userId="me").execute(num_retries=RETRIES)["emailAddress"]
