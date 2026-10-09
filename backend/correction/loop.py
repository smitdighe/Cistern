"""Self-correction loop for SQL that failed validation-passing execution.

Contract highlights, all load-bearing:

* Every corrected query goes back through ``validate_and_prepare``. A
  correction earns no exemption from safety checks — a corrector that emits
  DROP hits the same wall the generator would.
* A safety rejection of a corrected query is a *failed attempt*, not a loop
  abort — unless the rejection is a forbidden statement (DROP/TRUNCATE), which
  aborts immediately. Anything the corrector does after producing a forbidden
  statement is not worth an LLM call.
* Stall detection: when the corrector returns SQL identical (whitespace and
  casing normalised) to the immediately preceding failed attempt, the loop
  aborts rather than burning the remaining budget on a fixed point.
* The trail records every attempt — including stalled, rejected and errored
  ones — because phase 7 persists it and a gap there is an unexplainable log.

Shared helpers (``trail_entry``, ``classify_rows``, ``normalise_sql``) live
here so the pipeline and the loop describe attempts identically. This module
never imports the pipeline at module level; the default executor is resolved
lazily to keep the import graph acyclic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from backend.config import settings
from backend.db.executor import QueryExecutionResult, execute_query
from backend.dependencies import shared_correction_client
from backend.llm import cerebras_client
from backend.llm.base import LLMError
from backend.safety.validator import validate_and_prepare

ExecuteFn = Callable[[str], Awaitable[QueryExecutionResult]]

# Row-shape classifications shared with the pipeline.
ROWS_OK = "ok"
ROWS_EMPTY = "empty"
ROWS_SUSPICIOUS = "suspicious"


def normalise_sql(sql: str) -> str:
    """Collapse whitespace, drop trailing semicolons, fold case.

    Used only for stall comparison — never for validation, which works on the
    parsed AST.
    """
    return " ".join(sql.split()).rstrip("; ").lower()


def classify_rows(rows: list[dict]) -> str:
    """Decide whether an executed result looks like a real answer.

    Heuristic, documented because it drives correction handoff:

    * ``[]`` — empty. Sometimes a legitimate answer, but often a sign of a
      wrong filter; the pipeline lets correction take a shot and falls back to
      accepting the empty result if correction cannot do better.
    * exactly one row whose every value is NULL — suspicious. This is the
      signature of an aggregate over zero matching rows (``SELECT SUM(total)
      FROM orders WHERE ...`` with no hits returns one all-NULL row, not zero
      rows), which usually means the filter was wrong.
    * anything else — ok.
    """
    if not rows:
        return ROWS_EMPTY
    if len(rows) == 1 and rows[0] and all(value is None for value in rows[0].values()):
        return ROWS_SUSPICIOUS
    return ROWS_OK


def rows_summary(count: int) -> str:
    """Human-readable row count for trail entries."""
    return "1 row" if count == 1 else f"{count} rows"


def result_state_error(state: str) -> str:
    """The pseudo-error handed to the corrector for a non-error bad result."""
    if state == ROWS_SUSPICIOUS:
        return (
            "query executed without error but returned a single row where every value "
            "is NULL — likely an aggregate over zero matching rows; the filter is "
            "probably wrong"
        )
    return "query executed without error but returned no rows"


def trail_entry(
    *,
    attempt: int,
    source: str,
    sql: str | None,
    safety_flags: list[str] | None = None,
    validation: str | None = None,
    rejection_reason: str | None = None,
    executed: bool = False,
    error: str | None = None,
    row_count: int | None = None,
    result_summary: str | None = None,
    reasoning: str | None = None,
    stalled: bool = False,
) -> dict[str, Any]:
    """One attempt, in the shape phase 7 will persist. Every key always present."""
    return {
        "attempt": attempt,
        "source": source,
        "sql": sql,
        "safety_flags": list(safety_flags or []),
        "validation": validation,
        "rejection_reason": rejection_reason,
        "executed": executed,
        "error": error,
        "row_count": row_count,
        "result_summary": result_summary,
        "reasoning": reasoning,
        "stalled": stalled,
    }


@dataclass(slots=True)
class CorrectionLoopResult:
    """Outcome of the correction loop, successful or not.

    ``fallback_sql``/``fallback_rows`` carry the first attempt that executed
    cleanly but returned an empty or suspicious result — the pipeline may
    accept it when nothing better emerges.
    """

    final_sql: str | None
    success: bool
    attempts: int
    trail: list[dict] = field(default_factory=list)
    rows: list[dict] | None = None
    final_flags: list[str] = field(default_factory=list)
    stalled: bool = False
    aborted_reason: str | None = None
    fallback_sql: str | None = None
    fallback_rows: list[dict] | None = None
    fallback_flags: list[str] = field(default_factory=list)
    last_error: str | None = None


async def run_correction(
    question: str,
    schema: dict[str, Any],
    failed_sql: str,
    error: str,
    *,
    execute: ExecuteFn | None = None,
    max_attempts: int | None = None,
) -> CorrectionLoopResult:
    """Drive up to MAX_CORRECTION_ATTEMPTS repair rounds against real execution.

    ``execute`` defaults to the real sandboxed executor so this path and the
    pipeline's first attempt run SQL identically; tests inject a fake.
    """
    if execute is None:
        execute = execute_query

    budget = int(max_attempts if max_attempts is not None else settings.MAX_CORRECTION_ATTEMPTS)

    trail: list[dict] = []
    attempts = 0

    # The immediately preceding failed attempt, for the corrector's context
    # and for stall comparison. ``prev_norms`` holds both the corrector's raw
    # output and the validated form that actually executed (they differ by an
    # injected LIMIT), so a repeat of either counts as a stall.
    prev_sql = failed_sql
    prev_error = error
    prev_norms = {normalise_sql(failed_sql)}
    last_error: str | None = error

    fallback_sql: str | None = None
    fallback_rows: list[dict] | None = None
    fallback_flags: list[str] = []

    for attempt in range(1, budget + 1):
        attempts = attempt

        try:
            correction = await cerebras_client.correct_sql(
                question, schema, prev_sql, prev_error, client=shared_correction_client()
            )
        except LLMError as exc:
            last_error = f"correction call failed: {exc}"
            trail.append(
                trail_entry(attempt=attempt, source="correction", sql=None, error=last_error)
            )
            continue

        candidate = correction.sql
        if not candidate:
            last_error = "corrector returned empty SQL"
            trail.append(
                trail_entry(
                    attempt=attempt,
                    source="correction",
                    sql=None,
                    error=last_error,
                    reasoning=correction.reasoning,
                )
            )
            prev_error = last_error
            continue

        # Stall detection, before validation or execution spends anything.
        if normalise_sql(candidate) in prev_norms:
            last_error = "correction stalled: corrector repeated the previous failed query verbatim"
            trail.append(
                trail_entry(
                    attempt=attempt,
                    source="correction",
                    sql=candidate,
                    error=last_error,
                    reasoning=correction.reasoning,
                    stalled=True,
                )
            )
            return CorrectionLoopResult(
                final_sql=None,
                success=False,
                attempts=attempt,
                trail=trail,
                stalled=True,
                fallback_sql=fallback_sql,
                fallback_rows=fallback_rows,
                fallback_flags=fallback_flags,
                last_error=last_error,
            )

        # No exemption: corrected SQL passes the same validator or it does not run.
        validation = validate_and_prepare(candidate, schema)

        if not validation.is_safe:
            entry = trail_entry(
                attempt=attempt,
                source="correction",
                sql=candidate,
                safety_flags=validation.flags,
                validation="rejected",
                rejection_reason=validation.rejection_reason,
                reasoning=correction.reasoning,
            )
            trail.append(entry)

            if "forbidden_statement" in validation.flags:
                # DROP/TRUNCATE from a corrector is an immediate abort.
                return CorrectionLoopResult(
                    final_sql=None,
                    success=False,
                    attempts=attempt,
                    trail=trail,
                    aborted_reason=validation.rejection_reason,
                    fallback_sql=fallback_sql,
                    fallback_rows=fallback_rows,
                    fallback_flags=fallback_flags,
                    last_error=validation.rejection_reason,
                )

            prev_sql = candidate
            prev_error = f"rejected by safety validator: {validation.rejection_reason}"
            prev_norms = {normalise_sql(candidate)}
            last_error = prev_error
            continue

        if validation.requires_confirmation:
            # Safe but not auto-executable. The loop has no human to ask, so
            # this is a failed attempt steering the corrector back to SELECT.
            entry = trail_entry(
                attempt=attempt,
                source="correction",
                sql=candidate,
                safety_flags=validation.flags,
                validation="requires_confirmation",
                reasoning=correction.reasoning,
            )
            trail.append(entry)
            prev_sql = candidate
            prev_error = (
                "the statement type requires human confirmation and cannot run "
                "automatically; produce a single SELECT statement"
            )
            prev_norms = {normalise_sql(candidate), normalise_sql(validation.final_sql or "")}
            last_error = prev_error
            continue

        final_sql = validation.final_sql
        assert final_sql is not None  # is_safe guarantees this

        execution = await execute(final_sql)

        if not execution.ok:
            # Execution errors are the loop's raw material, not exceptions.
            last_error = execution.error
            trail.append(
                trail_entry(
                    attempt=attempt,
                    source="correction",
                    sql=final_sql,
                    safety_flags=validation.flags,
                    validation="passed",
                    executed=True,
                    error=last_error,
                    reasoning=correction.reasoning,
                )
            )
            prev_sql = final_sql
            prev_error = last_error
            prev_norms = {normalise_sql(candidate), normalise_sql(final_sql)}
            continue

        rows = execution.rows
        state = classify_rows(rows)

        if state == ROWS_OK:
            trail.append(
                trail_entry(
                    attempt=attempt,
                    source="correction",
                    sql=final_sql,
                    safety_flags=validation.flags,
                    validation="passed",
                    executed=True,
                    row_count=len(rows),
                    result_summary=rows_summary(len(rows)),
                    reasoning=correction.reasoning,
                )
            )
            return CorrectionLoopResult(
                final_sql=final_sql,
                success=True,
                attempts=attempt,
                trail=trail,
                rows=rows,
                final_flags=list(validation.flags),
                fallback_sql=fallback_sql,
                fallback_rows=fallback_rows,
                fallback_flags=fallback_flags,
                last_error=None,
            )

        # Executed cleanly but empty/suspicious: failed attempt, kept as fallback.
        summary = "0 rows" if state == ROWS_EMPTY else "1 row, all values NULL"
        last_error = result_state_error(state)
        trail.append(
            trail_entry(
                attempt=attempt,
                source="correction",
                sql=final_sql,
                safety_flags=validation.flags,
                validation="passed",
                executed=True,
                row_count=len(rows),
                result_summary=summary,
                error=last_error,
                reasoning=correction.reasoning,
            )
        )
        if fallback_sql is None:
            fallback_sql = final_sql
            fallback_rows = rows
            fallback_flags = list(validation.flags)
        prev_sql = final_sql
        prev_error = last_error
        prev_norms = {normalise_sql(candidate), normalise_sql(final_sql)}

    return CorrectionLoopResult(
        final_sql=None,
        success=False,
        attempts=attempts,
        trail=trail,
        fallback_sql=fallback_sql,
        fallback_rows=fallback_rows,
        fallback_flags=fallback_flags,
        last_error=last_error,
    )
