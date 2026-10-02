"""Google Cloud Storage for the connector gateway: list objects, read one into rows, write a new one.

    Storage(limits)        what a step may touch: its paths, within the connector's
      .list(prefix, modified_after, limit)     objects under a prefix: path, size, updated, content type
      .read(path, format)                      an object as rows (CSV, JSON, JSON lines, Parquet) or text
      .write(path, data, content_type, dry_run)  a new object; never overwrites one

A path is "bucket/name" (a leading gs:// is fine). The connector's admin lists the buckets and prefixes agents may use
(`allowed`); each step names its own within those (`paths`). Prefixes match as written, so "sales-landing/orders/"
allows everything under that folder and nothing beside it. Reads stop at the step's byte cap: line-based formats (CSV,
JSON lines, text) are cut at the last whole line and marked truncated; JSON and Parquet must fit or are refused.

Credentials are the connector's, as for BigQuery: a service account key from the vault, the gcloud account, or the
machine's default credentials (bigquery_api.credentials). Test runs read <sample data>/gcs/<bucket>/<name>, and write
into the run folder, with the same checks.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .bigquery_api import parse_bytes

API = "https://storage.googleapis.com/storage/v1"
UPLOAD = "https://storage.googleapis.com/upload/storage/v1"
READ_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"
WRITE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_ROWS = 5000
FORMATS = {".csv": "csv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".parquet": "parquet",
           ".txt": "text", ".md": "text", ".log": "text"}


class StorageRefused(Exception):
    """Outside the step's paths, too big, or would overwrite: the message says which."""


def split(path: str) -> tuple[str, str]:
    """"gs://bucket/a/b.csv" or "bucket/a/b.csv" -> ("bucket", "a/b.csv")."""
    p = str(path or "").strip().removeprefix("gs://").lstrip("/")
    bucket, _, name = p.partition("/")
    if not bucket or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,221}[a-z0-9]", bucket):
        raise StorageRefused(f"{path!r} isn't a bucket/name path.")
    if any(part in ("..", ".") for part in name.split("/")):
        raise StorageRefused(f"{path!r}: . and .. aren't allowed in a path.")
    return bucket, name


def norm(path: str) -> str:
    b, n = split(path)
    return f"{b}/{n}"


def within(path: str, prefixes: list[str]) -> bool:
    p = norm(path)
    return any(p.startswith(norm(x)) or p + "/" == norm(x) for x in prefixes)


def allowed(step_paths: list[str] | None, connector_allowed: list[str] | None) -> list[str]:
    """The prefixes a step may use: its own, each within the connector's; the connector's if it names none."""
    up = [norm(x) for x in connector_allowed or []]
    if not step_paths:
        return up
    return [norm(x) for x in step_paths if within(x, up)]


def _number(text: str) -> Any:
    """CSV cells as they read: whole numbers, decimals, or the text itself (empty is null)."""
    t = text.strip()
    if t == "":
        return None
    if re.fullmatch(r"-?\d{1,15}", t):
        return int(t)
    if re.fullmatch(r"-?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", t):
        return float(t)
    return text


def _plain(v: Any) -> Any:
    """Parquet values as JSON-friendly ones: decimals as numbers, dates and times as ISO text."""
    from datetime import date, time
    from decimal import Decimal
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def format_of(path: str, fmt: str | None) -> str:
    if fmt and fmt != "auto":
        return fmt
    return FORMATS.get(Path(split(path)[1]).suffix.lower(), "text")


def parse(raw: bytes, fmt: str, max_rows: int, truncated: bool) -> dict[str, Any]:
    """Bytes as rows (or text), at most max_rows; `truncated` if the bytes or the rows were cut."""
    if fmt == "parquet":
        import duckdb
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(raw)
        try:
            rel = duckdb.sql(f"SELECT * FROM read_parquet('{f.name}') LIMIT {max_rows + 1}")
            cols, data = rel.columns, rel.fetchall()
        except Exception as exc:
            raise StorageRefused(f"Couldn't read it as Parquet: {str(exc).splitlines()[0][:200]}") from None
        finally:
            os.unlink(f.name)
        rows = [{c: _plain(v) for c, v in zip(cols, r)} for r in data]
    else:
        text = raw.decode("utf-8", errors="replace")
        if fmt == "text":
            return {"text": text, "rows": [], "row_count": 0, "truncated": truncated}
        if fmt == "csv":
            rows = [{k: _number(v) if isinstance(v, str) else v for k, v in r.items() if k is not None}
                    for r in csv.DictReader(io.StringIO(text))]
        elif fmt == "jsonl":
            rows = []
            for n, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        raise StorageRefused(f"Line {n} isn't JSON.") from None
        elif fmt == "json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise StorageRefused(f"It isn't valid JSON: {exc}") from None
            rows = data if isinstance(data, list) else [data]
        else:
            raise StorageRefused(f"Unknown format {fmt!r}: use auto, csv, json, jsonl, parquet or text.")
    cut = len(rows) > max_rows
    rows = rows[:max_rows]
    return {"rows": [r if isinstance(r, dict) else {"value": r} for r in rows], "row_count": len(rows),
            "truncated": truncated or cut, "text": None}


def serialize(value: Any, fmt: str) -> tuple[bytes, str]:
    """A step's value as a file: JSON, JSON lines or CSV (from a list of records), text, or a chart's PNG."""
    if fmt == "json":
        return json.dumps(value, indent=1, ensure_ascii=False, default=str).encode(), "application/json"
    if fmt == "jsonl":
        items = value if isinstance(value, list) else [value]
        return "".join(json.dumps(x, ensure_ascii=False, default=str) + "\n" for x in items).encode(), "application/x-ndjson"
    if fmt == "csv":
        items = [x for x in (value if isinstance(value, list) else [value]) if isinstance(x, dict)]
        cols: list[str] = []
        for x in items:
            cols += [k for k in x if k not in cols]
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=cols)
        w.writeheader()
        for x in items:
            w.writerow({k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for k, v in x.items()})
        return out.getvalue().encode(), "text/csv"
    if fmt == "png":
        from .runstate import run_dir
        p = (run_dir() / str(value)).resolve()
        if p.parent != (run_dir() / "charts").resolve() or not p.exists():
            raise StorageRefused(f"{value!r} isn't a chart this run drew.")
        return p.read_bytes(), "image/png"
    return ("" if value is None else str(value)).encode(), "text/plain"


# ------------------------------------------------------------------ where the bytes are

class _Live:
    def __init__(self, up: dict[str, Any], write: bool):
        from google.auth.transport.requests import AuthorizedSession

        from .bigquery_api import credentials
        self.http = AuthorizedSession(credentials(up, [WRITE_SCOPE if write else READ_SCOPE]))

    def _error(self, r: Any) -> str:
        try:
            return (r.json().get("error") or {}).get("message") or r.text[:200]
        except Exception:
            return r.text[:200]

    def list(self, bucket: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        out, token = [], None
        while len(out) < limit:
            params = {"prefix": prefix, "maxResults": min(1000, limit - len(out)),
                      "fields": "items(name,size,updated,contentType,generation),nextPageToken"}
            if token:
                params["pageToken"] = token
            r = self.http.get(f"{API}/b/{bucket}/o", params=params, timeout=60)
            if r.status_code != 200:
                raise StorageRefused(f"Cloud Storage refused listing {bucket}: {self._error(r)}")
            body = r.json()
            out += [{"name": i["name"], "size": int(i.get("size", 0)), "updated": i.get("updated"),
                     "content_type": i.get("contentType"), "generation": i.get("generation")} for i in body.get("items", [])]
            token = body.get("nextPageToken")
            if not token:
                break
        return out

    def stat(self, bucket: str, name: str) -> dict[str, Any] | None:
        r = self.http.get(f"{API}/b/{bucket}/o/{quote(name, safe='')}", params={"fields": "size,updated,contentType"}, timeout=60)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise StorageRefused(f"Cloud Storage refused reading {bucket}/{name}: {self._error(r)}")
        return {**r.json(), "size": int(r.json().get("size", 0))}

    def read(self, bucket: str, name: str, upto: int) -> bytes:
        r = self.http.get(f"{API}/b/{bucket}/o/{quote(name, safe='')}", params={"alt": "media"},
                          headers={"Range": f"bytes=0-{upto - 1}"}, timeout=120)
        if r.status_code not in (200, 206):
            raise StorageRefused(f"Cloud Storage refused reading {bucket}/{name}: {self._error(r)}")
        return r.content[:upto]

    def write(self, bucket: str, name: str, data: bytes, content_type: str) -> dict[str, Any]:
        r = self.http.post(f"{UPLOAD}/b/{bucket}/o", params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
                           data=data, headers={"Content-Type": content_type}, timeout=120)
        if r.status_code == 412:
            raise StorageRefused(f"{bucket}/{name} already exists; steps never overwrite a file.")
        if r.status_code not in (200, 201):
            raise StorageRefused(f"Cloud Storage refused writing {bucket}/{name}: {self._error(r)}")
        return {"generation": r.json().get("generation")}


class _Sample:
    """<sample data>/gcs/<bucket>/<name> for reads; writes go to <run dir>/gcs/, never into the sample data."""

    def __init__(self) -> None:
        from .runstate import run_dir
        self.root = Path(os.environ.get("AGENT_SERVICE_SAMPLE_DATA", ".")) / "gcs"
        self.out = run_dir() / "gcs"

    def _file(self, bucket: str, name: str) -> Path | None:
        for root in (self.out, self.root):
            p = root / bucket / name
            if p.is_file():
                return p
        return None

    def list(self, bucket: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        out = []
        for root in (self.root, self.out):
            base = root / bucket
            for p in sorted(base.rglob("*")) if base.exists() else []:
                name = p.relative_to(base).as_posix()
                if p.is_file() and name.startswith(prefix) and not p.name.startswith("_"):
                    out.append({"name": name, "size": p.stat().st_size, "content_type": None, "generation": None,
                                "updated": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
        return out[:limit]

    def stat(self, bucket: str, name: str) -> dict[str, Any] | None:
        p = self._file(bucket, name)
        return {"size": p.stat().st_size} if p else None

    def read(self, bucket: str, name: str, upto: int) -> bytes:
        p = self._file(bucket, name)
        if p is None:
            raise StorageRefused(f"{bucket}/{name} doesn't exist.")
        with p.open("rb") as f:
            return f.read(upto)

    def write(self, bucket: str, name: str, data: bytes, content_type: str) -> dict[str, Any]:
        if self._file(bucket, name):
            raise StorageRefused(f"{bucket}/{name} already exists; steps never overwrite a file.")
        p = self.out / bucket / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"generation": None}


class Storage:
    def __init__(self, limits: dict[str, Any]):
        up = limits.get("upstream") or {}
        self.allow = allowed(limits.get("paths"), up.get("allowed")) if limits.get("source") == "live" else \
            [norm(x) for x in limits.get("paths") or up.get("allowed") or []]
        self.max_bytes = min(parse_bytes(limits.get("max_bytes")) or DEFAULT_MAX_BYTES,
                             parse_bytes(up.get("max_read_bytes")) or DEFAULT_MAX_BYTES * 20)
        self.max_rows = int(limits.get("max_rows") or DEFAULT_MAX_ROWS)
        self.store: Any = _Live(up, "write_object" in (limits.get("actions") or [])) if limits.get("source") == "live" else _Sample()

    def _check(self, path: str) -> tuple[str, str]:
        bucket, name = split(path)
        if not within(path, self.allow):
            raise StorageRefused(f"This step may not use {bucket}/{name}. It may use: {', '.join(self.allow) or 'nothing'}.")
        return bucket, name

    def list(self, prefix: str, modified_after: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        bucket, name = self._check(prefix)
        files = self.store.list(bucket, name, int(limit))
        if modified_after:
            files = [f for f in files if (f.get("updated") or "") > modified_after]
        return [{"path": f"{bucket}/{f['name']}", **f, "format": format_of(f"{bucket}/{f['name']}", None)} for f in files
                if not f["name"].endswith("/")]

    def read(self, path: str, fmt: str | None = None) -> dict[str, Any]:
        bucket, name = self._check(path)
        info = self.store.stat(bucket, name)
        if info is None:
            raise StorageRefused(f"{bucket}/{name} doesn't exist.")
        fmt = format_of(path, fmt)
        size = int(info.get("size") or 0)
        too_big = size > self.max_bytes
        if too_big and fmt in ("json", "parquet"):
            raise StorageRefused(f"{bucket}/{name} is {size:,} bytes, over this step's limit of {self.max_bytes:,}; "
                                 "a JSON or Parquet file can't be read in part.")
        raw = self.store.read(bucket, name, min(size, self.max_bytes) if size else self.max_bytes)
        if too_big and b"\n" in raw:
            raw = raw[: raw.rfind(b"\n") + 1]                 # whole lines only
        out = parse(raw, fmt, self.max_rows, too_big)
        return {"path": f"{bucket}/{name}", "format": fmt, "bytes": size, **out}

    def write(self, path: str, data: bytes, content_type: str, dry_run: bool) -> dict[str, Any]:
        bucket, name = self._check(path)
        if not name or name.endswith("/"):
            raise StorageRefused(f"{path!r} names a folder, not a file.")
        if dry_run:
            return {"written": False, "would_write": f"{bucket}/{name}", "bytes": len(data)}
        info = self.store.write(bucket, name, data, content_type)
        return {"written": True, "path": f"{bucket}/{name}", "bytes": len(data), **info}


def identity_and_check(up: dict[str, Any]) -> str:
    """For the connector's Test: who it signs in as, and one listing per allowed prefix."""
    from .bigquery_api import identity
    live = _Live(up, False)
    seen = []
    for prefix in up.get("allowed") or []:
        bucket, name = split(prefix if "/" in prefix else prefix + "/")
        n = len(live.list(bucket, name, 50))
        seen.append(f"{bucket}/{name} ({n}{'+' if n == 50 else ''} files)")
    return f"Signed in as {identity(up)}." + (f" Can list {', '.join(seen)}." if seen else " No buckets allowed yet.")
