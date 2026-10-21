"""JSON logging and a tiny Prometheus-format metrics registry (no extra dependencies)."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": round(record.created, 3), "level": record.levelname,
               "logger": record.name, "msg": record.getMessage()}
        out.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    log = logging.getLogger("insightagent")
    log.setLevel(level.upper())
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.propagate = False
    return log


def log_event(log: logging.Logger, msg: str, **fields) -> None:
    log.info(msg, extra={"extra_fields": fields})


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self.hist: dict[tuple[str, tuple], list[float]] = {}
        self.start = time.time()

    def inc(self, name: str, **labels) -> None:
        with self._lock:
            self.counters[(name, tuple(sorted(labels.items())))] += 1

    def observe(self, name: str, seconds: float, **labels) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            h = self.hist.setdefault(key, [0.0] * (len(BUCKETS) + 2))
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[i] += 1
            h[len(BUCKETS)] += 1
            h[len(BUCKETS) + 1] += seconds

    @staticmethod
    def _lbl(labels: tuple, extra: str = "") -> str:
        parts = [f'{k}="{v}"' for k, v in labels] + ([extra] if extra else [])
        return "{" + ",".join(parts) + "}" if parts else ""

    def render(self) -> str:
        lines = ["# TYPE insightagent_uptime_seconds gauge",
                 f"insightagent_uptime_seconds {time.time() - self.start:.0f}"]
        with self._lock:
            for (name, labels), v in sorted(self.counters.items()):
                lines.append(f"{name}{self._lbl(labels)} {v:g}")
            for (name, labels), h in sorted(self.hist.items()):
                for i, b in enumerate(BUCKETS):
                    le = 'le="' + str(b) + '"'
                    lines.append(f"{name}_bucket{self._lbl(labels, le)} {h[i]:g}")
                inf = 'le="+Inf"'
                lines.append(f"{name}_bucket{self._lbl(labels, inf)} {h[len(BUCKETS)]:g}")
                lines.append(f"{name}_sum{self._lbl(labels)} {h[len(BUCKETS) + 1]:.4f}")
                lines.append(f"{name}_count{self._lbl(labels)} {h[len(BUCKETS)]:g}")
        return "\n".join(lines) + "\n"
