"""SharePoint through Microsoft Graph for the connector gateway: files in document libraries, and SharePoint lists.

    Sites(limits)          what a step may touch: its paths, within the connector's
      .list(prefix, modified_after, limit)       files under a folder (and its subfolders): path, size, updated
      .read(path, format)                        a file as rows (CSV, JSON, Excel ...) or text (Word, PDF, text)
      .items(path, limit)                        a SharePoint list's items, as rows
      .write(path, data, content_type, dry_run)  a new file; never overwrites one

A path is "site/library/folder/file": the site as in its address (…/sites/<site>), the document library as in its
address ("Shared Documents"), then folders. A SharePoint list is "site/Lists/<list title>". Paths match whole
segments and ignore case, as SharePoint does: "Finance/Shared Documents/Reports" allows that folder and everything in
it. The connector's admin lists what agents may use (`allowed`); each step names its own within those (`paths`).

The connector signs in as an Entra ID app (client credentials). Ask IT for Sites.Selected, granted on just the sites
agents need (read, or write where they add files): nothing else in the tenant is visible to it. Test runs read
<sample data>/sharepoint/<site>/<library>/..., lists from <sample data>/sharepoint/<site>/Lists/<title>.json, and
write into the run folder, with the same checks.
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from .bigquery_api import parse_bytes
from .gcs_api import DEFAULT_MAX_BYTES, DEFAULT_MAX_ROWS, FORMATS, WHOLE, StorageRefused, parse

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
SIMPLE_UPLOAD = 4 * 1024 * 1024        # Graph's limit for a one-request upload
HIDDEN_FIELDS = {"ContentType", "Attachments", "Edit", "LinkTitle", "LinkTitleNoMenu", "ItemChildCount", "FolderChildCount",
                 "DocIcon", "AppAuthorLookupId", "AppEditorLookupId", "AuthorLookupId", "EditorLookupId"}


def parts(path: str) -> list[str]:
    """"Finance/Shared Documents/Reports/q3.xlsx" -> its segments; refuses empty, "." and ".." segments."""
    p = [x.strip() for x in str(path or "").strip().strip("/").split("/")]
    if not p or not p[0]:
        raise StorageRefused(f"{path!r} isn't a site/library/... path.")
    if any(x in ("", ".", "..") for x in p):
        raise StorageRefused(f"{path!r}: empty, . and .. segments aren't allowed in a path.")
    return p


def norm(path: str) -> str:
    return "/".join(parts(path))


def within(path: str, prefixes: list[str]) -> bool:
    p = [x.lower() for x in parts(path)]
    return any(p[: len(q)] == q for q in ([x.lower() for x in parts(pre)] for pre in prefixes))


def allowed(step_paths: list[str] | None, connector_allowed: list[str] | None) -> list[str]:
    """The paths a step may use: its own, each within the connector's; the connector's if it names none."""
    up = [norm(x) for x in connector_allowed or []]
    if not step_paths:
        return up
    return [norm(x) for x in step_paths if within(x, up)]


def is_list(p: list[str]) -> bool:
    return len(p) >= 2 and p[1].lower() == "lists"


def format_of(path: str, fmt: str | None) -> str:
    if fmt and fmt != "auto":
        return fmt
    return FORMATS.get(Path(parts(path)[-1]).suffix.lower(), "text")


def clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """A list item's own columns: without SharePoint's bookkeeping (etags, lookup ids, content type ...)."""
    return {k: v for k, v in fields.items() if not k.startswith(("@", "_")) and k not in HIDDEN_FIELDS}


# ------------------------------------------------------------------ where the bytes are

_TOKENS: dict[tuple[str, str, str], tuple[str, float]] = {}


def app_token(up: dict[str, Any]) -> str:
    """An app-only token for Graph (client credentials), cached until shortly before it expires."""
    import requests

    from . import vault
    tenant, client = (up.get("tenant_id") or "").strip(), (up.get("client_id") or "").strip()
    secret = (vault.load(f"connector-{up.get('connector')}") or {}).get("client_secret")
    if not tenant or not client or not secret:
        raise StorageRefused("The Microsoft 365 connector needs its tenant ID, app (client) ID and client secret for SharePoint.")
    import hashlib
    key = (tenant, client, hashlib.sha256(secret.encode()).hexdigest())     # a replaced secret signs in afresh
    cached = _TOKENS.get(key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    r = requests.post(f"{LOGIN}/{tenant}/oauth2/v2.0/token", timeout=30,
                      data={"grant_type": "client_credentials", "client_id": client, "client_secret": secret,
                            "scope": "https://graph.microsoft.com/.default"})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        why = (body.get("error_description") or body.get("error") or r.text[:200]).splitlines()[0]
        raise StorageRefused(f"Microsoft sign-in refused the app: {why}")
    _TOKENS[key] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
    return body["access_token"]


def claims(token: str) -> dict[str, Any]:
    """What a token says about itself (app name, roles): its middle part, unverified; for the connector's Test only."""
    middle = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))


class _Live:
    def __init__(self, up: dict[str, Any]):
        import requests
        self.host = (up.get("hostname") or "").strip().removeprefix("https://").strip("/")
        if not self.host:
            raise StorageRefused("The Microsoft 365 connector needs the SharePoint host name, e.g. contoso.sharepoint.com.")
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {app_token(up)}"
        self.sites: dict[str, str] = {}
        self.drives: dict[tuple[str, str], str] = {}

    def _error(self, r: Any) -> str:
        try:
            return ((r.json().get("error") or {}).get("message") or r.text[:200]).splitlines()[0]
        except Exception:
            return r.text[:200]

    def _get(self, url: str, what: str, **kw: Any) -> Any:
        r = self.http.get(url if url.startswith("http") else GRAPH + url, timeout=60, **kw)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise StorageRefused(f"SharePoint refused {what}: {self._error(r)}")
        return r.json()

    def site(self, name: str) -> str:
        if name.lower() not in self.sites:
            body = self._get(f"/sites/{self.host}:/sites/{quote(name)}", f"opening the site {name}", params={"$select": "id"})
            if body is None:
                raise StorageRefused(f"There's no site {name!r} on {self.host}, or the app hasn't been granted it.")
            self.sites[name.lower()] = body["id"]
        return self.sites[name.lower()]

    def drive(self, site: str, library: str) -> str:
        key = (site.lower(), library.lower())
        if key not in self.drives:
            body = self._get(f"/sites/{self.site(site)}/drives", f"listing the libraries of {site}", params={"$select": "id,name,webUrl"}) or {}
            for d in body.get("value", []):
                url_name = unquote((d.get("webUrl") or "").rstrip("/").rsplit("/", 1)[-1])
                if library.lower() in (str(d.get("name", "")).lower(), url_name.lower()):
                    self.drives[key] = d["id"]
                    break
            else:
                raise StorageRefused(f"There's no document library {library!r} in {site}.")
        return self.drives[key]

    def _item(self, drive: str, name: str) -> str:
        return f"/drives/{drive}/root" + (f":/{quote(name)}:" if name else "")

    def list(self, site: str, library: str, folder: str, limit: int) -> list[dict[str, Any]]:
        drive, out, todo = self.drive(site, library), [], [folder.strip("/")]
        while todo and len(out) < limit:
            here = todo.pop(0)
            url: str | None = self._item(drive, here) + "/children"
            params: dict[str, Any] | None = {"$select": "name,size,lastModifiedDateTime,file,folder", "$top": 200}
            while url and len(out) < limit:
                body = self._get(url, f"listing {site}/{library}/{here}", params=params)
                if body is None:
                    raise StorageRefused(f"{site}/{library}/{here} doesn't exist.")
                for it in body.get("value", []):
                    name = f"{here}/{it['name']}" if here else it["name"]
                    if "folder" in it:
                        todo.append(name)
                    elif len(out) < limit:
                        out.append({"name": name, "size": int(it.get("size") or 0), "updated": it.get("lastModifiedDateTime"),
                                    "content_type": (it.get("file") or {}).get("mimeType")})
                url, params = body.get("@odata.nextLink"), None
        return out

    def stat(self, site: str, library: str, name: str) -> dict[str, Any] | None:
        body = self._get(self._item(self.drive(site, library), name), f"reading {site}/{library}/{name}",
                         params={"$select": "size,lastModifiedDateTime,file,folder"})
        if body is None or "folder" in body:
            return None
        return {"size": int(body.get("size") or 0), "updated": body.get("lastModifiedDateTime")}

    def read(self, site: str, library: str, name: str, upto: int) -> bytes:
        r = self.http.get(GRAPH + self._item(self.drive(site, library), name) + "/content", headers={"Range": f"bytes=0-{upto - 1}"},
                          timeout=120, stream=True)
        if r.status_code not in (200, 206):
            raise StorageRefused(f"SharePoint refused reading {site}/{library}/{name}: {self._error(r)}")
        data = r.raw.read(upto, decode_content=True)
        r.close()
        return data

    def write(self, site: str, library: str, name: str, data: bytes, content_type: str) -> dict[str, Any]:
        if len(data) > SIMPLE_UPLOAD:
            raise StorageRefused(f"{name} is {len(data):,} bytes; files written to SharePoint can be at most {SIMPLE_UPLOAD:,} for now.")
        r = self.http.put(GRAPH + self._item(self.drive(site, library), name) + "/content", data=data, timeout=120,
                          params={"@microsoft.graph.conflictBehavior": "fail"}, headers={"Content-Type": content_type})
        if r.status_code == 409:
            raise StorageRefused(f"{site}/{library}/{name} already exists; steps never overwrite a file.")
        if r.status_code not in (200, 201):
            raise StorageRefused(f"SharePoint refused writing {site}/{library}/{name}: {self._error(r)}")
        return {"web_url": r.json().get("webUrl")}

    def items(self, site: str, title: str, limit: int) -> list[dict[str, Any]]:
        url: str | None = f"/sites/{self.site(site)}/lists/{quote(title)}/items"
        params: dict[str, Any] | None = {"$expand": "fields", "$top": min(limit, 999)}
        out: list[dict[str, Any]] = []
        while url and len(out) < limit:
            body = self._get(url, f"reading the list {title}", params=params)
            if body is None:
                raise StorageRefused(f"There's no list {title!r} in {site}.")
            out += [{"id": it.get("id"), **clean_fields(it.get("fields") or {})} for it in body.get("value", [])]
            url, params = body.get("@odata.nextLink"), None
        return out[:limit]


class _Sample:
    """<sample data>/sharepoint/<site>/<library>/<name> for reads; writes go to <run dir>/sharepoint/, never into the
    sample data. Names match ignoring case, as in SharePoint."""

    def __init__(self) -> None:
        from .runstate import run_dir
        self.root = Path(os.environ.get("AGENT_SERVICE_SAMPLE_DATA", ".")) / "sharepoint"
        self.out = run_dir() / "sharepoint"

    @staticmethod
    def _find(base: Path, segs: list[str]) -> Path | None:
        p = base
        for seg in segs:
            if not p.is_dir():
                return None
            p = next((c for c in p.iterdir() if c.name.lower() == seg.lower()), None)
            if p is None:
                return None
        return p

    def _file(self, site: str, library: str, name: str) -> Path | None:
        for root in (self.out, self.root):
            p = self._find(root, [site, library, *name.split("/")])
            if p is not None and p.is_file():
                return p
        return None

    def list(self, site: str, library: str, folder: str, limit: int) -> list[dict[str, Any]]:
        out = []
        for root in (self.root, self.out):
            base = self._find(root, [site, library, *[x for x in folder.split("/") if x]])
            lib = self._find(root, [site, library])
            for p in sorted(base.rglob("*")) if base is not None and base.is_dir() else []:
                if p.is_file() and not p.name.startswith(("_", ".")):
                    out.append({"name": p.relative_to(lib).as_posix(), "size": p.stat().st_size, "content_type": None,
                                "updated": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
        return out[:limit]

    def stat(self, site: str, library: str, name: str) -> dict[str, Any] | None:
        p = self._file(site, library, name)
        return {"size": p.stat().st_size} if p else None

    def read(self, site: str, library: str, name: str, upto: int) -> bytes:
        p = self._file(site, library, name)
        if p is None:
            raise StorageRefused(f"{site}/{library}/{name} doesn't exist.")
        with p.open("rb") as f:
            return f.read(upto)

    def write(self, site: str, library: str, name: str, data: bytes, content_type: str) -> dict[str, Any]:
        if self._file(site, library, name):
            raise StorageRefused(f"{site}/{library}/{name} already exists; steps never overwrite a file.")
        p = self.out / site / library / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"web_url": None}

    def items(self, site: str, title: str, limit: int) -> list[dict[str, Any]]:
        lists = self._find(self.root, [site, "Lists"])
        p = next((c for c in lists.iterdir() if c.suffix == ".json" and c.stem.lower() == title.lower()), None) if lists and lists.is_dir() else None
        if p is None:
            raise StorageRefused(f"There's no list {title!r} in {site}.")
        rows = json.loads(p.read_text())
        return [clean_fields(r) for r in rows][:limit]


class Sites:
    def __init__(self, limits: dict[str, Any]):
        up = limits.get("upstream") or {}
        live = limits.get("source") == "live"
        self.allow = allowed(limits.get("paths"), up.get("allowed")) if live else [norm(x) for x in limits.get("paths") or up.get("allowed") or []]
        self.max_bytes = min(parse_bytes(limits.get("max_bytes")) or DEFAULT_MAX_BYTES,
                             parse_bytes(up.get("max_read_bytes")) or DEFAULT_MAX_BYTES * 20)
        self.max_rows = int(limits.get("max_rows") or DEFAULT_MAX_ROWS)
        self.store: Any = _Live(up) if live else _Sample()

    def _check(self, path: str, want_list: bool = False) -> list[str]:
        p = parts(path)
        if not within(path, self.allow):
            raise StorageRefused(f"This step may not use {'/'.join(p)}. It may use: {', '.join(self.allow) or 'nothing'}.")
        if is_list(p) != want_list:
            raise StorageRefused(f"{'/'.join(p)} is a SharePoint list: read it as a list." if is_list(p)
                                 else f"{'/'.join(p)} isn't a list: lists are site/Lists/<title>.")
        if len(p) < 2:
            raise StorageRefused(f"{'/'.join(p)}: name the document library too, e.g. {p[0]}/Shared Documents/.")
        return p

    def list(self, prefix: str, modified_after: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        site, library, *folder = self._check(prefix)
        files = self.store.list(site, library, "/".join(folder), int(limit))
        if modified_after:
            files = [f for f in files if (f.get("updated") or "") > modified_after]
        return [{"path": f"{site}/{library}/{f['name']}", **f, "format": format_of(f["name"], None)} for f in files]

    def read(self, path: str, fmt: str | None = None) -> dict[str, Any]:
        site, library, *rest = self._check(path)
        name = "/".join(rest)
        info = self.store.stat(site, library, name) if name else None
        if info is None:
            raise StorageRefused(f"{site}/{library}/{name} doesn't exist.")
        fmt = format_of(path, fmt)
        size = int(info.get("size") or 0)
        too_big = size > self.max_bytes
        if too_big and fmt in WHOLE:
            raise StorageRefused(f"{site}/{library}/{name} is {size:,} bytes, over this step's limit of {self.max_bytes:,}; "
                                 f"a {fmt} file can't be read in part.")
        raw = self.store.read(site, library, name, min(size, self.max_bytes) if size else self.max_bytes)
        if too_big and b"\n" in raw:
            raw = raw[: raw.rfind(b"\n") + 1]                 # whole lines only
        out = parse(raw, fmt, self.max_rows, too_big)
        return {"path": f"{site}/{library}/{name}", "format": fmt, "bytes": size, **out}

    def items(self, path: str, limit: int | None = None) -> dict[str, Any]:
        p = self._check(path, want_list=True)
        if len(p) != 3:
            raise StorageRefused(f"{'/'.join(p)}: a list is site/Lists/<title>.")
        cap = min(int(limit or self.max_rows), self.max_rows)
        rows = self.store.items(p[0], p[2], cap + 1)
        return {"path": "/".join(p), "rows": rows[:cap], "row_count": min(len(rows), cap), "truncated": len(rows) > cap}

    def write(self, path: str, data: bytes, content_type: str, dry_run: bool) -> dict[str, Any]:
        site, library, *rest = self._check(path)
        name = "/".join(rest)
        if not name or path.rstrip().endswith("/"):
            raise StorageRefused(f"{path!r} names a folder, not a file.")
        if dry_run:
            return {"written": False, "would_write": f"{site}/{library}/{name}", "bytes": len(data)}
        info = self.store.write(site, library, name, data, content_type)
        return {"written": True, "path": f"{site}/{library}/{name}", "bytes": len(data), **info}


def identity_and_check(up: dict[str, Any]) -> str:
    """For the connector's Test: which app it signs in as, its permissions, and one listing per allowed path."""
    token = app_token(up)
    c = claims(token)
    roles = ", ".join(c.get("roles") or []) or "no application permissions"
    live = _Live(up)
    seen = []
    for prefix in up.get("allowed") or []:
        p = parts(prefix)
        if is_list(p) and len(p) == 3:
            n = len(live.items(p[0], p[2], 50))
            seen.append(f"{'/'.join(p)} ({n}{'+' if n == 50 else ''} items)")
        elif len(p) >= 2:
            n = len(live.list(p[0], p[1], "/".join(p[2:]), 50))
            seen.append(f"{'/'.join(p)} ({n}{'+' if n == 50 else ''} files)")
        else:
            live.site(p[0])
            seen.append(f"{p[0]} (site found)")
    who = c.get("app_displayname") or up.get("client_id")
    return f"Signed in as the app {who} ({roles})." + (f" Can see {', '.join(seen)}." if seen else " No sites allowed yet.")
