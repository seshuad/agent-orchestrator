"""Real GitHub, read-only, for runs on real accounts. The gateway decides what a step may see
(which repositories, how far back); this only talks to GitHub's REST API.

The credential is a fine-grained personal access token with read-only access, kept in the vault as
{"token": ...}. Issue and pull request text is written by other people: it is data, not instructions.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from . import vault

API = "https://api.github.com"
MAX_BODY_CHARS = 20_000
MAX_COMMENTS = 30


class GitHubError(Exception):
    """GitHub refused or failed the request; the message says why."""


def _get(token: str, path: str, params: dict[str, Any] | None = None, api: str = API) -> Any:
    url = api.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "agent-orchestrator"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read()).get("message", "")
        except Exception:
            pass
        raise GitHubError(f"GitHub said {exc.code}{': ' + detail if detail else ''}") from None
    except urllib.error.URLError as exc:
        raise GitHubError(f"Couldn't reach GitHub: {exc.reason}") from None


def whoami(token: str, api: str = API) -> str:
    """The login the token belongs to. Used to check a token when it's added."""
    return _get(token, "/user", api=api)["login"]


def _cut(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + f"\n[TRUNCATED: longer than {MAX_BODY_CHARS} characters]"


def _item(repo: str, it: dict[str, Any]) -> dict[str, Any]:
    return {"repo": repo, "number": it["number"], "kind": "pull request" if it.get("pull_request") else "issue",
            "title": it["title"], "state": it["state"], "author": (it.get("user") or {}).get("login", ""),
            "labels": [x["name"] for x in it.get("labels", [])], "created_at": it["created_at"], "updated_at": it["updated_at"]}


class LiveGitHub:
    """The same three reads the sample data offers, against the real account."""

    def __init__(self, connection: str):
        saved = vault.load(connection) or {}
        if not saved.get("token"):
            raise PermissionError(f"The connection {connection!r} has no GitHub token. Add one on Connections.")
        self.token, self.api = saved["token"], saved.get("api_url") or API

    def search(self, repos: list[str], keywords: list[str], newer_than_days: int | None, state: str | None = None,
               label: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q = [" ".join(f"repo:{r}" for r in repos)]
        q += [f'"{k}"' if " " in k else k for k in keywords]
        if newer_than_days:
            since = (datetime.now(timezone.utc) - timedelta(days=newer_than_days)).date().isoformat()
            q.append(f"updated:>={since}")
        if state in ("open", "closed"):
            q.append(f"state:{state}")
        if label:
            q.append(f'label:"{label}"')
        found = _get(self.token, "/search/issues", {"q": " ".join(q), "sort": "updated", "order": "desc", "per_page": min(limit, 100)}, api=self.api)
        out = []
        for it in found.get("items", []):
            repo = it["repository_url"].split("/repos/", 1)[1]
            out.append(_item(repo, it))
        return out

    def issue(self, repo: str, number: int) -> dict[str, Any] | None:
        try:
            it = _get(self.token, f"/repos/{repo}/issues/{number}", api=self.api)
            comments = _get(self.token, f"/repos/{repo}/issues/{number}/comments", {"per_page": MAX_COMMENTS}, api=self.api) if it.get("comments") else []
        except GitHubError:
            return None
        return {**_item(repo, it), "body": _cut(it.get("body")),
                "comments": [{"author": (c.get("user") or {}).get("login", ""), "date": c["created_at"], "body": _cut(c.get("body"))} for c in comments]}

    def file(self, repo: str, path: str) -> str | None:
        try:
            f = _get(self.token, f"/repos/{repo}/contents/{urllib.parse.quote(path)}", api=self.api)
        except GitHubError:
            return None
        if not isinstance(f, dict) or f.get("type") != "file":
            return None
        return _cut(base64.b64decode(f.get("content", "")).decode("utf-8", errors="replace"))
