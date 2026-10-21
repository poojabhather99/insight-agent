"""Read-only SQL execution. Three independent layers:

1. The statement is parsed by DuckDB itself and must be exactly one SELECT.
2. The database file is opened read-only.
3. External access is disabled, so file functions (read_csv, COPY, ATTACH) fail.

On top of that a timer interrupts long queries and results are capped.
"""

from __future__ import annotations

import datetime as dt
import decimal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


class QueryError(Exception):
    """A query that was rejected or failed. The message is safe to show to the model."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "truncated": self.truncated,
                "elapsed_ms": self.elapsed_ms}


def jsonable(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return v


def check_statement(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("empty query")
    if len(sql) > 4000:
        raise QueryError("query too long (max 4000 characters)")
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as e:
        raise QueryError(f"syntax error: {_clean(e)}") from None
    if len(statements) != 1:
        raise QueryError("exactly one statement is allowed")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise QueryError("only SELECT queries are allowed")


def run_query(db_path: Path, sql: str, max_rows: int = 200, timeout_s: int = 10) -> QueryResult:
    sql = (sql or "").strip().rstrip(";").strip()
    con = duckdb.connect(str(db_path), read_only=True, config={"enable_external_access": False})
    timer = threading.Timer(timeout_s, con.interrupt)
    try:
        check_statement(con, sql)
        timer.start()
        t0 = time.monotonic()
        try:
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException:
            raise QueryError(f"query exceeded the {timeout_s}s time limit") from None
        except duckdb.Error as e:
            raise QueryError(_clean(e)) from None
        elapsed = int((time.monotonic() - t0) * 1000)
        truncated = len(rows) > max_rows
        rows = [[jsonable(v) for v in r] for r in rows[:max_rows]]
        return QueryResult(columns, rows, truncated, elapsed)
    finally:
        timer.cancel()
        con.close()


def _clean(e: Exception) -> str:
    return str(e).splitlines()[0][:300] if str(e) else type(e).__name__
