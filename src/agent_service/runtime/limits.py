"""Signed limits tokens: what one use of a connection may do in one run.

The control plane mints a token per connection use when a run starts (for example: Gmail,
search and open, only these senders, last 180 days). The gateway verifies the signature and
enforces the limits on every call, so nothing a model sends can widen them.

In the service only the control plane and the gateway hold the signing key. In this prototype
`agent-service run` generates a key per run and passes it to the gateway through the
environment, which a real worker would not see.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

KEY_ENV = "AGENT_SERVICE_SIGNING_KEY"


class LimitsError(Exception):
    """A token is missing, forged, or does not allow what was asked."""


def _key() -> bytes:
    key = os.environ.get(KEY_ENV)
    if not key:
        raise LimitsError(f"{KEY_ENV} is not set, so no limits token can be checked.")
    return key.encode()


def mint(limits: dict[str, Any], key: bytes | None = None) -> str:
    body = base64.urlsafe_b64encode(json.dumps(limits, sort_keys=True).encode()).decode()
    sig = hmac.new(key or _key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify(token: str | None) -> dict[str, Any]:
    if not token:
        raise LimitsError("No limits token: this connection can do nothing.")
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        raise LimitsError("Malformed limits token.") from None
    want = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        raise LimitsError("Limits token signature does not match.")
    return json.loads(base64.urlsafe_b64decode(body))
