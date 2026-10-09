"""FallbackLLMClient: primary first, same-family fallback on any transport failure."""

import json

import httpx
import pytest

from backend.llm.base import FallbackLLMClient, LLMClient, LLMHTTPError, RetryPolicy
from backend.llm.cerebras_client import correct_sql, explain_sql

SCHEMA = {
    "orders": {
        "columns": [{"name": "id", "type": "integer", "nullable": False}],
        "primary_key": ["id"],
        "foreign_keys": [],
    }
}


def _completion(content: str, model: str) -> dict:
    return {
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


def _client(provider: str, model: str, handler) -> LLMClient:
    base_url = f"https://{provider}.test"
    return LLMClient(
        provider=provider,
        base_url=base_url,
        chat_path="/chat/completions",
        api_key="test_key_abcd",
        default_model=model,
        retry_policy=RetryPolicy(max_retries=1, base_seconds=0.0, max_seconds=0.0),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url),
    )


class _Recorder:
    """Counts calls and remembers the model each request asked for."""

    def __init__(self, status: int, content: str = "", model: str = "") -> None:
        self.status = status
        self.content = content
        self.model = model
        self.calls = 0
        self.models: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.models.append(json.loads(request.content)["model"])
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "nope"})
        return httpx.Response(200, json=_completion(self.content, self.model))


@pytest.mark.asyncio
async def test_primary_success_never_touches_fallback():
    primary = _Recorder(200, "Counts the orders.", "gpt-oss-120b")
    fallback = _Recorder(200, "unused", "openai/gpt-oss-120b")
    client = FallbackLLMClient(
        _client("cerebras", "gpt-oss-120b", primary),
        _client("groq-fallback", "openai/gpt-oss-120b", fallback),
    )

    text = await explain_sql("how many orders", "SELECT COUNT(*) FROM orders", client=client)

    assert text == "Counts the orders."
    assert (primary.calls, fallback.calls) == (1, 0)


@pytest.mark.asyncio
async def test_quota_error_falls_back_with_fallbacks_own_model():
    # 402 is the exhausted-free-tier response; it is not retryable, so the
    # primary is called exactly once before the fallback takes over.
    primary = _Recorder(402)
    body = json.dumps({"sql": "SELECT id FROM orders", "reasoning": "fixed"})
    fallback = _Recorder(200, body, "openai/gpt-oss-120b")
    client = FallbackLLMClient(
        _client("cerebras", "gpt-oss-120b", primary),
        _client("groq-fallback", "openai/gpt-oss-120b", fallback),
    )

    result = await correct_sql(
        "ids?", SCHEMA, "SELECT idd FROM orders", "column idd does not exist", client=client
    )

    assert result.sql == "SELECT id FROM orders"
    assert (primary.calls, fallback.calls) == (1, 1)
    assert fallback.models == ["openai/gpt-oss-120b"]


@pytest.mark.asyncio
async def test_rate_limit_falls_back_after_retry_budget():
    primary = _Recorder(429)
    fallback = _Recorder(200, "Counts the orders.", "openai/gpt-oss-120b")
    client = FallbackLLMClient(
        _client("cerebras", "gpt-oss-120b", primary),
        _client("groq-fallback", "openai/gpt-oss-120b", fallback),
    )

    text = await explain_sql("how many orders", "SELECT COUNT(*) FROM orders", client=client)

    assert text == "Counts the orders."
    assert primary.calls == 2  # initial attempt + max_retries=1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_both_failing_raises_the_fallbacks_error():
    client = FallbackLLMClient(
        _client("cerebras", "gpt-oss-120b", _Recorder(402)),
        _client("groq-fallback", "openai/gpt-oss-120b", _Recorder(403)),
    )

    with pytest.raises(LLMHTTPError) as raised:
        await explain_sql("q", "SELECT 1", client=client)

    assert raised.value.provider == "groq-fallback"
    assert raised.value.status_code == 403
