"""Sending email through an SMTP server: a company mail relay, or Microsoft 365's (smtp.office365.com).

    SmtpSender(upstream).send(to, cc, subject, body, images) -> the message's Message-ID

The Microsoft 365 connector's admin sets the server (host, port, how it's secured), the account it signs in as, if
any, and the From address; the password is in the vault. Many company relays take mail from inside the network with
no sign-in. What a step may send, and to whom, is checked by the gateway before this is called (gateway.Smtp).
"""

from __future__ import annotations

import smtplib
import ssl
from typing import Any

from .gmail_api import compose

TIMEOUT = 30            # seconds; a relay that doesn't answer fails the step rather than hold up the run


def _password(up: dict[str, Any]) -> str | None:
    from . import vault
    return (vault.load(f"connector-{up.get('connector')}") or {}).get("smtp_password")


def settings(up: dict[str, Any]) -> dict[str, Any]:
    st = up.get("smtp") or {}
    if not (st.get("host") or "").strip():
        raise ConnectionError("The Microsoft 365 connector has no SMTP server yet: an admin sets it under Connectors.")
    if not (st.get("from_address") or "").strip():
        raise ConnectionError("The Microsoft 365 connector has no From address for email yet: an admin sets it under Connectors.")
    security = st.get("security") or "starttls"
    port = int(st.get("port") or {"ssl": 465, "starttls": 587}.get(security, 25))
    return {**st, "host": st["host"].strip(), "port": port, "security": security}


def connect(up: dict[str, Any]) -> smtplib.SMTP:
    """An open, signed-in connection to the server, secured as the admin set it."""
    st = settings(up)
    context = ssl.create_default_context()
    if st["security"] == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(st["host"], st["port"], timeout=TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(st["host"], st["port"], timeout=TIMEOUT)
        server.ehlo()
        if st["security"] == "starttls":
            server.starttls(context=context)
            server.ehlo()
    if (st.get("username") or "").strip():
        password = _password(up)
        if not password:
            server.quit()
            raise ConnectionError("The SMTP server signs in, but there's no password in the vault: an admin adds it under Connectors.")
        server.login(st["username"].strip(), password)
    return server


class SmtpSender:
    def __init__(self, up: dict[str, Any]):
        self.up = up
        self.from_address = settings(up)["from_address"].strip()

    def send(self, to: list[str], cc: list[str], subject: str, body: str, images: list[Any] | None = None) -> str:
        from email.utils import make_msgid
        msg = compose(to, cc, subject, body, images)
        msg["From"] = self.from_address
        msg["Message-ID"] = make_msgid(domain=self.from_address.rsplit("@", 1)[-1])
        server = connect(self.up)
        try:
            refused = server.send_message(msg, to_addrs=[*to, *cc])
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass
        if refused:
            raise ConnectionError(f"The SMTP server refused {', '.join(refused)}.")
        return msg["Message-ID"]


def check(up: dict[str, Any]) -> str:
    """For the connector's Test: connects, signs in if set, and says so. Sends nothing."""
    st = settings(up)
    server = connect(up)
    try:
        server.noop()
    finally:
        server.quit()
    how = {"ssl": "TLS", "starttls": "STARTTLS", "none": "no encryption"}.get(st["security"], st["security"])
    who = f", signed in as {st['username'].strip()}" if (st.get("username") or "").strip() else ", no sign-in"
    return f"Reached {st['host']}:{st['port']} ({how}{who}); email goes from {st['from_address'].strip()}."
