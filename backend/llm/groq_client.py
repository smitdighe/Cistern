"""Groq — generation tier only.

This module owns exactly one job: turning a question plus a schema into
candidate SQL. It must never be imported by correction, explanation or judging
code. Those paths belong to ``cerebras_client`` and keeping the import graph
one-directional is what keeps the two tiers independent.

Transport: httpx via ``backend.llm.base.LLMClient``, not the ``groq`` SDK. The
SDK is installed and works, but it carries its own httpx client, retry loop and
timeout config, which would mean two divergent retry policies in one codebase.
The endpoint shape below was read off the installed SDK (groq 1.5.0) rather
than assumed — see report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import settings
from backend.llm.base import LLMClient, LLMResponse, extract_json
from backend.llm.prompts import generation_prompt

GROQ_BASE_URL = "https://api.groq.com"
GROQ_CHAT_PATH = "/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

_MAX_TOKENS = 1500


@dataclass(slots=True)
class GenerationResult:
    """Candidate SQL and the generator's own read on the question."""

    sql: str
    is_ambiguous: bool
    confidence: float | None
    raw: LLMResponse
    ambiguity_reason: str | None = None


def _build_client(client: LLMClient | None = None) -> LLMClient:
    if client is not None:
        return client
    return LLMClient(
        provider="groq",
        base_url=GROQ_BASE_URL,
        chat_path=GROQ_CHAT_PATH,
        api_key=settings.GROQ_API_KEY,
        default_model=GROQ_DEFAULT_MODEL,
    )


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, confidence))


async def generate_sql(
    question: str,
    schema: dict[str, Any],
    *,
    model: str | None = None,
    client: LLMClient | None = None,
) -> GenerationResult:
    """Generate a candidate SELECT for ``question`` against ``schema``."""
    llm = _build_client(client)
    owns_client = client is None

    try:
        response = await llm.chat(
            [{"role": "user", "content": generation_prompt(question, schema)}],
            model=model,
            temperature=0.0,
            max_tokens=_MAX_TOKENS,
            json_mode=True,
        )
    finally:
        if owns_client:
            await llm.aclose()

    parsed = extract_json(response.text)
    reason = parsed.get("ambiguity_reason") or None

    return GenerationResult(
        sql=(parsed.get("sql") or "").strip(),
        is_ambiguous=bool(parsed.get("is_ambiguous", False)),
        confidence=_coerce_confidence(parsed.get("confidence")),
        raw=response,
        ambiguity_reason=reason,
    )
