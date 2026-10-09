"""Groq generation-tier tests. HTTP is mocked; no network unless RUN_LIVE_LLM_TESTS=1."""

import json
import os

import httpx
import pytest

from backend.llm.base import LLMClient, LLMRetryExhausted, RetryPolicy
from backend.llm.groq_client import (
    GROQ_BASE_URL,
    GROQ_CHAT_PATH,
    GROQ_DEFAULT_MODEL,
    generate_sql,
)

SCHEMA = {
    "orders": {
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "customer_id", "type": "integer", "nullable": False},
            {"name": "total", "type": "numeric", "nullable": True},
        ],
        "primary_key": ["id"],
        "foreign_keys": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
    }
}


def _completion(content: str, model: str = GROQ_DEFAULT_MODEL) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
    }


def _client(handler, **policy_kwargs) -> LLMClient:
    transport = httpx.MockTransport(handler)
    return LLMClient(
        provider="groq",
        base_url=GROQ_BASE_URL,
        chat_path=GROQ_CHAT_PATH,
        api_key="gsk_test_key_abcd",
        default_model=GROQ_DEFAULT_MODEL,
        retry_policy=RetryPolicy(base_seconds=0.0, max_seconds=0.0, **policy_kwargs),
        client=httpx.AsyncClient(transport=transport, base_url=GROQ_BASE_URL),
    )


@pytest.mark.asyncio
async def test_generate_sql_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        payload = _completion(
            json.dumps(
                {
                    "sql": "SELECT customer_id, SUM(total) FROM orders GROUP BY customer_id",
                    "is_ambiguous": False,
                    "ambiguity_reason": "",
                    "confidence": 0.91,
                }
            )
        )
        return httpx.Response(200, json=payload)

    result = await generate_sql("total spend per customer", SCHEMA, client=_client(handler))

    assert seen["url"] == f"{GROQ_BASE_URL}{GROQ_CHAT_PATH}"
    assert seen["auth"] == "Bearer gsk_test_key_abcd"
    assert seen["body"]["model"] == GROQ_DEFAULT_MODEL
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["temperature"] == 0.0

    assert result.sql.startswith("SELECT customer_id")
    assert result.is_ambiguous is False
    assert result.confidence == pytest.approx(0.91)
    assert result.raw.usage["total_tokens"] == 160
    assert result.raw.model == GROQ_DEFAULT_MODEL
    assert result.raw.latency_ms >= 0


@pytest.mark.asyncio
async def test_generate_sql_retries_after_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, headers={"retry-after-ms": "5"}, json={"error": "rate limited"}
            )
        return httpx.Response(
            200,
            json=_completion(
                json.dumps(
                    {
                        "sql": "SELECT 1",
                        "is_ambiguous": False,
                        "ambiguity_reason": "",
                        "confidence": 0.5,
                    }
                )
            ),
        )

    result = await generate_sql("anything", SCHEMA, client=_client(handler))

    assert calls["n"] == 2, "expected exactly one retry after the 429"
    assert result.sql == "SELECT 1"


@pytest.mark.asyncio
async def test_generate_sql_gives_up_after_retry_budget():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    with pytest.raises(LLMRetryExhausted):
        await generate_sql("anything", SCHEMA, client=_client(handler, max_retries=2))

    assert calls["n"] == 3, "expected initial attempt plus two retries"


@pytest.mark.asyncio
async def test_ambiguous_question_is_reported_not_guessed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completion(
                json.dumps(
                    {
                        "sql": "",
                        "is_ambiguous": True,
                        "ambiguity_reason": "'best' is not defined by the schema",
                        "confidence": 0.2,
                    }
                )
            ),
        )

    result = await generate_sql("who is the best customer", SCHEMA, client=_client(handler))

    assert result.is_ambiguous is True
    assert result.sql == ""
    assert "best" in result.ambiguity_reason


@pytest.mark.asyncio
async def test_auth_failure_is_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "invalid api key"})

    from backend.llm.base import LLMHTTPError

    with pytest.raises(LLMHTTPError) as excinfo:
        await generate_sql("anything", SCHEMA, client=_client(handler))

    assert calls["n"] == 1, "401 must fail immediately, not burn the retry budget"
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_api_key_never_appears_in_retry_exhaustion_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    with pytest.raises(LLMRetryExhausted) as excinfo:
        await generate_sql("anything", SCHEMA, client=_client(handler, max_retries=1))

    assert "gsk_test_key_abcd" not in str(excinfo.value)
    assert "gsk_...abcd" in str(excinfo.value)


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1" or not os.getenv("GROQ_API_KEY"),
    reason="live test; set RUN_LIVE_LLM_TESTS=1 and GROQ_API_KEY",
)
@pytest.mark.asyncio
async def test_live_groq_generate_sql():
    result = await generate_sql("how many orders are there in total?", SCHEMA)

    assert result.sql, "live Groq call returned no SQL"
    assert "select" in result.sql.lower()
    assert result.raw.usage is not None
    print(f"\n[live groq] model={result.raw.model} latency={result.raw.latency_ms}ms")
    print(f"[live groq] sql={result.sql}")
