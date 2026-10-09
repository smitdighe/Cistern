"""Persistence of one row per pipeline run into ``query_log``.

WRITE PATH — deliberate exception to the read-only rule
-------------------------------------------------------
Everywhere else in this codebase, query traffic goes through the execution
engine bound to the read-only role, and that role's SELECT-only grant is the
last line of defence against a hallucinated write. Audit logging is the one
thing that must write, so it cannot use that engine — the grant would refuse
it, correctly.

This module therefore uses ``AdminSessionLocal``. Three constraints keep that
from eroding the guarantee:

* it is used *only* here, and only ever to INSERT into ``query_log``
* it never touches user data, and never runs generated SQL
* the SQL is a parameterised ORM insert of values the pipeline produced, not
  a string built from anything a model emitted

The alternative — a third Postgres role granted INSERT on ``query_log`` alone
— is strictly better and is what this should become in production. It is not
done here because that needs a role and grant that do not exist yet on the
Neon instance. See report.

FAILURE POLICY
--------------
Logging never raises. A failed audit write must not turn a successfully
answered question into an error for the caller; the write failure is itself
logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.db.connection import AdminSessionLocal
from backend.db.models import QueryLog

if TYPE_CHECKING:
    from backend.orchestration.pipeline import PipelineResult

logger = logging.getLogger(__name__)

# query_log.generated_sql and final_sql are NOT NULL, but the ambiguity
# short-circuit and generation failures produce no SQL at all. Empty string
# stands in for "no SQL was produced", which is distinguishable from a real
# statement and does not require a migration to store.
_NO_SQL = ""


def _generated_sql(result: PipelineResult) -> str:
    """The first SQL the generator produced, before any correction.

    Read back out of the attempt trail rather than carried as its own field —
    the trail is the record of what happened and already holds it.
    """
    for entry in result.attempt_trail:
        if entry.get("source") == "generation" and entry.get("sql"):
            return str(entry["sql"])
    return result.sql or _NO_SQL


def _safety_flags_payload(result: PipelineResult) -> dict[str, Any]:
    """What goes in the ``safety_flags`` JSONB column.

    Stores an object, not a bare list. ``query_log`` has no column for the
    attempt trail, and phase 4 committed to persisting it in full — this is
    the only JSON column available to hold it. A dedicated ``attempt_trail``
    column would be cleaner and needs a migration. See report.
    """
    return {
        "flags": list(result.safety_flags),
        "status": result.status,
        "attempt_trail": result.attempt_trail,
    }


async def log_query_attempt(pipeline_result: PipelineResult) -> int | None:
    """Write one ``query_log`` row. Returns the row id, or None on failure.

    Called by ``run_pipeline`` on every exit path, including the ones that
    never generated SQL. Callers that bypass HTTP — the phase 8 eval runner —
    get logged by virtue of going through the same function.
    """
    row = QueryLog(
        question=pipeline_result.question,
        generated_sql=_generated_sql(pipeline_result),
        final_sql=pipeline_result.sql or _NO_SQL,
        attempt_count=pipeline_result.attempts,
        safety_flags=_safety_flags_payload(pipeline_result),
        error=pipeline_result.error,
        latency_ms=pipeline_result.latency_ms,
        success=pipeline_result.success,
    )

    try:
        async with AdminSessionLocal() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id
    except Exception as exc:
        # Never propagate: an audit failure must not sink an answered query.
        logger.warning(
            "failed to persist query_log row",
            extra={
                "audit_error": str(exc),
                "pipeline_status": pipeline_result.status,
                "attempts": pipeline_result.attempts,
            },
        )
        return None
