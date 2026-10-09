"""Sandboxed execution of validated SQL on the read-only role.

Three layers stand between a generated query and the database, and they fail
independently on purpose:

1. The Postgres role itself is read-only — writes are refused by the server.
2. ``SET LOCAL statement_timeout`` bounds runtime *server-side*. This is the
   real timeout: it holds even if this process wedges, is GC-paused, or the
   event loop stalls, and Postgres cancels the backend rather than leaving a
   query burning CPU on a shared Neon instance. ``SET LOCAL`` is scoped to the
   surrounding transaction and reverts on commit/rollback, so it cannot leak
   into a later query on a pooled connection.
3. An ``asyncio.wait_for`` wrapper, deliberately slack, catches what the
   server-side timeout cannot: a TCP connection that stops responding without
   the server ever noticing. It is a backstop, not the mechanism.

Errors are returned, never raised. The correction loop's whole job is to read
an error string and try again; making it catch exceptions instead would put
control flow in an except block on the hot path.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from backend.db.connection import ExecutionSessionLocal
from backend.safety import limits

logger = logging.getLogger(__name__)

# Extra wall-clock room for the client-side backstop, on top of the server's
# own statement_timeout. Only a dead socket should ever reach it.
_CLIENT_GRACE_SECONDS = 5.0

# Postgres SQLSTATEs worth translating into something a corrector can act on.
_SQLSTATE_QUERY_CANCELED = "57014"
_SQLSTATE_INSUFFICIENT_PRIVILEGE = "42501"
_SQLSTATE_READ_ONLY_TRANSACTION = "25006"


@dataclass(slots=True)
class QueryExecutionResult:
    """Outcome of one execution attempt. ``error`` set means nothing ran."""

    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _jsonable(value: Any) -> Any:
    """Coerce Postgres types into something JSON serialisation can carry.

    Numeric comes back as ``Decimal`` and timestamps as ``datetime``; both
    reach the API layer and the LLM judge, so they are normalised once here
    rather than in each consumer. Decimal becomes ``float`` — these values are
    query output for display, not money being arithmetic'd on.
    """
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, memoryview | bytes | bytearray):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _describe(exc: BaseException) -> str:
    """Turn a driver exception into a message the corrector can use."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)

    if sqlstate == _SQLSTATE_QUERY_CANCELED:
        return (
            f"query exceeded the {limits.query_timeout_seconds()}s statement timeout "
            "and was cancelled by the server"
        )
    if sqlstate in (_SQLSTATE_INSUFFICIENT_PRIVILEGE, _SQLSTATE_READ_ONLY_TRANSACTION):
        return (
            "the execution role is read-only and refused this statement " f"(SQLSTATE {sqlstate})"
        )

    orig = getattr(exc, "orig", None)
    message = str(orig) if orig is not None else str(exc)
    return message.strip() or exc.__class__.__name__


async def execute_query(sql: str, *, timeout_seconds: int | None = None) -> QueryExecutionResult:
    """Execute one validated statement on the read-only role.

    ``sql`` is expected to have already passed
    ``safety.validator.validate_and_prepare``. This function is the sandbox,
    not the gate — it does not re-validate.
    """
    timeout = int(
        timeout_seconds if timeout_seconds is not None else limits.query_timeout_seconds()
    )
    if timeout <= 0:
        return QueryExecutionResult(error=f"invalid query timeout: {timeout}s")

    # Interpolated, not bound: SET LOCAL takes no parameters. `timeout` is an
    # int by construction above, so there is nothing here to inject through.
    timeout_ms = timeout * 1000

    try:
        return await asyncio.wait_for(
            _run(sql, timeout_ms), timeout=timeout + _CLIENT_GRACE_SECONDS
        )
    except TimeoutError:
        logger.warning("execution exceeded the client-side backstop (%ss)", timeout)
        return QueryExecutionResult(
            error=(
                f"query exceeded the {timeout}s timeout and the connection stopped " "responding"
            )
        )
    except DBAPIError as exc:
        return QueryExecutionResult(error=_describe(exc))
    except SQLAlchemyError as exc:
        return QueryExecutionResult(error=_describe(exc))
    except Exception as exc:  # pragma: no cover - unexpected driver failure
        logger.exception("unexpected execution failure")
        return QueryExecutionResult(error=_describe(exc))


async def _run(sql: str, timeout_ms: int) -> QueryExecutionResult:
    """Open a transaction, bound it server-side, run the statement, roll back."""
    async with ExecutionSessionLocal() as session:
        # An explicit transaction is required: SET LOCAL outside one is a
        # no-op that Postgres only warns about, which would silently leave the
        # query unbounded.
        transaction = await session.begin()
        try:
            await session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
            result = await session.execute(text(sql))
            rows = [
                {key: _jsonable(value) for key, value in row.items()}
                for row in result.mappings().all()
            ]
            return QueryExecutionResult(rows=rows, row_count=len(rows), error=None)
        finally:
            # Read-only work: roll back rather than commit. Also the point at
            # which SET LOCAL is discarded before the connection returns to
            # the pool.
            await transaction.rollback()
