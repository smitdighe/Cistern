"""Audit-row construction for every pipeline exit path.

The database session is faked, so this verifies exactly what would be written
— column by column — without a live Neon instance. It does not verify that the
INSERT lands; that needs credentials.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://ro:pw@placeholder.invalid/db")
os.environ.setdefault("DATABASE_ADMIN_URL", "postgresql://admin:pw@placeholder.invalid/db")

import pytest  # noqa: E402

from backend.app_logging import query_log  # noqa: E402
from backend.db.models import QueryLog  # noqa: E402
from backend.orchestration.pipeline import (  # noqa: E402
    STATUS_AMBIGUOUS,
    STATUS_FAILED,
    STATUS_SUCCESS,
    PipelineResult,
)


class FakeSession:
    """Captures the row instead of writing it."""

    captured: list[QueryLog] = []

    def __init__(self):
        self.rows: list[QueryLog] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, row):
        self.rows.append(row)
        FakeSession.captured.append(row)

    async def commit(self):
        pass

    async def refresh(self, row):
        row.id = 42


@pytest.fixture
def captured(monkeypatch):
    FakeSession.captured = []
    monkeypatch.setattr(query_log, "AdminSessionLocal", FakeSession)
    return FakeSession.captured


def _trail(**overrides):
    entry = {
        "attempt": 1,
        "source": "generation",
        "sql": None,
        "safety_flags": [],
        "validation": None,
        "rejection_reason": None,
        "executed": False,
        "error": None,
        "row_count": None,
        "result_summary": None,
        "reasoning": None,
        "stalled": False,
    }
    entry.update(overrides)
    return entry


# --- (1) clean success ------------------------------------------------------


async def test_success_row(captured):
    result = PipelineResult(
        sql="SELECT id FROM orders LIMIT 500",
        result=[{"id": 1}],
        explanation="Counts orders.",
        attempts=1,
        safety_flags=["statement:select", "limit_injected:500"],
        success=True,
        status=STATUS_SUCCESS,
        error=None,
        attempt_trail=[_trail(sql="SELECT id FROM orders LIMIT 500", executed=True, row_count=1)],
        question="how many orders",
        latency_ms=734,
    )

    row_id = await query_log.log_query_attempt(result)

    assert row_id == 42
    row = captured[0]
    assert row.question == "how many orders"
    assert row.generated_sql == "SELECT id FROM orders LIMIT 500"
    assert row.final_sql == "SELECT id FROM orders LIMIT 500"
    assert row.attempt_count == 1
    assert row.latency_ms == 734
    assert row.success is True
    assert row.error is None
    assert row.safety_flags["flags"] == ["statement:select", "limit_injected:500"]
    assert row.safety_flags["status"] == STATUS_SUCCESS
    assert len(row.safety_flags["attempt_trail"]) == 1


# --- (2) safety rejection ---------------------------------------------------


async def test_safety_rejection_row(captured):
    result = PipelineResult(
        sql=None,
        result=None,
        explanation=None,
        attempts=1,
        safety_flags=["statement:select", "unknown_column"],
        success=False,
        status=STATUS_FAILED,
        error="unknown column 'revenue' — not present in any referenced table.",
        attempt_trail=[
            _trail(sql="SELECT revenue FROM orders", validation="rejected"),
        ],
        question="total revenue",
        latency_ms=310,
    )

    await query_log.log_query_attempt(result)

    row = captured[0]
    # The rejected SQL is still recorded — that is the point of an audit row.
    assert row.generated_sql == "SELECT revenue FROM orders"
    assert row.final_sql == "", "nothing ran, so there is no final SQL"
    assert row.success is False
    assert "revenue" in row.error
    assert row.attempt_count == 1
    assert row.latency_ms == 310


# --- (3) ambiguity short-circuit --------------------------------------------


async def test_ambiguous_row(captured):
    result = PipelineResult(
        sql=None,
        result=None,
        explanation=None,
        attempts=0,
        safety_flags=[],
        ambiguity_question="Do you mean top by order count or by total spent?",
        success=False,
        status=STATUS_AMBIGUOUS,
        error=None,
        attempt_trail=[_trail(attempt=0, source="ambiguity")],
        question="show me the top customers",
        latency_ms=180,
    )

    await query_log.log_query_attempt(result)

    row = captured[0]
    assert row.question == "show me the top customers"
    assert row.generated_sql == "", "no SQL was ever generated"
    assert row.final_sql == ""
    assert row.attempt_count == 0
    assert row.error is None, "ambiguity is not an error"
    assert row.success is False
    assert row.safety_flags["status"] == STATUS_AMBIGUOUS
    assert row.latency_ms == 180


# --- (4) correction exhaustion ----------------------------------------------


async def test_correction_exhaustion_row(captured):
    trail = [
        _trail(attempt=1, sql="SELECT a FROM orders", executed=True, error="boom"),
        _trail(
            attempt=2, source="correction", sql="SELECT b FROM orders", executed=True, error="boom"
        ),
        _trail(
            attempt=3, source="correction", sql="SELECT c FROM orders", executed=True, error="boom"
        ),
        _trail(
            attempt=4, source="correction", sql="SELECT d FROM orders", executed=True, error="boom"
        ),
    ]
    result = PipelineResult(
        sql="SELECT d FROM orders",
        result=None,
        explanation=None,
        attempts=4,
        safety_flags=[],
        success=False,
        status=STATUS_FAILED,
        error="boom",
        attempt_trail=trail,
        question="anything",
        latency_ms=5120,
    )

    await query_log.log_query_attempt(result)

    row = captured[0]
    assert row.attempt_count == 4
    assert row.generated_sql == "SELECT a FROM orders", "first generation attempt"
    assert row.final_sql == "SELECT d FROM orders", "last attempted"
    assert row.error == "boom"
    assert row.latency_ms == 5120
    # Phase 4 promised nothing is dropped from the trail.
    assert len(row.safety_flags["attempt_trail"]) == 4
    assert [e["attempt"] for e in row.safety_flags["attempt_trail"]] == [1, 2, 3, 4]


# --- failure policy ---------------------------------------------------------


async def test_write_failure_is_swallowed(monkeypatch):
    """An audit failure must never surface to the caller."""

    class ExplodingSession:
        async def __aenter__(self):
            raise RuntimeError("neon is down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(query_log, "AdminSessionLocal", ExplodingSession)

    result = PipelineResult(
        sql="SELECT 1",
        result=[{"?column?": 1}],
        explanation=None,
        attempts=1,
        success=True,
        status=STATUS_SUCCESS,
        question="q",
        latency_ms=5,
    )

    assert await query_log.log_query_attempt(result) is None


async def test_write_path_uses_admin_not_execution_engine():
    """The read-only role cannot INSERT; the audit path must not use it."""
    import inspect

    source = inspect.getsource(query_log)

    assert "AdminSessionLocal" in source
    assert "ExecutionSessionLocal" not in source
    assert "get_execution_session" not in source
