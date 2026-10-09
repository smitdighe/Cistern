"""Pre-generation ambiguity detection.

DESIGN DECISION — option (b): a distinct pre-generation Groq call, not folded
into ``generate_sql``.

Option (a) — returning the ambiguity verdict alongside the SQL from one
``generate_sql`` round-trip — is cheaper by one call but cannot satisfy this
phase's contract. The pipeline must make zero calls to ``generate_sql`` for an
ambiguous question. Under (a) the ambiguity verdict *is* a ``generate_sql``
call, so either the short-circuit becomes a fiction (generation ran, we just
threw the SQL away) or ``detect_ambiguity`` has to return SQL it has no slot
for in ``AmbiguityResult``. Both make a public surface lie about what it does.
(a) also costs *more* on the common path: the detector call would generate SQL,
discard it, and then the pipeline would call ``generate_sql`` again.

Tier separation is preserved: this is generation-tier work and uses Groq.
Cerebras stays correction/explanation/judge-only — it never sees a question
that Groq has not already produced something for.

``generate_sql``'s existing ``is_ambiguous`` field is *not* redundant with this
module and is deliberately left in place. The two checks look at different
things: this one reads the question against the schema before any mapping is
attempted; the generator's flag is raised while actually attempting the mapping
and catches cases only visible at that point. The pipeline honours both.

Bias: this detector must lean toward *not* flagging. A false positive stops a
perfectly answerable question and demands clarification the user cannot
usefully give, which is a worse failure than generating SQL the validator and
correction loop can still catch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.config import settings
from backend.llm.base import LLMClient, LLMError, extract_json
from backend.llm.groq_client import GROQ_BASE_URL, GROQ_CHAT_PATH, GROQ_DEFAULT_MODEL
from backend.llm.prompts import render_schema

logger = logging.getLogger(__name__)

_MAX_TOKENS = 500

_GENERIC_CLARIFICATION = (
    "That question could be read more than one way against this data. "
    "Could you say more precisely what you want?"
)


@dataclass(slots=True)
class AmbiguityResult:
    """Verdict on whether a question can be answered without guessing."""

    is_ambiguous: bool
    clarifying_question: str | None = None
    reasoning: str | None = None


def ambiguity_prompt(question: str, schema: dict[str, Any]) -> str:
    """Prompt asking whether a question is answerable from this schema.

    Lives here rather than in ``backend/llm/prompts.py`` only because that file
    is outside this phase's scope; it belongs there. See report.
    """
    return f"""\
You decide whether a natural language question can be translated into a single
unambiguous SQL query against the schema below. You do not write the query.

Database schema:
{render_schema(schema)}

Question:
{question}

A question is ambiguous ONLY when answering it requires inventing a definition
the schema does not supply. Concretely:
- it names a metric or ranking the schema does not define ("top", "best",
  "most important") without saying by which column
- it names an entity that maps to two or more different columns with no way to
  choose between them
- it omits a bound that changes the answer entirely and has no sensible default

A question is NOT ambiguous when:
- it is answerable by an obvious reading, even if other readings exist
- a reasonable default resolves it (recency means the schema's timestamp
  column; "how many" means COUNT)
- it is broad or returns many rows — breadth is not ambiguity
- it uses casual phrasing that still maps cleanly onto schema columns

Default to NOT ambiguous. Flag a question only when you genuinely cannot pick
between readings without guessing. Stopping an answerable question to ask an
unnecessary clarifying question is a worse mistake than attempting it.

If it is ambiguous, write one short clarifying question, phrased for the person
who asked. Name the concrete choices they should pick between where you can.
Do not mention SQL, columns, tables or schemas in the clarifying question.

Respond with a single JSON object and nothing else:
{{
  "is_ambiguous": <true or false>,
  "clarifying_question": "<the question to ask back, or an empty string>",
  "reasoning": "<one sentence on why, for logs — not shown to the user>"
}}"""


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


async def detect_ambiguity(
    question: str,
    schema: dict[str, Any],
    *,
    model: str | None = None,
    client: LLMClient | None = None,
) -> AmbiguityResult:
    """Decide whether ``question`` needs clarification before SQL generation.

    Fails open. If the detector call errors or returns something unparseable,
    the question is treated as unambiguous and generation proceeds — a
    transient Groq failure must not present itself to the user as "your
    question was unclear". The generator's own ambiguity flag, the validator
    and the correction loop all still stand behind this.
    """
    llm = _build_client(client)
    owns_client = client is None

    try:
        response = await llm.chat(
            [{"role": "user", "content": ambiguity_prompt(question, schema)}],
            model=model,
            temperature=0.0,
            max_tokens=_MAX_TOKENS,
            json_mode=True,
        )
    except LLMError as exc:
        logger.warning("ambiguity detection failed, proceeding to generation: %s", exc)
        return AmbiguityResult(is_ambiguous=False, reasoning=f"detector unavailable: {exc}")
    finally:
        if owns_client:
            await llm.aclose()

    try:
        parsed = extract_json(response.text)
    except LLMError as exc:
        logger.warning("ambiguity response unparseable, proceeding to generation: %s", exc)
        return AmbiguityResult(
            is_ambiguous=False, reasoning=f"detector returned unusable output: {exc}"
        )

    is_ambiguous = bool(parsed.get("is_ambiguous", False))
    reasoning = (parsed.get("reasoning") or "").strip() or None

    if not is_ambiguous:
        return AmbiguityResult(is_ambiguous=False, clarifying_question=None, reasoning=reasoning)

    # A flag with no question attached is useless to the caller — the user has
    # to be told *something* actionable. Fall back rather than return None.
    clarifying = (parsed.get("clarifying_question") or "").strip() or _GENERIC_CLARIFICATION

    return AmbiguityResult(
        is_ambiguous=True,
        clarifying_question=clarifying,
        reasoning=reasoning,
    )
