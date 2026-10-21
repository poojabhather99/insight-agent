"""SQLite metadata store: datasets and agent runs (including the full step trace)."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, n_rows INTEGER NOT NULL,
  profile TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, question TEXT NOT NULL,
  status TEXT NOT NULL, answer TEXT, trace TEXT NOT NULL, steps INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL, llm TEXT NOT NULL, feedback INTEGER, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS runs_dataset ON runs(dataset_id, created_at);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def ping(self) -> bool:
        with self._db() as db:
            db.execute("SELECT 1")
        return True

    # datasets
    def add_dataset(self, ds_id: str, name: str, n_rows: int, profile: list[dict]) -> None:
        with self._db() as db:
            db.execute("INSERT INTO datasets VALUES (?,?,?,?,?)",
                       (ds_id, name, n_rows, json.dumps(profile), time.time()))

    def datasets(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM datasets ORDER BY created_at DESC").fetchall()
        return [self._ds(r) for r in rows]

    def dataset(self, ds_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM datasets WHERE id=?", (ds_id,)).fetchone()
        return self._ds(r) if r else None

    def delete_dataset(self, ds_id: str) -> bool:
        with self._db() as db:
            db.execute("DELETE FROM runs WHERE dataset_id=?", (ds_id,))
            return db.execute("DELETE FROM datasets WHERE id=?", (ds_id,)).rowcount > 0

    @staticmethod
    def _ds(r: sqlite3.Row) -> dict[str, Any]:
        return {"id": r["id"], "name": r["name"], "rows": r["n_rows"],
                "columns": json.loads(r["profile"]), "created_at": r["created_at"]}

    # runs
    def add_run(self, run: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,NULL,?)",
                (run["id"], run["dataset_id"], run["question"], run["status"], run.get("answer"),
                 json.dumps(run["trace"]), run["steps"], run["latency_ms"], run["llm"],
                 time.time()))

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(r) if r else None

    def runs(self, ds_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM runs WHERE dataset_id=? ORDER BY created_at DESC "
                              "LIMIT ?", (ds_id, limit)).fetchall()
        return [self._run(r) for r in rows]

    def set_feedback(self, run_id: str, value: int) -> bool:
        with self._db() as db:
            return db.execute("UPDATE runs SET feedback=? WHERE id=?", (value, run_id)).rowcount > 0

    @staticmethod
    def _run(r: sqlite3.Row) -> dict[str, Any]:
        return {"id": r["id"], "dataset_id": r["dataset_id"], "question": r["question"],
                "status": r["status"], "answer": r["answer"], "trace": json.loads(r["trace"]),
                "steps": r["steps"], "latency_ms": r["latency_ms"], "llm": r["llm"],
                "feedback": r["feedback"], "created_at": r["created_at"]}
