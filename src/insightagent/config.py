"""Settings read from environment variables. Defaults are safe for local use."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "./data")))
    api_key: str = field(default_factory=lambda: os.environ.get("API_KEY", ""))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 10))
    max_rows: int = field(default_factory=lambda: _int("MAX_ROWS", 500_000))
    max_columns: int = field(default_factory=lambda: _int("MAX_COLUMNS", 200))
    max_datasets: int = field(default_factory=lambda: _int("MAX_DATASETS", 50))
    query_timeout_s: int = field(default_factory=lambda: _int("QUERY_TIMEOUT_S", 10))
    max_result_rows: int = field(default_factory=lambda: _int("MAX_RESULT_ROWS", 200))
    max_steps: int = field(default_factory=lambda: _int("MAX_STEPS", 6))
    rate_limit_per_min: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MIN", 60))
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "auto"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", ""))
    groq_api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    hf_token: str = field(default_factory=lambda: os.environ.get("HF_TOKEN", ""))
    trust_proxy: bool = field(default_factory=lambda: os.environ.get("TRUST_PROXY", "") == "1")
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024
