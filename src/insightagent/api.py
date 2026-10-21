"""HTTP API. Everything the UI does goes through here, so it is also the public interface."""

from __future__ import annotations

import hmac
import json
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agent import Agent, execute
from .config import Settings
from .datasets import Datasets, IngestError
from .llm import LLM, build_llm
from .observability import Metrics, log_event, setup_logging
from .samples import visits_csv
from .sandbox import QueryError, run_query
from .store import Store

UI_DIR = Path(__file__).parent / "ui"
MAX_CONCURRENT_RUNS = 4


class AskReq(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class FeedbackReq(BaseModel):
    value: int = Field(ge=-1, le=1)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> int:
        """Return 0 if allowed, else seconds to wait."""
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return max(1, int(60 - (now - q[0])))
            q.append(now)
            if len(self.hits) > 5000:
                self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v})
        return 0


def _trim_trace(trace: list[dict]) -> list[dict]:
    out = []
    for ev in trace:
        ev = dict(ev)
        res = ev.get("result")
        if isinstance(res, dict) and "rows" in res:
            ev["result"] = {**res, "rows": res["rows"][:50]}
        out.append(ev)
    return out


def create_app(settings: Settings | None = None, llm: LLM | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log = setup_logging(settings.log_level)
    store = Store(settings.data_dir / "meta.db")
    datasets = Datasets(settings, store)
    model = llm or build_llm(settings)
    metrics = Metrics()
    limiter = RateLimiter(settings.rate_limit_per_min)
    run_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)

    app = FastAPI(title="Insight Agent", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    def client_ip(request: Request) -> str:
        if settings.trust_proxy:
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def wrap(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:16]
        t0 = time.monotonic()
        path = request.url.path
        if path.startswith("/api/") and request.method in ("POST", "DELETE"):
            wait = limiter.check(client_ip(request))
            if wait:
                metrics.inc("insightagent_rate_limited_total")
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                                    headers={"Retry-After": str(wait), "X-Request-ID": rid})
        response = await call_next(request)
        dt = time.monotonic() - t0
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'self' https://huggingface.co https://*.hf.space")
        route = request.scope.get("route")
        label = getattr(route, "path", "static")
        metrics.inc("insightagent_http_requests_total", method=request.method, route=label,
                    status=response.status_code)
        metrics.observe("insightagent_http_seconds", dt, route=label)
        if path.startswith("/api/") and path not in ("/api/health", "/api/ready"):
            log_event(log, "request", request_id=rid, method=request.method, path=path,
                      status=response.status_code, ms=int(dt * 1000))
        return response

    def require_key(request: Request) -> None:
        if not settings.api_key:
            return
        supplied = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = supplied or auth[7:]
        if not hmac.compare_digest(supplied.encode(), settings.api_key.encode()):
            raise HTTPException(401, "missing or invalid API key")

    auth = [Depends(require_key)]

    def get_dataset(ds_id: str) -> dict:
        ds = store.dataset(ds_id) if len(ds_id) == 12 else None
        if not ds:
            raise HTTPException(404, "dataset not found")
        return ds

    # ---- health ----
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/ready")
    def ready():
        try:
            store.ping()
            probe = settings.data_dir / ".probe"
            probe.write_text("ok")
            probe.unlink()
        except Exception:
            raise HTTPException(503, "not ready") from None
        return {"status": "ready"}

    @app.get("/metrics", dependencies=auth, response_class=PlainTextResponse)
    def metrics_endpoint():
        return metrics.render()

    @app.get("/api/config")
    def config():
        s = settings
        return {"llm": model.name, "auth_required": bool(s.api_key), "version": __version__,
                "limits": {"max_upload_mb": s.max_upload_mb, "max_rows": s.max_rows,
                           "max_steps": s.max_steps, "query_timeout_s": s.query_timeout_s,
                           "max_result_rows": s.max_result_rows}}

    # ---- datasets ----
    @app.post("/api/datasets", dependencies=auth, status_code=201)
    def upload(file: UploadFile = File(...)):  # noqa: B008
        data = file.file.read(settings.max_upload_bytes + 1)
        try:
            ds = datasets.ingest(file.filename or "dataset.csv", data)
        except IngestError as e:
            raise HTTPException(413 if "larger" in str(e) else 422, str(e)) from None
        metrics.inc("insightagent_datasets_created_total")
        return ds

    @app.post("/api/datasets/sample", dependencies=auth, status_code=201)
    def sample():
        return datasets.ingest("sample_visits.csv", visits_csv())

    @app.get("/api/datasets", dependencies=auth)
    def list_datasets():
        return store.datasets()

    @app.get("/api/datasets/{ds_id}", dependencies=auth)
    def dataset(ds_id: str):
        return get_dataset(ds_id)

    @app.delete("/api/datasets/{ds_id}", dependencies=auth, status_code=204)
    def delete_dataset(ds_id: str):
        get_dataset(ds_id)
        datasets.delete(ds_id)

    @app.get("/api/datasets/{ds_id}/preview", dependencies=auth)
    def preview(ds_id: str, limit: int = 15):
        get_dataset(ds_id)
        try:
            n = max(1, min(limit, 50))
            res = run_query(datasets.db_path(ds_id), f"SELECT * FROM data LIMIT {n}")
        except QueryError as e:
            raise HTTPException(400, str(e)) from None
        return res.as_dict()

    # ---- asking ----
    def make_agent(ds: dict) -> Agent:
        return Agent(model, datasets.db_path(ds["id"]), ds["columns"], ds["rows"], settings)

    def persist(ds_id: str, question: str, out: dict) -> None:
        final = out["final"]
        answer = final["answer"] if final else (out["error"] or {}).get("message")
        store.add_run({"id": out["id"], "dataset_id": ds_id, "question": question,
                       "status": out["status"], "answer": answer,
                       "trace": _trim_trace(out["trace"]), "steps": out["steps"],
                       "latency_ms": out["latency_ms"], "llm": out["llm"]})
        metrics.inc("insightagent_agent_runs_total", status=out["status"], llm=out["llm"])
        metrics.observe("insightagent_agent_seconds", out["latency_ms"] / 1000)
        log_event(log, "agent_run", run_id=out["id"], dataset=ds_id, status=out["status"],
                  steps=out["steps"], ms=out["latency_ms"], llm=out["llm"])

    @app.post("/api/datasets/{ds_id}/ask", dependencies=auth)
    def ask(ds_id: str, body: AskReq):
        ds = get_dataset(ds_id)
        if not run_slots.acquire(blocking=False):
            raise HTTPException(503, "too many analyses running; try again in a moment")
        try:
            out = execute(make_agent(ds), body.question)
        finally:
            run_slots.release()
        persist(ds_id, body.question, out)
        return {"run_id": out["id"], "status": out["status"], "final": out["final"],
                "error": out["error"], "steps": out["steps"], "latency_ms": out["latency_ms"],
                "trace": _trim_trace(out["trace"])}

    @app.post("/api/datasets/{ds_id}/ask/stream", dependencies=auth)
    def ask_stream(ds_id: str, body: AskReq):
        ds = get_dataset(ds_id)
        if not run_slots.acquire(blocking=False):
            raise HTTPException(503, "too many analyses running; try again in a moment")
        agent = make_agent(ds)

        def gen() -> Iterator[str]:
            t0 = time.monotonic()
            trace: list[dict] = []
            final = error = None
            try:
                for ev in agent.stream(body.question):
                    trace.append(ev)
                    final = ev if ev["type"] == "final" else final
                    error = ev if ev["type"] == "error" else error
                    yield f"data: {json.dumps(_trim_trace([ev])[0], default=str)}\n\n"
            finally:
                run_slots.release()
            steps = sum(1 for e in trace if e["type"] == "step")
            status = "ok" if final and not final["refused"] else "refused" if final else "failed"
            out = {"id": uuid.uuid4().hex[:12], "status": status, "final": final, "error": error,
                   "trace": trace, "steps": steps, "llm": model.name,
                   "latency_ms": int((time.monotonic() - t0) * 1000)}
            persist(ds_id, body.question, out)
            done = {"type": "done", "run_id": out["id"], "status": status,
                    "latency_ms": out["latency_ms"]}
            yield f"data: {json.dumps(done)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/datasets/{ds_id}/runs", dependencies=auth)
    def runs(ds_id: str):
        get_dataset(ds_id)
        return [{k: r[k] for k in ("id", "question", "status", "answer", "steps", "latency_ms",
                                   "feedback", "created_at")} for r in store.runs(ds_id)]

    @app.get("/api/runs/{run_id}", dependencies=auth)
    def run(run_id: str):
        r = store.run(run_id)
        if not r:
            raise HTTPException(404, "run not found")
        return r

    @app.post("/api/runs/{run_id}/feedback", dependencies=auth)
    def feedback(run_id: str, body: FeedbackReq):
        if not store.set_feedback(run_id, body.value):
            raise HTTPException(404, "run not found")
        metrics.inc("insightagent_feedback_total", value=body.value)
        return {"ok": True}

    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
    return app


def app_factory() -> FastAPI:
    return create_app()
