from pathlib import Path

import pytest

from insightagent.config import Settings
from insightagent.datasets import Datasets
from insightagent.samples import visits_csv
from insightagent.store import Store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, api_key="", rate_limit_per_min=1000)


@pytest.fixture
def env(settings):
    store = Store(settings.data_dir / "meta.db")
    ds = Datasets(settings, store)
    meta = ds.ingest("visits.csv", visits_csv(400))
    return settings, store, ds, meta, ds.db_path(meta["id"])
