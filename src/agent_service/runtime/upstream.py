"""Talking to a connector's own MCP server (an "upstream"): list its tools, call one.

    server  {"transport": "url", "url": ...}  or  {"transport": "command", "command": ..., "args": [...]}
    auth    {"kind": "none" | "bearer" | "header" | "oauth", "header": "X-Api-Key"}

Credentials come from the vault, never from the caller: a shared token for the connector
(`connector-<id>`: {"token": ...}), or a builder's OAuth tokens for one account (`<account id>`).
Only the gateway and the designer's admin pages use this module; agents reach an upstream through
the gateway, which checks every call against the step's limits first.

Tools are pinned: `pin(tool)` hashes a tool's name, description and input schema. An admin
approves tools with their pins; if the server later changes a description (a way to slip new
instructions to the model), the pin no longer matches and the gateway refuses the tool.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from . import vault


class UpstreamError(Exception):
    """The server couldn't be reached, refused the credentials, or failed the call."""


def pin(tool: dict[str, Any]) -> str:
    body = json.dumps({"name": tool["name"], "description": tool.get("description") or "",
                       "input_schema": tool.get("input_schema") or {}}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def limitable(schema: dict[str, Any]) -> list[str]:
    """Top-level arguments a step can restrict to a list of values: strings, numbers, and lists of them."""
    out = []
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type")
        items = (prop.get("items") or {}).get("type") if kind == "array" else None
        if kind in ("string", "integer", "number") or items in ("string", "integer", "number") or "enum" in prop:
            out.append(name)
    return out


# ------------------------------------------------------------------ credentials

class VaultTokens:
    """OAuth token storage for the MCP client, kept in the vault under one key."""

    def __init__(self, key: str, vault_dir=None):
        self.key, self.vault_dir = key, vault_dir

    def _load(self) -> dict[str, Any]:
        return vault.load(self.key, self.vault_dir) or {}

    def _save(self, data: dict[str, Any]) -> None:
        vault.save(self.key, data, self.vault_dir)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        t = self._load().get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens) -> None:
        self._save({**self._load(), "tokens": tokens.model_dump(mode="json", exclude_none=True)})

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        c = self._load().get("client")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, info) -> None:
        self._save({**self._load(), "client": info.model_dump(mode="json", exclude_none=True)})


def oauth_provider(url: str, key: str, redirect_uri: str | None = None, redirect_handler=None, callback_handler=None, vault_dir=None):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    meta = OAuthClientMetadata(client_name="Agent Orchestrator", redirect_uris=[redirect_uri or "http://127.0.0.1/unused"],
                               grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                               token_endpoint_auth_method="none")
    return OAuthClientProvider(url, meta, VaultTokens(key, vault_dir), redirect_handler, callback_handler)


def _headers(auth: dict[str, Any], secret: str | None) -> dict[str, str]:
    kind = auth.get("kind", "none")
    if kind == "bearer" and secret:
        return {"Authorization": f"Bearer {secret}"}
    if kind == "header" and secret:
        return {auth.get("header") or "X-Api-Key": secret}
    return {}


@asynccontextmanager
async def session(server: dict[str, Any], auth: dict[str, Any], *, secret: str | None = None, oauth_key: str | None = None,
                  oauth=None, vault_dir=None) -> AsyncIterator[Any]:
    """An initialized MCP client session with the upstream. `oauth` overrides the provider (for sign-in)."""
    from mcp import ClientSession
    inside = False          # errors from the caller's own code pass through; only connecting is this module's to explain
    try:
        if server.get("transport") == "command":
            from mcp.client.stdio import StdioServerParameters, stdio_client
            params = StdioServerParameters(command=server["command"], args=list(server.get("args") or []),
                                           env={**(server.get("env") or {}), **({"UPSTREAM_TOKEN": secret} if secret else {})} or None)
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                inside = True
                yield s
            return
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        provider = oauth or (oauth_provider(server["url"], oauth_key, vault_dir=vault_dir)
                             if auth.get("kind") == "oauth" and oauth_key else None)
        async with httpx2.AsyncClient(headers=_headers(auth, secret), auth=provider, timeout=httpx2.Timeout(30, read=120),
                                      follow_redirects=True) as http:
            async with streamable_http_client(server["url"], http_client=http) as streams, ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                inside = True
                yield s
    except UpstreamError:
        raise
    except BaseException as exc:    # an ExceptionGroup from the transport's task group, usually
        leaf = _leaf(exc)
        if inside:
            raise leaf from None
        if not isinstance(leaf, Exception):
            raise
        raise UpstreamError(_reason(leaf)) from None


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _reason(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = str(exc) or type(exc).__name__
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if text == "Server returned an error response":
        text = "The server refused the request: check the URL and the credentials"
    return f"{status}: {text}" if status and str(status) not in text else text


def tool_dict(t: Any) -> dict[str, Any]:
    return {"name": t.name, "description": t.description or "", "input_schema": t.input_schema or {}}


async def list_tools_async(server, auth, **kw) -> list[dict[str, Any]]:
    async with session(server, auth, **kw) as s:
        return [tool_dict(t) for t in (await s.list_tools()).tools]


def list_tools(server: dict[str, Any], auth: dict[str, Any], **kw) -> list[dict[str, Any]]:
    return asyncio.run(list_tools_async(server, auth, **kw))


def result_text(result: Any) -> str:
    """A tool result as text for the model: its text blocks, or its structured content as JSON."""
    parts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
    if not parts and getattr(result, "structured_content", None) is not None:
        parts = [json.dumps(result.structured_content, ensure_ascii=False)]
    return "\n".join(parts)
