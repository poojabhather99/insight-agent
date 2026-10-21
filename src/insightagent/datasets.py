"""Dataset ingestion: CSV bytes -> one DuckDB file per dataset, plus a column profile."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import duckdb

from .config import Settings
from .store import Store

TABLE = "data"


class IngestError(Exception):
    pass


def safe_name(name: str) -> str:
    base = Path(name or "dataset").name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:80].strip() or "dataset"
    return base


class Datasets:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.root = settings.data_dir / "datasets"
        self.root.mkdir(parents=True, exist_ok=True)

    def db_path(self, ds_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", ds_id):
            raise KeyError(ds_id)
        return self.root / f"{ds_id}.duckdb"

    def ingest(self, filename: str, data: bytes) -> dict:
        s = self.settings
        if not data:
            raise IngestError("the file is empty")
        if len(data) > s.max_upload_bytes:
            raise IngestError(f"file is larger than {s.max_upload_mb} MB")
        if b"\x00" in data[:4096]:
            raise IngestError("this does not look like a CSV text file")
        if len(self.store.datasets()) >= s.max_datasets:
            raise IngestError("dataset limit reached; delete one first")
        ds_id = uuid.uuid4().hex[:12]
        tmp = self.root / f"{ds_id}.upload.csv"
        path = self.db_path(ds_id)
        tmp.write_bytes(data)
        try:
            con = duckdb.connect(str(path))
            try:
                con.execute(f"CREATE TABLE {TABLE} AS SELECT * FROM "
                            "read_csv_auto(?, header=true, normalize_names=true)", [str(tmp)])
                n_rows = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
                if n_rows == 0:
                    raise IngestError("the CSV has a header but no rows")
                if n_rows > s.max_rows:
                    raise IngestError(f"too many rows ({n_rows}); the limit is {s.max_rows}")
                profile = self._profile(con)
                if len(profile) > s.max_columns:
                    raise IngestError(f"too many columns; the limit is {s.max_columns}")
            finally:
                con.close()
        except IngestError:
            self._cleanup(path)
            raise
        except duckdb.Error as e:
            self._cleanup(path)
            raise IngestError(f"could not read the CSV: {str(e).splitlines()[0][:200]}") from None
        finally:
            tmp.unlink(missing_ok=True)
        name = safe_name(filename)
        self.store.add_dataset(ds_id, name, n_rows, profile)
        return self.store.dataset(ds_id)

    def delete(self, ds_id: str) -> bool:
        ok = self.store.delete_dataset(ds_id)
        self._cleanup(self.db_path(ds_id))
        return ok

    @staticmethod
    def _cleanup(path: Path) -> None:
        for p in (path, Path(str(path) + ".wal")):
            p.unlink(missing_ok=True)

    @staticmethod
    def _profile(con: duckdb.DuckDBPyConnection) -> list[dict]:
        cols = con.execute(f"DESCRIBE {TABLE}").fetchall()
        out = []
        for name, typ, *_ in cols:
            q = '"' + name.replace('"', '""') + '"'
            nulls, distinct = con.execute(
                f"SELECT COUNT(*) - COUNT({q}), COUNT(DISTINCT {q}) FROM {TABLE}").fetchone()
            info = {"name": name, "type": typ, "nulls": nulls, "distinct": distinct}
            kind = typ.upper()
            if any(k in kind for k in ("INT", "DOUBLE", "DECIMAL", "FLOAT", "DATE", "TIME")):
                lo, hi = con.execute(f"SELECT MIN({q}), MAX({q}) FROM {TABLE}").fetchone()
                info["min"], info["max"] = str(lo), str(hi)
            if kind == "VARCHAR" and 0 < distinct <= 20:
                vals = con.execute(f"SELECT DISTINCT {q} FROM {TABLE} WHERE {q} IS NOT NULL "
                                   "ORDER BY 1 LIMIT 8").fetchall()
                info["values"] = [str(v[0])[:40] for v in vals]
            out.append(info)
        return out
