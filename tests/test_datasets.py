import dataclasses

import pytest

from insightagent.datasets import Datasets, IngestError, safe_name
from insightagent.samples import visits_csv
from insightagent.store import Store


def test_ingest_profiles_columns(env):
    meta = env[3]
    cols = {c["name"]: c for c in meta["columns"]}
    assert meta["rows"] == 400 and cols["district"]["type"] == "VARCHAR"
    assert set(cols["district"]["values"]) == {"North", "South", "East", "West"}
    assert cols["completed"]["type"] == "BOOLEAN" and "min" in cols["duration_min"]


@pytest.mark.parametrize("data,msg", [(b"", "empty"), (b"a,b\n", "no rows"),
                                       (b"\x00\x01\x02binary", "CSV")])
def test_bad_files(env, data, msg):
    with pytest.raises(IngestError, match=msg):
        env[2].ingest("x.csv", data)


def test_size_limit(env):
    settings, store, _, _, _ = env
    ds = Datasets(dataclasses.replace(settings, max_upload_mb=0), store)
    with pytest.raises(IngestError, match="larger"):
        ds.ingest("x.csv", b"a\n1\n")


def test_row_limit_cleans_up(env):
    settings, store, _, _, _ = env
    ds = Datasets(dataclasses.replace(settings, max_rows=5), store)
    before = sorted(p.name for p in ds.root.iterdir())
    with pytest.raises(IngestError, match="too many rows"):
        ds.ingest("x.csv", visits_csv(50))
    assert sorted(p.name for p in ds.root.iterdir()) == before


def test_dataset_limit(env):
    settings, store, _, _, _ = env
    ds = Datasets(dataclasses.replace(settings, max_datasets=1), store)
    with pytest.raises(IngestError, match="limit"):
        ds.ingest("x.csv", b"a\n1\n")


def test_ragged_csv_reports_error(env):
    with pytest.raises(IngestError):
        env[2].ingest("x.csv", b"a,b\n1,2\n3\n4,5,6,7\n")


def test_delete_removes_files_and_runs(env):
    settings, store, ds, meta, db = env
    assert db.exists()
    assert ds.delete(meta["id"]) and not db.exists() and store.dataset(meta["id"]) is None


def test_db_path_rejects_traversal(env):
    with pytest.raises(KeyError):
        env[2].db_path("../../etc/passwd")


def test_safe_name():
    assert safe_name("../../evil name.csv") == "evil name.csv"
    assert safe_name("") == "dataset"


def test_store_roundtrip(tmp_path):
    s = Store(tmp_path / "m.db")
    s.add_run({"id": "r1", "dataset_id": "d", "question": "q", "status": "ok", "answer": "a",
               "trace": [{"type": "final"}], "steps": 1, "latency_ms": 5, "llm": "offline"})
    assert s.set_feedback("r1", 1) and s.run("r1")["feedback"] == 1
    assert not s.set_feedback("missing", 1)
