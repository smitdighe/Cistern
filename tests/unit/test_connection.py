"""Proves the execution role is read-only at the database level.

The point of these tests is that no application code enforces the restriction —
Postgres does. A passing result here means a bug anywhere in the pipeline still
cannot write through the execution engine.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _live_db_configured() -> bool:
    """True only when both URLs are set and are not test placeholders.

    Other test modules inject ``placeholder.invalid`` URLs into the
    environment at import time so config can load without credentials; those
    must read as "no live database", not as a reachable server.
    """
    url = os.getenv("DATABASE_URL", "")
    admin_url = os.getenv("DATABASE_ADMIN_URL", "")
    if not (url and admin_url):
        return False
    return "placeholder.invalid" not in url and "placeholder.invalid" not in admin_url


pytestmark = pytest.mark.skipif(
    not _live_db_configured(),
    reason="requires a live Postgres; set DATABASE_URL and DATABASE_ADMIN_URL",
)

# asyncpg SQLSTATEs that mean "the server refused the write".
#   42501 insufficient_privilege — role lacks INSERT/UPDATE grant
#   25006 read_only_sql_transaction — session or transaction is read-only
_REFUSAL_SQLSTATES = {"42501", "25006"}


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


@pytest.mark.asyncio
async def test_execution_engine_can_read():
    """Guards the tests below: a connectivity failure must not read as a pass."""
    from backend.db.connection import ExecutionSessionLocal

    async with ExecutionSessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_execution_engine_rejects_insert():
    from backend.db.connection import ExecutionSessionLocal

    async with ExecutionSessionLocal() as session:
        with pytest.raises(DBAPIError) as excinfo:
            await session.execute(
                text(
                    "INSERT INTO query_log "
                    "(question, generated_sql, final_sql, attempt_count, "
                    " safety_flags, latency_ms, success) "
                    "VALUES ('probe', 'SELECT 1', 'SELECT 1', 1, '{}'::jsonb, 1, true)"
                )
            )
            await session.commit()

    assert (
        _sqlstate(excinfo.value) in _REFUSAL_SQLSTATES
    ), f"INSERT failed, but not because the role is read-only: {excinfo.value!r}"


@pytest.mark.asyncio
async def test_execution_engine_rejects_update():
    from backend.db.connection import ExecutionSessionLocal

    async with ExecutionSessionLocal() as session:
        with pytest.raises(DBAPIError) as excinfo:
            await session.execute(text("UPDATE query_log SET success = false"))
            await session.commit()

    assert (
        _sqlstate(excinfo.value) in _REFUSAL_SQLSTATES
    ), f"UPDATE failed, but not because the role is read-only: {excinfo.value!r}"


@pytest.mark.asyncio
async def test_execution_and_admin_engines_are_distinct():
    """The two engines must not share a URL, a pool, or a sessionmaker."""
    from backend.db.connection import (
        AdminSessionLocal,
        ExecutionSessionLocal,
        admin_engine,
        execution_engine,
    )

    assert execution_engine is not admin_engine
    assert ExecutionSessionLocal is not AdminSessionLocal
    assert execution_engine.pool is not admin_engine.pool
