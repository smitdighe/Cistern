"""End-to-end query pipeline: question in, executed result + explanation out.

Strict step order — schema, ambiguity, generation, safety, execution,
correction, explanation. Two rules shape every branch:

* A safety rejection is terminal. Correction exists to repair queries that
  *ran* and failed, not to negotiate with the validator; retrying a rejected
  statement teaches the corrector to gamble against the safety layer.
* Nothing is dropped from ``attempt_trail``. Phase 7 persists it; any branch
  that returns without recording what happened produces an unexplainable log.

Failure-path semantics for ``PipelineResult.sql``: on success it is the SQL
that produced ``result``; on ordinary failure it is the last SQL attempted
(debuggable); when correction aborted on a forbidden statement it is ``None``
— a forbidden statement is never surfaced as "the query".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from backend.ambiguity.detector import detect_ambiguity
from backend.app_logging.query_log import log_query_attempt
from backend.correction.loop import (
    ROWS_OK,
    classify_rows,
    result_state_error,
    rows_summary,
    run_correction,
    trail_entry,
)
from backend.db import introspect
from backend.db.executor import execute_query
from backend.dependencies import shared_cerebras_client, shared_groq_client
from backend.llm import cerebras_client, groq_client
from backend.llm.base import LLMError
from backend.safety.validator import validate_and_prepare

logger = logging.getLogger(__name__)

# Terminal states, for routes/ to branch on without inferring intent from a
# combination of `success` and `ambiguity_question is not None`.
STATUS_SUCCESS = "success"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_FAILED = "failed"

# The pipeline's first attempt and every correction attempt go through this
# one name, so the two paths cannot drift apart in how they run SQL. Bound at
# module level rather than imported into each call site so tests can swap it.
execute_sql = execute_query


@dataclass(slots=True)
class PipelineResult:
    """Everything one question produced, including the full attempt history.

    ``attempts`` counts SQL-producing attempts: 1 for generation plus one per
    correction round.

    ``status`` is the discriminator routes/ should switch on. "ambiguous" is
    not a failure — the system worked correctly and is asking a question back —
    and phase 6 must not map it onto the same response shape as "failed".
    ``success`` stays as the boolean shorthand for ``status == "success"``.
    """

    sql: str | None
    result: list | None
    explanation: str | None
    attempts: int
    safety_flags: list[str] = field(default_factory=list)
    ambiguity_question: str | None = None
    success: bool = False
    error: str | None = None
    attempt_trail: list[dict] = field(default_factory=list)
    status: str = STATUS_FAILED
    # Stamped by run_pipeline on the way out, for the audit row. The inner
    # pipeline does not set these; it does not know how long it took and does
    # not carry its own input.
    question: str = ""
    latency_ms: int = 0


async def _safe_explain(question: str, sql: str) -> tuple[str | None, list[str]]:
    """Explanation is best-effort: its failure must not sink a good result."""
    try:
        explanation = await cerebras_client.explain_sql(
            question, sql, client=shared_cerebras_client()
        )
        return explanation, []
    except LLMError as exc:
        logger.warning("explanation failed, continuing without one: %s", exc)
        return None, ["explanation_failed"]


async def run_pipeline(question: str) -> PipelineResult:
    """Run one question through the pipeline and record the attempt.

    Every exit path of ``_run_pipeline`` — answered, safety-rejected,
    ambiguous, correction-exhausted, or crashed — passes through here, so the
    audit row is written exactly once per invocation and cannot be skipped by
    a branch that returns early.

    The hook lives here rather than in ``routes/query.py`` on purpose: the
    phase 8 eval runner calls ``run_pipeline`` directly with no HTTP involved,
    and must produce the same audit trail as a real request.
    """
    started = time.perf_counter()

    try:
        result = await _run_pipeline(question)
    except Exception as exc:
        # An unhandled failure is exactly the case worth having in the log.
        logger.exception("pipeline raised")
        result = PipelineResult(
            sql=None,
            result=None,
            explanation=None,
            attempts=0,
            success=False,
            status=STATUS_FAILED,
            error=f"pipeline error: {exc}",
            attempt_trail=[],
        )

    result.question = question
    result.latency_ms = int((time.perf_counter() - started) * 1000)

    await log_query_attempt(result)

    return result


async def _run_pipeline(question: str) -> PipelineResult:
    """Run one natural-language question through the full pipeline."""
    trail: list[dict] = []

    # a. Schema. Without it nothing downstream can be validated.
    try:
        schema = await introspect.get_schema()
    except Exception as exc:
        return PipelineResult(
            sql=None,
            result=None,
            explanation=None,
            attempts=0,
            success=False,
            status=STATUS_FAILED,
            error=f"schema introspection failed: {exc}",
            attempt_trail=trail,
        )

    # b. Ambiguity gate. A flag here short-circuits the request outright:
    #    generation, validation and execution are all skipped. Generating SQL
    #    for a question we have already decided is a guess only produces a
    #    confident wrong answer.
    ambiguity = await detect_ambiguity(question, schema, client=shared_groq_client())
    if ambiguity.is_ambiguous:
        trail.append(
            trail_entry(
                attempt=0,
                source="ambiguity",
                sql=None,
                result_summary="short-circuited before generation",
                reasoning=ambiguity.reasoning,
            )
        )
        return PipelineResult(
            sql=None,
            result=None,
            explanation=None,
            attempts=0,
            ambiguity_question=ambiguity.clarifying_question,
            success=False,
            error=None,
            attempt_trail=trail,
            status=STATUS_AMBIGUOUS,
        )

    # c. Generation.
    try:
        generation = await groq_client.generate_sql(question, schema, client=shared_groq_client())
    except LLMError as exc:
        trail.append(
            trail_entry(attempt=1, source="generation", sql=None, error=f"generation failed: {exc}")
        )
        return PipelineResult(
            sql=None,
            result=None,
            explanation=None,
            attempts=1,
            success=False,
            status=STATUS_FAILED,
            error=f"generation failed: {exc}",
            attempt_trail=trail,
        )

    # The generator may itself report ambiguity instead of guessing. Honour it
    # — executing a query the generator flagged as a guess is silent corruption.
    if generation.is_ambiguous:
        clarification = (
            generation.ambiguity_reason
            or "The question is ambiguous for this schema — please clarify."
        )
        trail.append(
            trail_entry(
                attempt=1,
                source="generation",
                sql=generation.sql or None,
                result_summary="generation reported ambiguity",
            )
        )
        return PipelineResult(
            sql=None,
            result=None,
            explanation=None,
            attempts=1,
            ambiguity_question=clarification,
            success=False,
            status=STATUS_AMBIGUOUS,
            error=None,
            attempt_trail=trail,
        )

    # d. Safety. A rejection here is final — no correction on safety failures.
    validation = validate_and_prepare(generation.sql, schema)

    if not validation.is_safe:
        trail.append(
            trail_entry(
                attempt=1,
                source="generation",
                sql=generation.sql,
                safety_flags=validation.flags,
                validation="rejected",
                rejection_reason=validation.rejection_reason,
            )
        )
        return PipelineResult(
            sql=None,
            result=None,
            explanation=None,
            attempts=1,
            safety_flags=list(validation.flags),
            success=False,
            status=STATUS_FAILED,
            error=validation.rejection_reason,
            attempt_trail=trail,
        )

    if validation.requires_confirmation:
        # Safe but not auto-executable; the pipeline has no confirmation UI.
        trail.append(
            trail_entry(
                attempt=1,
                source="generation",
                sql=validation.final_sql,
                safety_flags=validation.flags,
                validation="requires_confirmation",
            )
        )
        return PipelineResult(
            sql=validation.final_sql,
            result=None,
            explanation=None,
            attempts=1,
            safety_flags=list(validation.flags),
            success=False,
            status=STATUS_FAILED,
            error="statement requires explicit confirmation and was not executed",
            attempt_trail=trail,
        )

    final_sql = validation.final_sql
    assert final_sql is not None  # is_safe guarantees this

    # e. Execution, sandboxed and server-side time-bounded.
    execution = await execute_sql(final_sql)
    exec_error: str | None = execution.error
    rows: list[dict] | None = execution.rows if execution.ok else None

    if exec_error is None:
        assert rows is not None
        state = classify_rows(rows)

        if state == ROWS_OK:
            trail.append(
                trail_entry(
                    attempt=1,
                    source="generation",
                    sql=final_sql,
                    safety_flags=validation.flags,
                    validation="passed",
                    executed=True,
                    row_count=len(rows),
                    result_summary=rows_summary(len(rows)),
                )
            )
            explanation, explain_flags = await _safe_explain(question, final_sql)
            return PipelineResult(
                sql=final_sql,
                result=rows,
                explanation=explanation,
                attempts=1,
                safety_flags=list(validation.flags) + explain_flags,
                success=True,
                status=STATUS_SUCCESS,
                error=None,
                attempt_trail=trail,
            )

        # Executed cleanly but empty/suspicious — hand to correction.
        handoff_error = result_state_error(state)
        trail.append(
            trail_entry(
                attempt=1,
                source="generation",
                sql=final_sql,
                safety_flags=validation.flags,
                validation="passed",
                executed=True,
                row_count=len(rows),
                result_summary="0 rows" if not rows else "1 row, all values NULL",
                error=handoff_error,
            )
        )
    else:
        handoff_error = exec_error
        trail.append(
            trail_entry(
                attempt=1,
                source="generation",
                sql=final_sql,
                safety_flags=validation.flags,
                validation="passed",
                executed=True,
                error=exec_error,
            )
        )

    # f. Correction.
    correction = await run_correction(
        question, schema, failed_sql=final_sql, error=handoff_error, execute=execute_sql
    )

    # Renumber the loop's attempts to continue after generation's attempt 1.
    for entry in correction.trail:
        entry["attempt"] += 1
    trail.extend(correction.trail)
    attempts = 1 + correction.attempts

    if correction.success:
        assert correction.final_sql is not None and correction.rows is not None
        explanation, explain_flags = await _safe_explain(question, correction.final_sql)
        return PipelineResult(
            sql=correction.final_sql,
            result=correction.rows,
            explanation=explanation,
            attempts=attempts,
            safety_flags=list(correction.final_flags) + explain_flags,
            success=True,
            status=STATUS_SUCCESS,
            error=None,
            attempt_trail=trail,
        )

    if correction.aborted_reason is not None:
        # Corrector produced a forbidden statement. Hard stop, no fallback —
        # this failure must be loud, not papered over with a partial result.
        return PipelineResult(
            sql=None,
            result=None,
            explanation=None,
            attempts=attempts,
            safety_flags=["correction_aborted_forbidden"],
            success=False,
            status=STATUS_FAILED,
            error=f"correction aborted: {correction.aborted_reason}",
            attempt_trail=trail,
        )

    # g/h. Correction could not do better. If some attempt executed cleanly
    # but returned an empty/suspicious result, accept it rather than calling a
    # valid empty answer a failure. Original first-try result takes precedence.
    accepted_sql: str | None = None
    accepted_rows: list[dict] | None = None
    accepted_flags: list[str] = []
    accepted_marker: str | None = None

    if exec_error is None and rows is not None:
        accepted_sql = final_sql
        accepted_rows = rows
        accepted_flags = list(validation.flags)
        accepted_marker = "empty_result_accepted" if not rows else "suspicious_result_accepted"
    elif correction.fallback_sql is not None:
        accepted_sql = correction.fallback_sql
        accepted_rows = correction.fallback_rows
        accepted_flags = list(correction.fallback_flags)
        accepted_marker = "empty_result_accepted"

    if accepted_sql is not None:
        flags = accepted_flags + [accepted_marker]
        if correction.stalled:
            flags.append("correction_stalled")
        explanation, explain_flags = await _safe_explain(question, accepted_sql)
        return PipelineResult(
            sql=accepted_sql,
            result=accepted_rows,
            explanation=explanation,
            attempts=attempts,
            safety_flags=flags + explain_flags,
            success=True,
            status=STATUS_SUCCESS,
            error=None,
            attempt_trail=trail,
        )

    flags = ["correction_stalled"] if correction.stalled else []
    last_attempted = next((entry["sql"] for entry in reversed(trail) if entry["sql"]), final_sql)
    return PipelineResult(
        sql=last_attempted,
        result=None,
        explanation=None,
        attempts=attempts,
        safety_flags=flags,
        success=False,
        status=STATUS_FAILED,
        error=correction.last_error or handoff_error,
        attempt_trail=trail,
    )
