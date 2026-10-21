"""LLM backends. `complete(messages, ctx) -> str` must return the agent's next JSON action.

OfflineLLM is a deterministic rule-based planner so the product runs with no key or network.
OpenAICompatLLM talks to any OpenAI-compatible chat endpoint (Groq, Hugging Face router).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .config import Settings


class LLMError(Exception):
    pass


@dataclass
class AgentContext:
    question: str
    schema: list[dict[str, Any]]
    n_rows: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


class LLM(Protocol):
    name: str

    def complete(self, messages: list[dict[str, str]], ctx: AgentContext) -> str: ...


class ScriptedLLM:
    """Returns canned replies in order. Used in tests."""

    name = "scripted"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, messages, ctx):
        self.calls += 1
        if not self.replies:
            raise LLMError("script exhausted")
        return self.replies.pop(0)


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, name: str,
                 timeout: float = 45.0, client: httpx.Client | None = None, retries: int = 2):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.name = api_key, model, name
        self.client = client or httpx.Client(timeout=timeout)
        self.retries = retries

    def complete(self, messages, ctx):
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 700}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last = ""
        for attempt in range(self.retries + 1):
            try:
                r = self.client.post(self.url, json=body, headers=headers)
            except httpx.HTTPError as e:
                last = type(e).__name__
            else:
                if r.status_code == 200:
                    try:
                        return r.json()["choices"][0]["message"]["content"] or ""
                    except (KeyError, IndexError, ValueError):
                        raise LLMError("unexpected response shape from the model API") from None
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise LLMError(f"model API returned HTTP {r.status_code}")
                last = f"HTTP {r.status_code}"
            time.sleep(min(2**attempt, 4) * 0.5)
        raise LLMError(f"model API unavailable ({last})")


def build_llm(settings: Settings) -> LLM:
    from .offline import OfflineLLM

    p = settings.llm_provider.lower()
    if p in ("auto", "groq") and settings.groq_api_key:
        return OpenAICompatLLM("https://api.groq.com/openai/v1", settings.groq_api_key,
                               settings.llm_model or "llama-3.3-70b-versatile", "groq")
    if p in ("auto", "hf") and settings.hf_token:
        return OpenAICompatLLM("https://router.huggingface.co/v1", settings.hf_token,
                               settings.llm_model or "meta-llama/Llama-3.3-70B-Instruct", "hf")
    return OfflineLLM()
