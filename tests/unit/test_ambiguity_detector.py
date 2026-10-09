"""Ambiguity detector, against mocked Groq HTTP responses."""

import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql://ro:pw@placeholder.invalid/db")
os.environ.setdefault("DATABASE_ADMIN_URL", "postgresql://admin:pw@placeholder.invalid/db")

import httpx  # noqa: E402
import pytest  # noqa: E402

from backend.ambiguity.detector import (  # noqa: E402
    _GENERIC_CLARIFICATION,
    ambiguity_prompt,
    detect_ambiguity,
)
from backend.llm.base import LLMClient, RetryPolicy  # noqa: E402
from backend.llm.groq_client import (  # noqa: E402
    GROQ_BASE_URL,
    GROQ_CHAT_PATH,
    GROQ_DEFAULT_MODEL,
)

SCHEMA = {
    "orders": {
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "customer_id", "type": "integer", "nullable": False},
            {"name": "total", "type": "numeric", "nullable": True},
            {"name": "placed_at", "type": "timestamp", "nullable": False},
        ],
        "primary_key": ["id"],
        "foreign_keys": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
    },
    "customers": {
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "name", "type": "text", "nullable": False},
            {"name": "country", "type": "text", "nullable": True},
        ],
        "primary_key": ["id"],
        "foreign_keys": [],
    },
}

AMBIGUOUS_QUESTION = "show me the top customers"
CLEAR_QUESTION = "list all orders placed in the last 7 days"


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": GROQ_DEFAULT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 90, "completion_tokens": 30, "total_tokens": 120},
    }


def _client(handler) -> LLMClient:
    return LLMClient(
        provider="groq",
        base_url=GROQ_BASE_URL,
        chat_path=GROQ_CHAT_PATH,
        api_key="gsk_test_key_abcd",
        default_model=GROQ_DEFAULT_MODEL,
        retry_policy=RetryPolicy(base_seconds=0.0, max_seconds=0.0),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=GROQ_BASE_URL),
    )


def _responder(payload: dict):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_completion(json.dumps(payload)))

    handler.seen = seen
    return handler


# --- ambiguous case ---------------------------------------------------------


async def test_ambiguous_question_returns_clarification():
    handler = _responder(
        {
            "is_ambiguous": True,
            "clarifying_question": "Do you mean top by number of orders, or by total amount spent?",
            "reasoning": "'top' is not defined by the schema",
        }
    )

    result = await detect_ambiguity(AMBIGUOUS_QUESTION, SCHEMA, client=_client(handler))

    assert result.is_ambiguous is True
    assert result.clarifying_question == (
        "Do you mean top by number of orders, or by total amount spent?"
    )
    assert result.reasoning == "'top' is not defined by the schema"
    # The detector must ask Groq, on the generation-tier endpoint, in JSON mode.
    assert handler.seen["url"] == f"{GROQ_BASE_URL}{GROQ_CHAT_PATH}"
    assert handler.seen["body"]["response_format"] == {"type": "json_object"}
    assert handler.seen["body"]["model"] == GROQ_DEFAULT_MODEL


async def test_ambiguous_without_a_question_falls_back_to_generic():
    """A bare flag is useless to the caller; something actionable must come back."""
    handler = _responder(
        {"is_ambiguous": True, "clarifying_question": "", "reasoning": "underspecified"}
    )

    result = await detect_ambiguity(AMBIGUOUS_QUESTION, SCHEMA, client=_client(handler))

    assert result.is_ambiguous is True
    assert result.clarifying_question == _GENERIC_CLARIFICATION


# --- unambiguous case -------------------------------------------------------


async def test_clear_question_passes_through():
    handler = _responder(
        {
            "is_ambiguous": False,
            "clarifying_question": "",
            "reasoning": "placed_at supplies the time bound; no metric to choose",
        }
    )

    result = await detect_ambiguity(CLEAR_QUESTION, SCHEMA, client=_client(handler))

    assert result.is_ambiguous is False
    assert result.clarifying_question is None
    assert result.reasoning == "placed_at supplies the time bound; no metric to choose"


# --- fail-open behaviour ----------------------------------------------------


async def test_transport_failure_fails_open():
    """A broken detector must not tell the user their question was unclear."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "upstream is down"})

    result = await detect_ambiguity(CLEAR_QUESTION, SCHEMA, client=_client(handler))

    assert result.is_ambiguous is False
    assert result.clarifying_question is None
    assert "detector unavailable" in result.reasoning


async def test_unparseable_response_fails_open():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("I think maybe it is fine?"))

    result = await detect_ambiguity(AMBIGUOUS_QUESTION, SCHEMA, client=_client(handler))

    assert result.is_ambiguous is False
    assert "unusable output" in result.reasoning


async def test_missing_flag_defaults_to_unambiguous():
    handler = _responder({"reasoning": "no verdict field at all"})

    result = await detect_ambiguity(AMBIGUOUS_QUESTION, SCHEMA, client=_client(handler))

    assert result.is_ambiguous is False


# --- prompt construction ----------------------------------------------------


@pytest.mark.parametrize("question", [AMBIGUOUS_QUESTION, CLEAR_QUESTION])
def test_prompt_carries_question_and_schema(question):
    prompt = ambiguity_prompt(question, SCHEMA)

    assert question in prompt
    assert "TABLE orders" in prompt
    assert "placed_at" in prompt
    assert "Default to NOT ambiguous" in prompt, "the anti-false-positive bias must survive"


def test_prompt_does_not_ask_for_sql():
    """Detection is not generation — the detector must not return a query."""
    prompt = ambiguity_prompt(AMBIGUOUS_QUESTION, SCHEMA)

    assert "You do not write the query" in prompt
    assert '"sql"' not in prompt
