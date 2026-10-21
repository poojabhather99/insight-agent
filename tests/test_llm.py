import httpx
import pytest

from insightagent.config import Settings
from insightagent.llm import AgentContext, LLMError, OpenAICompatLLM, build_llm
from insightagent.offline import OfflineLLM

CTX = AgentContext(question="q", schema=[])


def llm(handler, retries=2):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    m = OpenAICompatLLM("https://x.test/v1", "key", "m", "test", client=client, retries=retries)
    return m


def ok(content="hi"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_success_sends_auth_and_model(monkeypatch):
    seen = {}

    def h(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.read()
        return ok('{"action":"final"}')

    assert llm(h).complete([{"role": "user", "content": "x"}], CTX) == '{"action":"final"}'
    assert seen["auth"] == "Bearer key" and b'"temperature":0' in seen["body"].replace(b" ", b"")


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else ok()

    assert llm(h).complete([], CTX) == "hi" and len(calls) == 3


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(LLMError, match="unavailable"):
        llm(lambda r: httpx.Response(503)).complete([], CTX)


def test_client_errors_do_not_retry():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(LLMError, match="401"):
        llm(h).complete([], CTX)
    assert len(calls) == 1


def test_bad_shape_and_network_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(LLMError, match="unexpected"):
        llm(lambda r: httpx.Response(200, json={"nope": 1})).complete([], CTX)

    def boom(req):
        raise httpx.ConnectError("x")

    with pytest.raises(LLMError, match="unavailable"):
        llm(boom).complete([], CTX)


def test_build_llm_selection(tmp_path):
    base = Settings(data_dir=tmp_path, groq_api_key="", hf_token="")
    assert isinstance(build_llm(base), OfflineLLM)
    import dataclasses

    assert build_llm(dataclasses.replace(base, groq_api_key="g")).name == "groq"
    assert build_llm(dataclasses.replace(base, hf_token="h")).name == "hf"
    both = dataclasses.replace(base, groq_api_key="g", hf_token="h", llm_provider="hf")
    assert build_llm(both).name == "hf"
    assert isinstance(build_llm(dataclasses.replace(base, llm_provider="offline",
                                                    groq_api_key="g")), OfflineLLM)
