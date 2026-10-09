"""Pipeline short-circuit on ambiguity.

The load-bearing assertion in this file is a call count, not a returned value:
an ambiguous question must reach *zero* calls to generate_sql, the validator
and the executor. A pipeline that generated SQL and then discarded it would
return an identical PipelineResult, so only the counters can tell the
difference.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://ro:pw@placeholder.invalid/db")
os.environ.setdefault("DATABASE_ADMIN_URL", "postgresql://admin:pw@placeholder.invalid/db")

import pytest  # noqa: E402

from backend.ambiguity.detector import AmbiguityResult  # noqa: E402
from backend.db import introspect  # noqa: E402
from backend.db.executor import QueryExecutionResult  # noqa: E402
from backend.llm import cerebras_client, groq_client  # noqa: E402
from backend.llm.base import LLMResponse  # noqa: E402
from backend.llm.groq_client import GenerationResult  # noqa: E402
from backend.orchestration import pipeline  # noqa: E402
from backend.orchestration.pipeline import (  # noqa: E402
    STATUS_AMBIGUOUS,
    STATUS_SUCCESS,
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

CLEAR_SQL = "SELECT id, placed_at FROM orders WHERE placed_at > now() - INTERVAL '7 days'"


class CountingGenerator:
    """Counts every generate_sql invocation."""

    def __init__(self, sql: str):
        self.sql = sql
        self.calls = 0

    async def __call__(self, question, schema, **kwargs):
        self.calls += 1
        return GenerationResult(
            sql=self.sql,
            is_ambiguous=False,
            confidence=0.95,
            raw=LLMResponse(text="", raw={}, latency_ms=1, model="test-model", usage=None),
            ambiguity_reason=None,
        )


class CountingExecutor:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[str] = []

    async def __call__(self, sql: str) -> QueryExecutionResult:
        self.calls.append(sql)
        return QueryExecutionResult(rows=self.rows, row_count=len(self.rows), error=None)


class CountingDetector:
    def __init__(self, result: AmbiguityResult):
        self.result = result
        self.calls: list[str] = []

    async def __call__(self, question, schema, **kwargs):
        self.calls.append(question)
        return self.result


@pytest.fixture
def wired(monkeypatch):
    """Schema + explanation stubbed; each test installs its own detector."""

    async def fake_get_schema(schema: str = "public"):
        return SCHEMA

    async def fake_explain(question, sql, **kwargs):
        return f"EXPLAIN::{sql}"

    async def fake_log(result):
        return 1

    monkeypatch.setattr(introspect, "get_schema", fake_get_schema)
    monkeypatch.setattr(cerebras_client, "explain_sql", fake_explain)
    monkeypatch.setattr(pipeline, "log_query_attempt", fake_log)

    def install(detector_result, generator_sql=CLEAR_SQL, rows=None):
        det = CountingDetector(detector_result)
        gen = CountingGenerator(generator_sql)
        ex = CountingExecutor(rows if rows is not None else [{"id": 1}])
        monkeypatch.setattr(pipeline, "detect_ambiguity", det)
        monkeypatch.setattr(groq_client, "generate_sql", gen)
        monkeypatch.setattr(pipeline, "execute_sql", ex)
        return det, gen, ex

    return install


# --- ambiguous: short-circuit ------------------------------------------------


async def test_ambiguous_question_short_circuits_with_zero_generation_calls(wired):
    detector_result = AmbiguityResult(
        is_ambiguous=True,
        clarifying_question="Do you mean top by number of orders, or by total spent?",
        reasoning="'top' is not defined by the schema",
    )
    det, gen, ex = wired(detector_result)

    result = await pipeline.run_pipeline(AMBIGUOUS_QUESTION)

    # The assertion this whole file exists for.
    assert gen.calls == 0, "generate_sql must not be called for an ambiguous question"
    assert ex.calls == [], "nothing may be executed for an ambiguous question"
    assert det.calls == [AMBIGUOUS_QUESTION]

    assert result.status == STATUS_AMBIGUOUS
    assert result.ambiguity_question == ("Do you mean top by number of orders, or by total spent?")
    assert result.success is False
    assert result.error is None, "ambiguity is not an error"
    assert result.sql is None
    assert result.result is None
    assert result.explanation is None
    assert result.attempts == 0, "no SQL-producing attempt was made"
    # The trail still records why we stopped — phase 7 persists this.
    assert len(result.attempt_trail) == 1
    assert result.attempt_trail[0]["source"] == "ambiguity"
    assert result.attempt_trail[0]["reasoning"] == "'top' is not defined by the schema"


async def test_ambiguous_status_is_distinguishable_from_failure(wired):
    """routes/ must be able to tell 'asking a question back' from 'broke'."""
    _, _, _ = wired(AmbiguityResult(is_ambiguous=True, clarifying_question="By what measure?"))

    ambiguous = await pipeline.run_pipeline(AMBIGUOUS_QUESTION)

    # Both are success=False; only `status` separates them.
    assert ambiguous.success is False
    assert ambiguous.status == STATUS_AMBIGUOUS
    assert ambiguous.error is None


# --- unambiguous: full pipeline runs -----------------------------------------


async def test_clear_question_proceeds_to_generation(wired):
    det, gen, ex = wired(
        AmbiguityResult(is_ambiguous=False, reasoning="placed_at supplies the bound"),
        rows=[{"id": 1, "placed_at": "2026-07-15"}],
    )

    result = await pipeline.run_pipeline(CLEAR_QUESTION)

    assert det.calls == [CLEAR_QUESTION], "the gate still runs for clear questions"
    assert gen.calls == 1, "generation must run exactly once"
    assert len(ex.calls) == 1

    assert result.status == STATUS_SUCCESS
    assert result.success is True
    assert result.ambiguity_question is None
    assert result.result == [{"id": 1, "placed_at": "2026-07-15"}]
    assert result.attempts == 1
    assert result.sql.endswith("LIMIT 500")


async def test_detector_failure_does_not_block_a_clear_question(wired):
    """Fail-open, end to end: a dead detector must not stop a good question."""
    det, gen, ex = wired(
        AmbiguityResult(is_ambiguous=False, reasoning="detector unavailable: boom")
    )

    result = await pipeline.run_pipeline(CLEAR_QUESTION)

    assert gen.calls == 1
    assert result.status == STATUS_SUCCESS
    assert result.ambiguity_question is None
