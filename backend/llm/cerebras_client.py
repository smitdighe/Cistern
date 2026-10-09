"""Cerebras — correction, explanation and judge tiers.

This module never imports ``groq_client`` and is never told that the SQL it
receives came from another model, let alone which one. It sees a question, a
schema, a SQL string, and either an error or a result summary. That omission is
the whole point: a judge that knows it is grading its own family of model
drifts toward agreement, and a corrector that knows the query came from a
"weaker" tier rewrites rather than diagnoses.

Transport: httpx via ``backend.llm.base.LLMClient``, not the
``cerebras-cloud-sdk`` package, for the same single-retry-policy reason as the
Groq module. Endpoint shape read off the installed SDK (cerebras-cloud-sdk
1.91.0) — see report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import settings
from backend.llm.base import LLMClient, LLMResponse, extract_json
from backend.llm.prompts import correction_prompt, explanation_prompt, judge_prompt

CEREBRAS_BASE_URL = "https://api.cerebras.ai"
CEREBRAS_CHAT_PATH = "/v1/chat/completions"
CEREBRAS_DEFAULT_MODEL = "gpt-oss-120b"

_CORRECTION_MAX_TOKENS = 1500
_EXPLANATION_MAX_TOKENS = 500
_JUDGE_MAX_TOKENS = 600


@dataclass(slots=True)
class CorrectionResult:
    """A repaired query and why the previous one failed."""

    sql: str
    reasoning: str | None
    raw: LLMResponse


@dataclass(slots=True)
class JudgeResult:
    """A ruling on whether a candidate query matches a reference query."""

    verdict: bool
    reasoning: str
    raw: LLMResponse


def _build_client(client: LLMClient | None = None) -> LLMClient:
    if client is not None:
        return client
    return LLMClient(
        provider="cerebras",
        base_url=CEREBRAS_BASE_URL,
        chat_path=CEREBRAS_CHAT_PATH,
        api_key=settings.CEREBRAS_API_KEY,
        default_model=CEREBRAS_DEFAULT_MODEL,
    )


async def _complete(
    prompt: str,
    *,
    max_tokens: int,
    json_mode: bool,
    model: str | None,
    client: LLMClient | None,
) -> LLMResponse:
    llm = _build_client(client)
    owns_client = client is None
    try:
        return await llm.chat(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
    finally:
        if owns_client:
            await llm.aclose()


async def correct_sql(
    question: str,
    schema: dict[str, Any],
    failed_sql: str,
    error: str,
    *,
    model: str | None = None,
    client: LLMClient | None = None,
) -> CorrectionResult:
    """Repair a query that failed to execute. Author of ``failed_sql`` is not disclosed."""
    response = await _complete(
        correction_prompt(question, schema, failed_sql, error),
        max_tokens=_CORRECTION_MAX_TOKENS,
        json_mode=True,
        model=model,
        client=client,
    )
    parsed = extract_json(response.text)
    return CorrectionResult(
        sql=(parsed.get("sql") or "").strip(),
        reasoning=parsed.get("reasoning") or None,
        raw=response,
    )


async def explain_sql(
    question: str,
    sql: str,
    *,
    model: str | None = None,
    client: LLMClient | None = None,
) -> str:
    """Return a plain-language explanation of ``sql``."""
    response = await _complete(
        explanation_prompt(question, sql),
        max_tokens=_EXPLANATION_MAX_TOKENS,
        json_mode=False,
        model=model,
        client=client,
    )
    return response.text.strip()


async def judge(
    question: str,
    gold_sql: str,
    generated_sql: str,
    gold_result_summary: str,
    generated_result_summary: str,
    *,
    model: str | None = None,
    client: LLMClient | None = None,
) -> JudgeResult:
    """Rule on whether ``generated_sql`` is semantically equivalent to ``gold_sql``.

    Neither query is attributed to a model in the prompt.
    """
    response = await _complete(
        judge_prompt(
            question,
            gold_sql,
            generated_sql,
            gold_result_summary,
            generated_result_summary,
        ),
        max_tokens=_JUDGE_MAX_TOKENS,
        json_mode=True,
        model=model,
        client=client,
    )
    parsed = extract_json(response.text)
    return JudgeResult(
        verdict=bool(parsed.get("verdict", False)),
        reasoning=parsed.get("reasoning") or "",
        raw=response,
    )
