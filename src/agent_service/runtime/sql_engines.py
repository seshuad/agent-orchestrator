"""Trino and Spark SQL (on Dataproc) for agents: read queries checked before they run, on real engines or sample tables.

    Engine(limits)          the step's view: query(sql, params), list_tables(schema), get_schema(table)

Every query is checked first, without running it (sqlglot parses it in the engine's dialect):
    - one statement, and only reading: a SELECT (or WITH ... SELECT, UNION ...), nothing that writes or changes anything
    - every table it reads is inside the step's allowed data (`datasets`), within the connector's (`allowed`)
    - @name parameters become literals, quoted for the dialect, so values a step passes in are never SQL
Then it runs with a row cap (wrapped in SELECT * FROM (...) LIMIT n) and the connector's time limit.

Names are qualified before they're checked: Trino tables as catalog.schema.table (an unqualified name gets the
connector's catalog and schema), Spark tables as catalog.schema.table too, where the catalog is spark_catalog (the
metastore) unless the connector configures another, e.g. an Iceberg catalog. An allowed entry names a catalog, a schema
or one table: Trino "iceberg", "iceberg.sales", "iceberg.sales.orders"; Spark "sales", "sales.orders" (in the default
catalog) or "lake.sales.orders".

Where it runs:
    trino      the Trino HTTP API (e.g. Dataproc's Trino component on the master node, port 8060), as the connector's user
    spark-sql  a Dataproc job on a running cluster, or a Dataproc Serverless batch. Results are written as JSON to the
               connector's staging folder in Cloud Storage (INSERT OVERWRITE DIRECTORY, added after the checks) and read
               back from there. A serverless batch takes a minute or more to start: use it for scheduled checks.
               The connector's admin can add jars (e.g. the Iceberg runtime) and setup statements that run before each
               query in the same job, e.g. registering Iceberg tables in a session catalog from their metadata files.
               Setup comes from the connector, never from a step.

Sample data (test runs): <sample data>/sql/<schema>/<table>.json, loaded into DuckDB; queries are translated from the
engine's dialect, and the same checks apply.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .bigquery_api import QueryRefused, _jsonable

DIALECT = {"trino": "trino", "spark-sql": "spark"}
NAME = {"trino": "Trino", "spark-sql": "Spark SQL"}
DEFAULT_TIMEOUT = {"trino": 120, "spark-sql": 900}     # seconds; a serverless batch spends a minute or two starting
DATAPROC = "https://dataproc.googleapis.com/v1"
STORAGE = "https://storage.googleapis.com/storage/v1"
UPLOAD = "https://storage.googleapis.com/upload/storage/v1"
WRITES = ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable", "Command", "Grant",
          "Set", "Use", "Cache", "Uncache", "LoadData", "Refresh", "Copy", "Transaction", "Commit", "Rollback")


# ------------------------------------------------------------------ checking

def bind(sql: str, params: dict[str, Any], dialect: str) -> str:
    """@name -> the value as a literal of the dialect. Text inside quotes is left alone."""
    from sqlglot import exp

    def literal(name: str) -> str:
        if name not in params:
            raise QueryRefused(f"The query uses @{name}, but the step passes no {name}.")
        v = params[name]
        if isinstance(v, list):
            return "(" + ", ".join(exp.convert(x).sql(dialect=dialect) for x in v) + ")" if v else "(NULL)"
        return exp.convert(v).sql(dialect=dialect)

    pattern = re.compile(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`)|@(\w+)")
    return pattern.sub(lambda m: m.group(1) if m.group(1) else literal(m.group(2)), sql)


def parse(sql: str, dialect: str) -> Any:
    """The one reading statement in `sql`, parsed; refuses anything else."""
    import sqlglot
    from sqlglot import exp
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise QueryRefused(f"The SQL doesn't parse: {str(exc).splitlines()[0][:300]}") from None
    if len(statements) != 1:
        raise QueryRefused(f"Only a single SELECT can run here (this has {len(statements)} statements).")
    tree = statements[0]
    writes = [n for n in tree.walk() if type(n).__name__ in WRITES]
    if not isinstance(tree, exp.Query) or writes:
        what = type(writes[0] if writes else tree).__name__.upper()
        raise QueryRefused(f"Only a single SELECT can run here (this is {what}).")
    return tree


def tables_of(tree: Any) -> list[tuple[str, str, str]]:
    """(catalog, schema, table) of every table the query reads, as written (parts it leaves out are "")."""
    from sqlglot import exp
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out = []
    for t in tree.find_all(exp.Table):
        if not t.name or (not t.db and t.name.lower() in ctes):
            continue
        out.append((t.catalog, t.db, t.name))
    return list(dict.fromkeys(out))


class Names:
    """Qualifying table names and allowed entries the engine's way, and checking one is within another."""

    def __init__(self, service: str, catalog: str | None, schema: str | None):
        self.service = service
        self.catalog = (catalog or ("spark_catalog" if service == "spark-sql" else "")).lower()
        self.schema = (schema or ("default" if service == "spark-sql" else "")).lower()

    def table(self, parts: tuple[str, str, str]) -> tuple[str, str, str]:
        catalog, schema, name = (p.lower() for p in parts)
        return (catalog or self.catalog, schema or self.schema, name)

    def entry(self, text: str) -> tuple[str, ...]:
        parts = tuple(p.strip().strip('`"').lower() for p in str(text).strip().split(".") if p.strip())
        if self.service == "spark-sql" and len(parts) < 3:        # Spark entries name schemas in the default catalog
            parts = (self.catalog, *parts)
        return parts

    def within(self, table: tuple[str, ...], entry: tuple[str, ...]) -> bool:
        return bool(entry) and table[: len(entry)] == entry

    def show(self, t: tuple[str, ...]) -> str:
        return ".".join(x for x in t if x)


def check(sql: str, params: dict[str, Any], service: str, names: Names, allow: list[str]) -> tuple[Any, list[str]]:
    """The parsed query and the qualified tables it reads; refuses anything outside the step's limits."""
    tree = parse(bind(sql, params or {}, DIALECT[service]), DIALECT[service])
    tables = [names.table(t) for t in tables_of(tree)]
    unknown = [names.show(t) for t in tables if not t[0] or not t[1]]
    if unknown:
        raise QueryRefused(f"Name {', '.join(unknown)} in full (catalog.schema.table): the connector sets no default catalog and schema.")
    entries = [names.entry(a) for a in allow]
    outside = [names.show(t) for t in tables if not any(names.within(t, e) for e in entries)]
    if outside:
        raise QueryRefused(f"This step may not read {', '.join(outside)}. It may read: {', '.join(allow) or 'nothing'}.")
    return tree, [names.show(t) for t in tables]


def capped(tree: Any, max_rows: int, dialect: str) -> str:
    """The query, returning at most max_rows + 1 rows (one more, to know it was cut)."""
    from sqlglot import exp
    return exp.select("*").from_(tree.subquery("q")).limit(max_rows + 1).sql(dialect=dialect)


# ------------------------------------------------------------------ where queries run

class _Trino:
    def __init__(self, up: dict[str, Any], timeout: int):
        import trino
        from . import vault
        url = (up.get("server") or "").strip().rstrip("/")
        if not url:
            raise QueryRefused("The Trino connector has no server address yet: an admin sets it under Connectors.")
        m = re.fullmatch(r"(https?)://([^/:]+)(?::(\d+))?", url)
        if not m:
            raise QueryRefused(f"{url!r} isn't a server address like http://cluster-m:8060.")
        scheme, host, port = m.group(1), m.group(2), int(m.group(3) or (443 if m.group(1) == "https" else 8080))
        kind = (up.get("auth") or {}).get("kind", "none")
        secret = (vault.load(f"connector-{up.get('connector')}") or {}).get("password")
        auth = None
        if kind == "basic":
            auth = trino.auth.BasicAuthentication(up.get("user") or "agent-orchestrator", secret or "")
        elif kind == "jwt":
            auth = trino.auth.JWTAuthentication(secret or "")
        self.connect = lambda: trino.dbapi.connect(host=host, port=port, http_scheme=scheme, user=up.get("user") or "agent-orchestrator",
                                                   auth=auth, catalog=up.get("catalog") or None, schema=up.get("schema") or None,
                                                   source="agent-orchestrator", request_timeout=timeout,
                                                   session_properties={"query_max_run_time": f"{timeout}s"})

    def run(self, sql: str, labels: dict[str, str]) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(f"-- {json.dumps(labels)}\n{sql}")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description or []]
            return [_jsonable(dict(zip(cols, r))) for r in rows]
        except Exception as exc:
            raise QueryRefused(f"Trino refused the query: {str(exc).splitlines()[0][:400]}") from None
        finally:
            conn.close()


class _Dataproc:
    """Spark SQL as a Dataproc job (on the connector's cluster) or a Dataproc Serverless batch. The query goes to the
    staging folder as a file, writes its rows there as JSON, and they're read back."""

    def __init__(self, up: dict[str, Any], timeout: int):
        from google.auth.transport.requests import AuthorizedSession

        from .bigquery_api import credentials
        self.up, self.timeout = up, timeout
        what = {"project": "the project ID", "region": "the region", "staging": "a staging folder (gs://bucket/folder)", "cluster": "the cluster name"}
        missing = [k for k in ("project", "region", "staging") if not (up.get(k) or "").strip()]
        if up.get("mode", "serverless") == "cluster" and not (up.get("cluster") or "").strip():
            missing.append("cluster")
        if missing:
            raise QueryRefused(f"Fill in {', '.join(what[k] for k in missing)} under Connectors.")
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", up["project"].strip()):
            raise QueryRefused(f"{up['project'].strip()!r} isn't a project ID. Use the ID (lower case, e.g. my-project-123456), not the project's name.")
        if not up["staging"].strip().startswith("gs://"):
            raise QueryRefused(f"The staging folder must be a Cloud Storage folder like gs://bucket/agent-sql (got {up['staging'].strip()!r}).")
        self.http = AuthorizedSession(credentials(up, ["https://www.googleapis.com/auth/cloud-platform"]))
        self.base = f"{DATAPROC}/projects/{up['project'].strip()}/regions/{up['region'].strip()}"
        bucket, _, prefix = up["staging"].strip().removeprefix("gs://").partition("/")
        self.bucket, self.prefix = bucket, prefix.strip("/")

    def _ok(self, r: Any, what: str) -> Any:
        if r.status_code not in (200, 201):
            try:
                why = (r.json().get("error") or {}).get("message") or r.text[:300]
            except Exception:
                why = r.text[:300]
            raise QueryRefused(f"Dataproc refused {what}: {why.splitlines()[0]}")
        return r.json() if r.text else {}

    def run(self, sql: str, labels: dict[str, str]) -> list[dict[str, Any]]:
        rid = f"ao-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        folder = "/".join(x for x in (self.prefix, rid) if x)
        out = f"gs://{self.bucket}/{folder}/rows"
        setup = "".join(f"{s.strip().rstrip(';')};\n" for s in setup_statements(self.up))
        script = f"{setup}INSERT OVERWRITE DIRECTORY '{out}' USING json\n{sql};\n"
        self._ok(self.http.post(f"{UPLOAD}/b/{self.bucket}/o", params={"uploadType": "media", "name": f"{folder}/query.sql"},
                                data=script.encode(), headers={"Content-Type": "text/plain"}, timeout=60), "the query file")
        query = f"gs://{self.bucket}/{folder}/query.sql"
        props = {str(k): str(v) for k, v in (self.up.get("properties") or {}).items()}
        jars = [j.strip() for j in self.up.get("jars") or [] if str(j).strip()]
        labels = {k: v for k, v in labels.items() if v}
        if self.up.get("mode", "serverless") == "cluster":
            body = {"job": {"reference": {"jobId": rid}, "placement": {"clusterName": self.up["cluster"].strip()}, "labels": labels,
                            "sparkSqlJob": {"queryFileUri": query, "properties": props, **({"jarFileUris": jars} if jars else {})}}}
            self._ok(self.http.post(f"{self.base}/jobs:submit", json=body, timeout=60), "the Spark SQL job")
            status = self._wait(f"{self.base}/jobs/{rid}", lambda j: (j.get("status") or {}).get("state"),
                                {"DONE"}, {"ERROR", "CANCELLED"}, lambda: self.http.post(f"{self.base}/jobs/{rid}:cancel", timeout=60),
                                lambda j: self._cause(j.get("driverOutputResourceUri")) or (j.get("status") or {}).get("details"))
        else:
            batch: dict[str, Any] = {"sparkSqlBatch": {"queryFileUri": query, **({"jarFileUris": jars} if jars else {})}, "labels": labels,
                                     "runtimeConfig": {"properties": props, **({"version": self.up["runtime_version"]} if self.up.get("runtime_version") else {})}}
            env = {k: v for k, v in {"serviceAccount": self.up.get("service_account"), "subnetworkUri": self.up.get("subnetwork")}.items() if v}
            if env:
                batch["environmentConfig"] = {"executionConfig": env}
            base = self.base.replace("/regions/", "/locations/")
            op = self._ok(self.http.post(f"{base}/batches", params={"batchId": rid}, json=batch, timeout=60), "the Spark SQL batch")
            status = self._wait(f"{base}/batches/{rid}", lambda b: b.get("state"), {"SUCCEEDED"}, {"FAILED", "CANCELLED"},
                                lambda: self.http.post(f"{DATAPROC}/{op.get('name')}:cancel", timeout=60),
                                lambda b: self._cause((b.get("runtimeInfo") or {}).get("outputUri")) or b.get("stateMessage"))
        return self._rows(f"{folder}/rows/") if status else []

    def _wait(self, url: str, state_of: Any, done: set[str], failed: set[str], cancel: Any, why: Any) -> bool:
        deadline, delay = time.time() + self.timeout, 3.0
        while True:
            body = self._ok(self.http.get(url, timeout=60), "checking the query")
            state = state_of(body)
            if state in done:
                return True
            if state in failed:
                raise QueryRefused(f"Spark SQL failed: {(why(body) or state)[:500]}")
            if time.time() > deadline:
                cancel()
                raise QueryRefused(f"Spark SQL took longer than {self.timeout} seconds, so it was cancelled.")
            time.sleep(delay)
            delay = min(delay * 1.5, 15.0)

    def _cause(self, output_uri: str | None) -> str | None:
        """The error a failed job's driver printed, from its output in Cloud Storage: Dataproc's own message only says
        that it failed. The last exception, with what caused it, in a few lines."""
        if not output_uri:
            return None
        try:
            from urllib.parse import quote
            bucket, _, prefix = output_uri.removeprefix("gs://").partition("/")
            listing = self.http.get(f"{STORAGE}/b/{bucket}/o", params={"prefix": prefix, "fields": "items(name)"}, timeout=60).json()
            text = ""
            for item in sorted(i["name"] for i in listing.get("items", [])):
                r = self.http.get(f"{STORAGE}/b/{bucket}/o/{quote(item, safe='')}", params={"alt": "media"}, timeout=60)
                text += r.text if r.status_code == 200 else ""
        except Exception:
            return None
        lines = [l.strip() for l in text.splitlines()]
        found = [l for l in lines if re.search(r"(Exception|Error)(:|$)", l) and not l.startswith("at ") and " WARN " not in l]
        return " ".join(dict.fromkeys(found[-3:]))[:600] or None

    def _rows(self, prefix: str) -> list[dict[str, Any]]:
        listing = self._ok(self.http.get(f"{STORAGE}/b/{self.bucket}/o", params={"prefix": prefix, "fields": "items(name)"}, timeout=60), "listing the results")
        rows: list[dict[str, Any]] = []
        for item in sorted(i["name"] for i in listing.get("items", []) if i["name"].rsplit("/", 1)[-1].startswith("part-")):
            from urllib.parse import quote
            r = self.http.get(f"{STORAGE}/b/{self.bucket}/o/{quote(item, safe='')}", params={"alt": "media"}, timeout=120)
            if r.status_code != 200:
                raise QueryRefused(f"Couldn't read the results ({item}): {r.status_code}")
            rows += [json.loads(line) for line in r.text.splitlines() if line.strip()]
        return rows


def setup_statements(up: dict[str, Any]) -> list[str]:
    """The connector's setup, one statement each: a list, or text with statements ending in ";"."""
    setup = up.get("setup") or []
    if isinstance(setup, str):
        setup = [s for s in re.split(r";\s*(?:\n|$)", setup)]
    return [s.strip() for s in setup if s and s.strip()]


class _Sample:
    """Sample tables in DuckDB: <dir>/sql/<schema>/<table>.json (a list of rows), whatever the catalog. Queries are
    translated from the engine's dialect to DuckDB's."""

    def __init__(self, root: Path, dialect: str):
        import duckdb
        self.dialect, self.db, self.tables = dialect, duckdb.connect(), {}
        base = root / "sql"
        for path in sorted(base.glob("*/*.json")) if base.exists() else []:
            self.db.execute(f'CREATE SCHEMA IF NOT EXISTS "{path.parent.name}"')
            self.db.execute(f'CREATE TABLE "{path.parent.name}"."{path.stem}" AS SELECT * FROM read_json_auto(?)', [str(path)])
            self.tables[(path.parent.name.lower(), path.stem.lower())] = path

    def run(self, sql: str, labels: dict[str, str]) -> list[dict[str, Any]]:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read=self.dialect)
        for t in tree.find_all(exp.Table):                       # catalogs don't exist here: schema.table
            if t.args.get("catalog"):
                t.set("catalog", None)
        try:
            cur = self.db.execute(tree.sql(dialect="duckdb"))
        except Exception as exc:
            raise QueryRefused(f"The query failed on the sample tables: {str(exc).splitlines()[0][:300]}") from None
        cols = [d[0] for d in cur.description]
        return [_jsonable(dict(zip(cols, r))) for r in cur.fetchall()]

    def list_tables(self, schema: str) -> list[str]:
        return [f"{s}.{t}" for s, t in self.tables if s == schema.split(".")[-1].lower()]

    def describe(self, schema: str, table: str) -> list[dict[str, Any]]:
        return [{"name": c[0], "type": c[1]} for c in self.db.execute(f'DESCRIBE "{schema}"."{table}"').fetchall()]


# ------------------------------------------------------------------ the step's view

class Engine:
    """The step's Trino or Spark SQL, within its limits. `limits` comes from the signed limits token."""

    def __init__(self, limits: dict[str, Any], sample_root: Path | None = None):
        self.limits = limits
        self.service = limits.get("connection", "trino")
        self.up = limits.get("upstream") or {}
        live = limits.get("source") == "live"
        self.names = Names(self.service, self.up.get("catalog"), self.up.get("schema"))
        cap = self.up.get("max_seconds")
        cap = int(cap) if cap not in (None, "") and int(cap) > 0 else None        # empty or 0: no limit set
        self.timeout = min(int(limits.get("timeout_seconds") or (cap if cap is not None else DEFAULT_TIMEOUT[self.service])),
                           cap if cap is not None else 3600)
        self.max_rows = int(limits.get("max_rows") or 1000)
        self.allow = self._scope(live)
        self.engine: Any = ((_Trino if self.service == "trino" else _Dataproc)(self.up, self.timeout) if live
                            else _Sample(sample_root or Path(os.environ.get("AGENT_SERVICE_SAMPLE_DATA", ".")), DIALECT[self.service]))

    def _scope(self, live: bool) -> list[str]:
        """The step's data, narrowed to the connector's: a step can't read beyond what the admin allowed."""
        connector = list(self.up.get("allowed") or [])
        step = list(self.limits.get("datasets") or [])
        if not live:
            return step or connector
        if not step:
            return connector
        inside = [x for x in step if any(self.names.within(self.names.entry(x), self.names.entry(c)) for c in connector)]
        self.outside = [x for x in step if x not in inside]
        return inside

    outside: list[str] = []

    def _explain(self, exc: QueryRefused) -> QueryRefused:
        """A refusal that comes from the step naming data its connector doesn't allow says so: that's what to fix."""
        if not self.outside:
            return exc
        connector = ", ".join(self.up.get("allowed") or []) or "nothing yet"
        return QueryRefused(f"{exc} This step's Uses name {', '.join(self.outside)}, but {self.up.get('name') or 'its connector'} only allows "
                            f"{connector}: change the step's data to something within that, or ask an admin to allow more.")

    def _labels(self) -> dict[str, str]:
        return {k: re.sub(r"[^a-z0-9_-]", "-", str(v).lower())[:63] for k, v in
                {"agent": self.limits.get("agent", ""), "run": os.environ.get("CONDUCTOR_RUN_ID", ""),
                 "step": os.environ.get("AGENT_SERVICE_STEP", "")}.items() if v}

    def query(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            tree, tables = check(sql, params or {}, self.service, self.names, self.allow)
        except QueryRefused as exc:
            raise self._explain(exc) from None
        started = time.time()
        rows = self.engine.run(capped(tree, self.max_rows, DIALECT[self.service]), self._labels())
        return {"rows": rows[: self.max_rows], "row_count": min(len(rows), self.max_rows), "truncated": len(rows) > self.max_rows,
                "tables": tables, "elapsed_seconds": round(time.time() - started, 1)}

    def list_tables(self, schema: str) -> list[dict[str, Any]]:
        entry = self.names.entry(schema)
        if len(entry) != 2:
            raise QueryRefused(f"Name a schema: {'catalog.schema' if self.service == 'trino' else 'schema'}.")
        if isinstance(self.engine, _Sample):
            names = self.engine.list_tables(entry[1])
        else:
            rows = self.engine.run(f"SHOW TABLES IN {'.'.join(entry)}", self._labels())
            names = [f"{entry[1]}.{r.get('Table') or r.get('tableName') or next(iter(r.values()))}" for r in rows]
        out = []
        for n in names:
            t = (entry[0], *n.lower().split(".")[-2:])
            if any(self.names.within(t, self.names.entry(a)) for a in self.allow):
                out.append({"table": self.names.show(t)})
        return out

    def get_schema(self, table: str) -> dict[str, Any]:
        parts = [p.strip('`"') for p in table.split(".")]
        t = self.names.table(tuple(["", "", ""][: 3 - len(parts)] + parts[-3:]))     # type: ignore[arg-type]
        if not any(self.names.within(t, self.names.entry(a)) for a in self.allow):
            raise QueryRefused(f"This step may not read {self.names.show(t)}.")
        if isinstance(self.engine, _Sample):
            cols = self.engine.describe(t[1], t[2])
        else:
            rows = self.engine.run(f"DESCRIBE {self.names.show(t)}", self._labels())
            cols = [{"name": r.get("Column") or r.get("col_name"), "type": r.get("Type") or r.get("data_type")} for r in rows
                    if (r.get("Column") or r.get("col_name") or "").strip() and not str(r.get("col_name", "")).startswith("#")]
        return {"table": self.names.show(t), "columns": cols}


def check_connection(service: str, up: dict[str, Any]) -> str:
    """For the connector's Test: runs SELECT 1 (Trino: and lists the catalogs it can see)."""
    timeout = int(up["max_seconds"]) if up.get("max_seconds") not in (None, "") and int(up["max_seconds"]) > 0 else DEFAULT_TIMEOUT[service]
    if service == "trino":
        t = _Trino(up, min(timeout, 60))
        catalogs = [next(iter(r.values())) for r in t.run("SHOW CATALOGS", {})]
        return f"Reached {up.get('server')} as {up.get('user') or 'agent-orchestrator'}; catalogs: {', '.join(catalogs) or 'none'}."
    d = _Dataproc(up, timeout)
    started = time.time()
    rows = d.run("SELECT 1 AS ok", {"agent": "connector-test"})
    where = f"cluster {up.get('cluster')}" if up.get("mode") == "cluster" else "Dataproc Serverless"
    return f"Ran a test query on {where} in {up.get('region')} ({time.time() - started:.0f} s); results come back through gs://{d.bucket}/{d.prefix}." \
        if rows else f"The test query ran on {where} but returned nothing."
