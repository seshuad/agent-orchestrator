"""Signing in to an MCP server with OAuth, from the browser.

The MCP client library runs the whole flow (metadata discovery, client registration, PKCE, token
exchange) as one coroutine that stops twice: to send the user to the server's sign-in page, and to
wait for the code the server sends back. Here that coroutine runs on its own thread:

    start(...)   runs the flow until it has the sign-in URL, and returns it for the browser
    finish(...)  hands it the code from the designer's sign-in return, and waits for the tokens

Tokens go to the vault under `key`: an account's id, or `connector-<id>-admin` for the admin's own
sign-in that lists the server's tools.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..runtime import upstream


class _Flow:
    def __init__(self) -> None:
        self.url: str | None = None
        self.url_ready = threading.Event()
        self.code: tuple[str, str | None] | None = None
        self.code_ready = threading.Event()
        self.done = threading.Event()
        self.error: str | None = None
        self.tools: list[dict[str, Any]] = []


class Pending:
    """MCP sign-ins in progress, by OAuth state. Each is single use and expires after ten minutes."""

    def __init__(self) -> None:
        self.flows: dict[str, tuple[_Flow, str, str, float]] = {}   # state -> (flow, key, what it's for, expires)

    def start(self, server: dict[str, Any], key: str, origin: str, vault_dir: Path, purpose: str) -> str:
        flow = _Flow()
        redirect = origin.rstrip("/") + "/"

        async def redirect_handler(url: str) -> None:
            flow.url = url
            flow.url_ready.set()

        async def callback_handler():
            while not flow.code_ready.is_set():
                if flow.done.is_set():
                    raise TimeoutError("sign-in abandoned")
                await asyncio.sleep(0.2)
            return flow.code

        def run() -> None:
            provider = upstream.oauth_provider(server["url"], key, redirect, redirect_handler, callback_handler, vault_dir)
            try:
                flow.tools = asyncio.run(upstream.list_tools_async(server, {"kind": "oauth"}, oauth=provider))
            except Exception as exc:
                flow.error = str(exc)
            finally:
                flow.done.set()
                flow.url_ready.set()

        threading.Thread(target=run, daemon=True).start()
        flow.url_ready.wait(30)
        if flow.url is None:
            raise ValueError(flow.error or "The server didn't offer an OAuth sign-in.")
        state = parse_qs(urlparse(flow.url).query).get("state", [""])[0]
        self.flows = {k: v for k, v in self.flows.items() if v[3] > time.time()}
        self.flows[state] = (flow, key, purpose, time.time() + 600)
        return flow.url

    def owns(self, state: str) -> bool:
        return state in self.flows

    def finish(self, state: str, code: str) -> tuple[str, str, list[dict[str, Any]]]:
        """Returns (vault key, purpose, the tools the server listed once signed in)."""
        item = self.flows.pop(state, None)
        if item is None or item[3] < time.time():
            raise ValueError("That sign-in link has expired or was already used. Start again from Connections.")
        flow, key, purpose, _ = item
        flow.code = (code, state)
        flow.code_ready.set()
        flow.done.wait(60)
        if flow.error:
            raise ValueError(f"The sign-in didn't finish: {flow.error}")
        return key, purpose, flow.tools
