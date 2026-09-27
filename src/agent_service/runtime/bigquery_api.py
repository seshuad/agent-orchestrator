"""BigQuery for agents: queries checked before they run, on real data or on sample tables.

    Warehouse(limits)       the step's view: query(sql, params), list_tables(dataset), get_schema(table), insert_rows(...)

Every query is checked first, with a dry run:
    - one statement, and a SELECT (reading only), unless the step may write
    - every table it reads is inside the step's allowed data (projects, datasets or single tables)
    - it scans no more than the step's byte cap, and fits what's left of the connector's monthly budget
Then it runs with BigQuery's own maximum_bytes_billed set to that cap (so BigQuery refuses, too), labelled with the
agent, run and step so the cost shows up per agent in the billing export. Results are capped at max_rows.

Credentials (the limits token's `upstream`, from the connector): a service account key from the vault, the gcloud
account signed in on the service's machine, or its application default credentials.

Sample data (test runs): <sample data>/bigquery/<dataset>/<table>.json, loaded into DuckDB; the same checks apply.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import vault

PRICE_PER_TIB = 6.25                     # on-demand USD per TiB scanned
MIN_BILLED = 10 * 1024 * 1024            # BigQuery bills at least 10 MB per query that scans anything
UNITS = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}


class QueryRefused(Exception):
    """The query is outside the step's limits; the message says which and why."""


def parse_bytes(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?b)?\s*", str(value).lower())
    if not m:
        raise ValueError(f"{value!r} isn't a size like 500MB or 2GB.")
    return int(float(m.group(1)) * UNITS[m.group(2) or "b"])


def human(n: int | None) -> str:
    if n is None:
        return "unknown"
    for unit in ("TB", "GB", "MB", "KB"):
        size = UNITS[unit.lower()]
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def cost_of(bytes_billed: int | None) -> float:
    return (bytes_billed or 0) / 1024 ** 4 * PRICE_PER_TIB


def _jsonable(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def scope_of(ref: str, default_project: str) -> tuple[str, str | None, str | None]:
    """An allowed-data entry as (project, dataset, table): `dataset` (in the billing project), `project.dataset`,
    or `project.dataset.table`."""
    parts = ref.strip().strip("`").split(".")
    if len(parts) == 1:
        return (default_project, parts[0], None)
    if len(parts) == 2:
        return (parts[0], parts[1], None)
    return (parts[0], parts[1], ".".join(parts[2:]))


def within(inner: tuple, outer: tuple) -> bool:
    return all(o is None or i == o for i, o in zip(inner, outer)) and not (outer[1] is not None and inner[1] is None) \
        and not (outer[2] is not None and inner[2] is None)


def allowed(table: str, allow: list[str], default_project: str) -> bool:
    """`table` is project.dataset.table."""
    project, dataset, name = table.split(".", 2)
    return any(within((project, dataset, name), scope_of(a, default_project)) for a in allow)


# ------------------------------------------------------------------ credentials and clients

def credentials(up: dict[str, Any]):
    """google-auth credentials for the connector: a service account key, the gcloud account, or ADC."""
    kind = (up.get("auth") or {}).get("kind", "gcloud")
    if kind == "service_account":
        from google.oauth2 import service_account
        key = (vault.load(f"connector-{up.get('connector')}") or {}).get("key")
        if not key:
            raise PermissionError("The BigQuery connector has no service account key. An admin adds it under Connectors.")
        info = key if isinstance(key, dict) else json.loads(key)
        return service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/bigquery"])
    if kind == "gcloud":
        from google.oauth2.credentials import Credentials
        out = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise PermissionError(f"The gcloud command couldn't give a token: {out.stderr.strip()[-200:]}")
        return Credentials(out.stdout.strip())
    import google.auth
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
    return creds


def identity(up: dict[str, Any]) -> str:
    kind = (up.get("auth") or {}).get("kind", "gcloud")
    if kind == "service_account":
        key = (vault.load(f"connector-{up.get('connector')}") or {}).get("key")
        return (key if isinstance(key, dict) else json.loads(key or "{}")).get("client_email", "a service account")
    if kind == "gcloud":
        out = subprocess.run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "the gcloud account"
    return "this machine's application default credentials"


class _Live:
    def __init__(self, up: dict[str, Any]):
        from google.cloud import bigquery
        self.bq = bigquery
        self.project = up.get("billing_project")
        self.client = bigquery.Client(project=self.project, credentials=credentials(up), location=up.get("location") or None)

    def _params(self, params: dict[str, Any]) -> list:
        out = []
        for k, v in (params or {}).items():
            kind = "BOOL" if isinstance(v, bool) else "INT64" if isinstance(v, int) else "FLOAT64" if isinstance(v, float) else "STRING"
            if isinstance(v, list):
                inner = "INT64" if v and all(isinstance(x, int) for x in v) else "STRING"
                out.append(self.bq.ArrayQueryParameter(k, inner, v))
            else:
                out.append(self.bq.ScalarQueryParameter(k, kind, None if v is None else v))
        return out

    def dry_run(self, sql: str, params: dict[str, Any]) -> tuple[str, list[str], int]:
        job = self.client.query(sql, job_config=self.bq.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=self._params(params)))
        tables = [f"{t.project}.{t.dataset_id}.{t.table_id}" for t in (job.referenced_tables or [])]
        return job.statement_type or "", tables, int(job.total_bytes_processed or 0)

    def run(self, sql: str, params: dict[str, Any], max_bytes: int, max_rows: int, labels: dict[str, str]) -> tuple[list[dict], int, bool]:
        cfg = self.bq.QueryJobConfig(query_parameters=self._params(params), maximum_bytes_billed=max_bytes, labels=labels)
        job = self.client.query(sql, job_config=cfg)
        rows, more = [], False
        for i, r in enumerate(job.result(max_results=max_rows + 1, timeout=120)):
            if i >= max_rows:
                more = True
                break
            rows.append(_jsonable(dict(r.items())))
        return rows, int(job.total_bytes_billed or 0), more

    def list_tables(self, dataset: str) -> list[dict[str, Any]]:
        ref = dataset if dataset.count(".") == 1 else f"{self.project}.{dataset}"
        return [{"table": f"{t.project}.{t.dataset_id}.{t.table_id}", "type": t.table_type} for t in self.client.list_tables(ref)]

    def get_schema(self, table: str) -> dict[str, Any]:
        t = self.client.get_table(table)
        return {"table": table, "rows": t.num_rows, "columns": [{"name": f.name, "type": f.field_type, "mode": f.mode,
                                                                  "description": f.description} for f in t.schema]}

    def insert_rows(self, table: str, rows: list[dict]) -> int:
        errors = self.client.insert_rows_json(table, rows)
        if errors:
            raise QueryRefused(f"BigQuery refused some rows: {str(errors)[:300]}")
        return len(rows)


class _Sample:
    """Sample tables in DuckDB: <dir>/bigquery/<dataset>/<table>.json (a list of rows). Table names are
    rewritten from `project.dataset.table` (or dataset.table) to DuckDB's dataset.table."""

    def __init__(self, up: dict[str, Any], root: Path):
        import duckdb
        self.project = up.get("billing_project") or "sample-project"
        self.db = duckdb.connect()
        self.tables: dict[str, Path] = {}
        base = root / "bigquery"
        for path in sorted(base.glob("*/*.json")) if base.exists() else []:
            dataset, table = path.parent.name, path.stem
            self.db.execute(f'CREATE SCHEMA IF NOT EXISTS "{dataset}"')
            self.db.execute(f'CREATE TABLE "{dataset}"."{table}" AS SELECT * FROM read_json_auto(?)', [str(path)])
            self.tables[f"{self.project}.{dataset}.{table}"] = path

    def _local(self, sql: str) -> str:
        def swap(m: re.Match) -> str:
            parts = m.group(1).split(".")
            return f"{parts[-2]}.{parts[-1]}" if len(parts) >= 2 else m.group(1)
        return re.sub(r"`([^`]+)`", swap, sql)

    def dry_run(self, sql: str, params: dict[str, Any]) -> tuple[str, list[str], int]:
        import duckdb
        local = self._local(sql)
        try:
            statements = duckdb.extract_statements(local)
        except duckdb.Error as exc:
            raise QueryRefused(f"The SQL doesn't parse: {str(exc).splitlines()[0]}") from None
        kind = "SELECT" if len(statements) == 1 and statements[0].type.name == "SELECT" else " + ".join(s.type.name for s in statements)
        if kind != "SELECT":
            return kind, [], 0                   # refused before its tables matter
        names = duckdb.get_table_names(local, qualified=True)
        tables = []
        for n in names:
            bare = n.split(" AS ")[0].strip().strip('"')
            parts = [p.strip('"') for p in bare.split(".")]
            tables.append(f"{self.project}.{parts[-2]}.{parts[-1]}" if len(parts) >= 2 else f"{self.project}.?.{parts[-1]}")
        size = sum(self.tables[t].stat().st_size for t in tables if t in self.tables)
        return kind, tables, size

    def run(self, sql: str, params: dict[str, Any], max_bytes: int, max_rows: int, labels: dict[str, str]) -> tuple[list[dict], int, bool]:
        local = re.sub(r"@(\w+)", r"$\1", self._local(sql))
        cur = self.db.execute(local, {k: v for k, v in (params or {}).items() if f"${k}" in local})
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        return [_jsonable(dict(zip(cols, r))) for r in rows[:max_rows]], 0, len(rows) > max_rows

    def list_tables(self, dataset: str) -> list[dict[str, Any]]:
        d = dataset.split(".")[-1]
        return [{"table": t, "type": "TABLE"} for t in self.tables if t.split(".")[1] == d]

    def get_schema(self, table: str) -> dict[str, Any]:
        _, d, t = table.split(".") if table.count(".") == 2 else (self.project, *table.split("."))
        cols = self.db.execute(f'DESCRIBE "{d}"."{t}"').fetchall()
        count = self.db.execute(f'SELECT count(*) FROM "{d}"."{t}"').fetchone()[0]
        return {"table": f"{self.project}.{d}.{t}", "rows": count, "columns": [{"name": c[0], "type": c[1]} for c in cols]}

    def insert_rows(self, table: str, rows: list[dict]) -> int:
        from .runstate import run_dir
        path = run_dir() / "bigquery" / f"{table}.jsonl"             # written in the run folder, never into the sample data
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")
        return len(rows)


class Warehouse:
    """The step's BigQuery, within its limits. `limits` comes from the signed limits token."""

    def __init__(self, limits: dict[str, Any], sample_root: Path | None = None):
        self.limits = limits
        self.up = limits.get("upstream") or {}
        self.default_project = self.up.get("billing_project") or "sample-project"
        live = limits.get("source") == "live"
        self.engine: Any = _Live(self.up) if live else _Sample(self.up, sample_root or Path(os.environ.get("AGENT_SERVICE_SAMPLE_DATA", ".")))
        if not live:
            self.default_project = self.engine.project
        caps = [parse_bytes(limits.get("max_bytes")), parse_bytes(self.up.get("max_bytes_cap"))]
        self.max_bytes = min([c for c in caps if c] or [parse_bytes("1GB")])
        self.max_rows = int(limits.get("max_rows") or 1000)
        self.allow = self._scope()

    def _scope(self) -> list[str]:
        """The step's data, narrowed to the connector's: a step can't read beyond what the admin allowed."""
        connector = list(self.up.get("allowed") or [])
        step = list(self.limits.get("datasets") or [])
        if not step:
            return connector
        return [x for x in step if any(within(scope_of(x, self.default_project), scope_of(c, self.default_project)) for c in connector)]

    def check(self, sql: str, params: dict[str, Any], write: bool = False) -> tuple[list[str], int]:
        kind, tables, size = self.engine.dry_run(sql, params)
        if kind != "SELECT" and not write:
            raise QueryRefused(f"Only a single SELECT can run here (this is {kind or 'not a query'}).")
        outside = [t for t in tables if not allowed(t, self.allow, self.default_project)]
        if outside:
            raise QueryRefused(f"This step may not read {', '.join(outside)}. It may read: {', '.join(self.allow) or 'nothing'}.")
        if size > self.max_bytes:
            raise QueryRefused(f"This query would scan {human(size)}, over this step's limit of {human(self.max_bytes)}. "
                               "Select fewer columns, filter on the partition column, or ask for a higher limit.")
        left = self.limits.get("budget_left_usd")
        if left is not None and cost_of(max(size, MIN_BILLED)) > float(left):
            raise QueryRefused(f"This query would cost about ${cost_of(size):.2f}, more than the ${float(left):.2f} left in "
                               "the connector's monthly budget.")
        return tables, size

    def query(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        tables, estimate = self.check(sql, params)
        labels = {k: re.sub(r"[^a-z0-9_-]", "-", str(v).lower())[:63] for k, v in
                  {"agent": self.limits.get("agent", ""), "run": os.environ.get("CONDUCTOR_RUN_ID", ""),
                   "step": os.environ.get("AGENT_SERVICE_STEP", "")}.items() if v}
        rows, billed, more = self.engine.run(sql, params, self.max_bytes, self.max_rows, labels)
        return {"rows": rows, "row_count": len(rows), "truncated": more, "bytes_billed": billed,
                "cost_usd": round(cost_of(billed), 6), "tables": tables, "estimated_bytes": estimate}

    def list_tables(self, dataset: str) -> list[dict[str, Any]]:
        out = self.engine.list_tables(dataset)
        return [t for t in out if allowed(t["table"], self.allow, self.default_project)]

    def get_schema(self, table: str) -> dict[str, Any]:
        full = table if table.count(".") == 2 else f"{self.default_project}.{table}"
        if not allowed(full, self.allow, self.default_project):
            raise QueryRefused(f"This step may not read {full}.")
        return self.engine.get_schema(full)

    def insert_rows(self, table: str, rows: list[dict], dry_run: bool) -> dict[str, Any]:
        targets = self.limits.get("tables") or []
        full = table if table.count(".") == 2 else f"{self.default_project}.{table}"
        if full not in [t if t.count(".") == 2 else f"{self.default_project}.{t}" for t in targets]:
            raise QueryRefused(f"This step may not write to {full}. It may write to: {', '.join(targets) or 'nothing'}.")
        if dry_run:
            return {"inserted": 0, "would_insert": rows}
        return {"inserted": self.engine.insert_rows(full, rows), "would_insert": []}
