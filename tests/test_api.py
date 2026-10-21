import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from insightagent.api import create_app
from insightagent.llm import ScriptedLLM
from insightagent.samples import visits_csv


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def ds_id(client):
    return client.post("/api/datasets/sample").json()["id"]


def test_health_ready_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/ready").json()["status"] == "ready"
    c = client.get("/api/config").json()
    assert c["llm"] == "offline" and c["limits"]["max_steps"] == 6


def test_security_headers_and_request_id(client):
    r = client.get("/api/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["x-request-id"] == "abc123"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Insight Agent" in r.text
    assert client.get("/app.js").status_code == 200


def test_upload_list_preview_delete(client):
    r = client.post("/api/datasets", files={"file": ("my data.csv", visits_csv(50), "text/csv")})
    assert r.status_code == 201
    ds = r.json()
    assert ds["rows"] == 50 and ds["name"] == "my data.csv"
    assert [d["id"] for d in client.get("/api/datasets").json()] == [ds["id"]]
    pv = client.get(f"/api/datasets/{ds['id']}/preview?limit=5").json()
    assert len(pv["rows"]) == 5
    assert client.delete(f"/api/datasets/{ds['id']}").status_code == 204
    assert client.get(f"/api/datasets/{ds['id']}").status_code == 404


def test_upload_errors(client, settings):
    assert client.post("/api/datasets", files={"file": ("e.csv", b"", "text/csv")}).status_code == 422
    small = TestClient(create_app(dataclasses.replace(settings, max_upload_mb=0)))
    assert small.post("/api/datasets", files={"file": ("a.csv", b"a\n1\n", "text/csv")}).status_code == 413


def test_ask_returns_answer_chart_and_persists_run(client, ds_id):
    r = client.post(f"/api/datasets/{ds_id}/ask", json={"question": "completion rate by district"})
    j = r.json()
    assert r.status_code == 200 and j["status"] == "ok"
    assert j["final"]["chart"]["type"] == "bar" and "North" in j["final"]["answer"]
    run = client.get(f"/api/runs/{j['run_id']}").json()
    assert run["question"] == "completion rate by district" and run["steps"] == 3
    runs = client.get(f"/api/datasets/{ds_id}/runs").json()
    assert runs[0]["id"] == j["run_id"]


def test_ask_refuses_destructive_requests(client, ds_id):
    j = client.post(f"/api/datasets/{ds_id}/ask", json={"question": "Drop the data table"}).json()
    assert j["status"] == "refused"
    assert client.get(f"/api/datasets/{ds_id}").json()["rows"] == 1200


def test_stream_emits_steps_then_done(client, ds_id):
    r = client.post(f"/api/datasets/{ds_id}/ask/stream", json={"question": "Monthly trend of visits"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "step" and "final" in types and types[-1] == "done"
    assert client.get(f"/api/runs/{events[-1]['run_id']}").status_code == 200


def test_validation_and_not_found(client, ds_id):
    assert client.post(f"/api/datasets/{ds_id}/ask", json={"question": "x"}).status_code == 422
    assert client.post("/api/datasets/aaaaaaaaaaaa/ask", json={"question": "how many rows"}).status_code == 404
    assert client.get("/api/datasets/not-an-id").status_code == 404
    assert client.get("/api/runs/nope").status_code == 404


def test_feedback(client, ds_id):
    run_id = client.post(f"/api/datasets/{ds_id}/ask", json={"question": "how many rows"}).json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/feedback", json={"value": 1}).status_code == 200
    assert client.get(f"/api/runs/{run_id}").json()["feedback"] == 1
    assert client.post(f"/api/runs/{run_id}/feedback", json={"value": 5}).status_code == 422
    assert client.post("/api/runs/none/feedback", json={"value": 1}).status_code == 404


def test_api_key_required_when_configured(settings):
    c = TestClient(create_app(dataclasses.replace(settings, api_key="s3cret")))
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/datasets").status_code == 401
    assert c.get("/api/datasets", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/api/datasets", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert c.get("/api/datasets", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/metrics").status_code == 401


def test_rate_limit(settings):
    c = TestClient(create_app(dataclasses.replace(settings, rate_limit_per_min=3)))
    codes = [c.post("/api/datasets/sample").status_code for _ in range(5)]
    assert codes[:3] == [201, 201, 201] and codes[3] == 429
    r = c.post("/api/datasets/sample")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert c.get("/api/health").status_code == 200


def test_metrics_exposed(client, ds_id):
    client.post(f"/api/datasets/{ds_id}/ask", json={"question": "how many rows"})
    text = client.get("/metrics").text
    assert 'insightagent_agent_runs_total{llm="offline",status="ok"} 1' in text
    assert "insightagent_http_requests_total" in text


def test_llm_failure_is_a_clean_run_not_a_500(settings):
    c = TestClient(create_app(settings, llm=ScriptedLLM([])))
    ds = c.post("/api/datasets/sample").json()["id"]
    r = c.post(f"/api/datasets/{ds}/ask", json={"question": "how many rows"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert r.json()["error"]["code"] == "llm_unavailable"
