"""Deterministic synthetic sample dataset (no real data)."""

from __future__ import annotations

import csv
import io
import random
from datetime import date, timedelta

DISTRICTS = ["North", "South", "East", "West"]
TYPES = ["antenatal", "postnatal", "immunisation", "nutrition"]


def visits_csv(n: int = 1200, seed: int = 3) -> bytes:
    rng = random.Random(seed)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["visit_id", "visit_date", "district", "visit_type", "worker_id",
                "duration_min", "completed"])
    start = date(2026, 1, 1)
    for i in range(1, n + 1):
        d = start + timedelta(days=rng.randrange(0, 180))
        district = rng.choice(DISTRICTS)
        done = rng.random() < (0.9 if district in ("North", "East") else 0.78)
        w.writerow([i, d.isoformat(), district, rng.choice(TYPES), f"W{rng.randrange(1, 41):03d}",
                    rng.randrange(8, 75) if done else rng.randrange(1, 12),
                    "true" if done else "false"])
    return out.getvalue().encode()
