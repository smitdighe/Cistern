"""Pipeline + correction loop integration, with mocked LLMs and executor.

No network, no database: Groq/Cerebras calls and SQL execution are replaced by
fakes injected via monkeypatch. The validator runs for real — these tests
exercise the actual safety gate, not a mock of it.
"""

import os

# Must precede any backend import: config fails fast without these.
os.environ.setdefault("DATABASE_URL", "postgresql://ro:pw@placeholder.invalid/db")
os.environ.setdefault("DATABASE_ADMIN_URL", "postgresql://admin:pw@placeholder.invalid/db")

import pytest  # noqa: E402

from backend.ambiguity.detector import AmbiguityResult  # noqa: E402
from backend.correction import loop as correction_loop  # noqa: E402
from backend.db import introspect  # noqa: E402
from backend.db.executor import QueryExecutionResult  # noqa: E402
from backend.llm import cerebras_client, groq_client  # noqa: E402
from backend.llm.base import LLMResponse  # noqa: E402
from backend.llm.cerebras_client import CorrectionResult  # noqa: E402
from backend.llm.groq_client import GenerationResult  # noqa: E402
from backend.orchestration import pipeline  # noqa: E402

SCHEMA = {
    "orders": {
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "customer_id", "type": "integer", "nullable": False},
            {"name": "total", "type": "numeric", "nullable": True},
        ],
        "primary_key": ["id"],
        "foreign_keys": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
    },
    "customers": {
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "name", "type": "text", "nullable": False},
        ],
        "primary_key": ["id"],
        "foreign_keys": [],
    },
}


def _llm_response() -> LLMResponse:
    return LLMResponse(text="", raw={}, latency_ms=1, model="test-model", usage=None)


class FakeGenerator:
    """Stands in for groq_client.generate_sql."""

    def __init__(self, sql: str, *, ambiguous: bool = False, reason: str | None = None):
        self.sql = sql
        self.ambiguous = ambiguous
        self.reason = reason
        self.calls = 0

    async def __call__(self, question, schema, **kwargs):
        self.calls += 1
        return GenerationResult(
            sql=self.sql,
            is_ambiguous=self.ambiguous,
            confidence=0.9,
            raw=_llm_response(),
            ambiguity_reason=self.reason,
        )


class FakeCorrector:
    """Stands in for cerebras_client.correct_sql; replays a scripted sequence."""

    def __init__(self, sqls: list[str]):
        self.sqls = list(sqls)
        self.calls: list[dict] = []

    async def __call__(self, question, schema, failed_sql, error, **kwargs):
        self.calls.append({"failed_sql": failed_sql, "error": error})
        index = min(len(self.calls) - 1, len(self.sqls) - 1)
        return CorrectionResult(sql=self.sqls[index], reasoning="scripted", raw=_llm_response())


class FakeExecutor:
    """Stands in for executor.execute_query, keyed on normalised SQL.

    ``script`` maps normalised SQL to ("rows", [...]) or ("error", "message").
    Unscripted SQL raises AssertionError so drift fails loudly instead of
    silently returning default rows.

    Mirrors the real executor's contract: errors come back inside
    QueryExecutionResult, they are never raised.
    """

    def __init__(self, script: dict, default=None):
        self.script = dict(script)
        self.default = default
        self.calls: list[str] = []

    async def __call__(self, sql: str) -> QueryExecutionResult:
        self.calls.append(sql)
        key = correction_loop.normalise_sql(sql)
        action = self.script.get(key, self.default)
        assert action is not None, f"unscripted SQL reached executor: {sql!r}"
        kind, value = action
        if kind == "error":
            return QueryExecutionResult(rows=[], row_count=0, error=value)
        return QueryExecutionResult(rows=value, row_count=len(value), error=None)


class FakeExplainer:
    async def __call__(self, question, sql, **kwargs):
        return f"EXPLAIN::{sql}"


@pytest.fixture
def env(monkeypatch):
    """Patch schema loading, explanation and the audit write.

    The audit write is stubbed so these tests do not attempt a real database
    connection on every run — it is covered directly in test_query_log.py.
    """

    async def fake_get_schema(schema: str = "public"):
        return SCHEMA

    async def fake_log(result):
        return 1

    async def fake_detect(question, schema, **kwargs):
        # Never ambiguous: these tests exercise generation/correction, and the
        # real detector would make a live Groq call whenever a key is present.
        return AmbiguityResult(is_ambiguous=False, reasoning="stubbed for tests")

    monkeypatch.setattr(introspect, "get_schema", fake_get_schema)
    monkeypatch.setattr(cerebras_client, "explain_sql", FakeExplainer())
    monkeypatch.setattr(pipeline, "log_query_attempt", fake_log)
    monkeypatch.setattr(pipeline, "detect_ambiguity", fake_detect)
    return monkeypatch


def _wire(monkeypatch, generator, corrector, executor):
    monkeypatch.setattr(groq_client, "generate_sql", generator)
    monkeypatch.setattr(cerebras_client, "correct_sql", corrector)
    monkeypatch.setattr(pipeline, "execute_sql", executor)


# --- (a) success on first try ------------------------------------------------


async def test_first_try_success_no_correction(env):
    generator = FakeGenerator("SELECT id, total FROM orders")
    corrector = FakeCorrector([])
    executor = FakeExecutor(
        {"select id, total from orders limit 500": ("rows", [{"id": 1, "total": 9}])}
    )
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("total per order")

    assert result.success is True
    assert result.error is None
    assert result.attempts == 1
    assert result.sql == "SELECT id, total FROM orders LIMIT 500"
    assert result.result == [{"id": 1, "total": 9}]
    assert result.explanation == "EXPLAIN::SELECT id, total FROM orders LIMIT 500"
    assert result.ambiguity_question is None
    assert "limit_injected:500" in result.safety_flags
    assert corrector.calls == [], "correction must not run on a clean first try"
    assert len(result.attempt_trail) == 1
    entry = result.attempt_trail[0]
    assert entry["source"] == "generation"
    assert entry["executed"] is True
    assert entry["row_count"] == 1


# --- (b) error on first try, success on retry 1 ------------------------------


async def test_error_then_corrected_success(env):
    generator = FakeGenerator("SELECT total FROM orders")
    corrector = FakeCorrector(["SELECT id FROM orders"])
    executor = FakeExecutor(
        {
            "select total from orders limit 500": ("error", 'column "total" is corrupt'),
            "select id from orders limit 500": ("rows", [{"id": 7}]),
        }
    )
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("order ids")

    assert result.success is True
    assert result.attempts == 2
    assert result.sql == "SELECT id FROM orders LIMIT 500"
    assert result.result == [{"id": 7}]
    assert result.explanation == "EXPLAIN::SELECT id FROM orders LIMIT 500"
    assert len(result.attempt_trail) == 2
    assert result.attempt_trail[0]["error"] == 'column "total" is corrupt'
    assert result.attempt_trail[1]["source"] == "correction"
    assert result.attempt_trail[1]["attempt"] == 2
    assert result.attempt_trail[1]["row_count"] == 1
    # The corrector saw the executed SQL (with LIMIT) and the real error.
    assert corrector.calls[0]["failed_sql"] == "SELECT total FROM orders LIMIT 500"
    assert corrector.calls[0]["error"] == 'column "total" is corrupt'


# --- (c) all attempts fail, full trail returned -------------------------------


async def test_exhaustion_returns_complete_trail(env):
    generator = FakeGenerator("SELECT total FROM orders")
    corrector = FakeCorrector(
        [
            "SELECT id FROM orders",
            "SELECT customer_id FROM orders",
            "SELECT id, customer_id FROM orders",
        ]
    )
    executor = FakeExecutor({}, default=("error", "db exploded"))
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("anything")

    assert result.success is False
    assert result.attempts == 4, "generation + MAX_CORRECTION_ATTEMPTS(3)"
    assert result.error == "db exploded"
    assert result.result is None
    assert result.explanation is None
    assert len(result.attempt_trail) == 4, "no attempt may be dropped from the trail"
    assert [entry["attempt"] for entry in result.attempt_trail] == [1, 2, 3, 4]
    assert all(entry["error"] == "db exploded" for entry in result.attempt_trail)
    assert all(entry["sql"] for entry in result.attempt_trail)
    assert len(corrector.calls) == 3


# --- (d) stall detection ------------------------------------------------------


async def test_stall_detection_aborts_early(env):
    generator = FakeGenerator("SELECT total FROM orders")
    # Same corrected SQL twice in a row: attempt 2 must stall, attempt 3 never happen.
    corrector = FakeCorrector(["SELECT id FROM orders", "SELECT id FROM orders"])
    executor = FakeExecutor({}, default=("error", "still broken"))
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("anything")

    assert result.success is False
    assert len(corrector.calls) == 2, "loop must abort at the stall, not burn attempt 3"
    assert result.attempts == 3  # generation + 2 correction attempts
    assert "stalled" in result.error
    assert "correction_stalled" not in result.safety_flags or result.result is None
    stalled_entries = [e for e in result.attempt_trail if e["stalled"]]
    assert len(stalled_entries) == 1
    assert stalled_entries[0]["attempt"] == 3
    # Only the original and the first corrected query ever executed.
    assert len(executor.calls) == 2


async def test_stall_detection_direct_loop_result(env):
    """Loop-level view of the stall: normalised comparison, casing/whitespace ignored."""
    corrector = FakeCorrector(["select   ID from ORDERS   ;"])
    env.setattr(cerebras_client, "correct_sql", corrector)
    executor = FakeExecutor({}, default=("error", "boom"))

    result = await correction_loop.run_correction(
        "q",
        SCHEMA,
        failed_sql="SELECT id FROM orders",  # normalises identically to the correction
        error="boom",
        execute=executor,
    )

    assert result.stalled is True
    assert result.success is False
    assert result.attempts == 1
    assert result.final_sql is None
    assert executor.calls == [], "a stalled correction must never execute"
    assert result.trail[0]["stalled"] is True
    assert "stalled" in result.last_error


# --- (e) corrected SQL that fails safety validation ---------------------------


async def test_unsafe_correction_never_reaches_execution(env):
    generator = FakeGenerator("SELECT total FROM orders")
    corrector = FakeCorrector(
        [
            "DELETE FROM orders",  # missing WHERE — rejected
            "DELETE FROM orders WHERE id = 1",  # safe but requires confirmation
            "UPDATE orders SET total = 0",  # missing WHERE — rejected
        ]
    )
    executor = FakeExecutor({"select total from orders limit 500": ("error", "original failure")})
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("anything")

    assert result.success is False
    assert result.attempts == 4
    # Only the original generation query ever executed; no corrected DML did.
    assert executor.calls == ["SELECT total FROM orders LIMIT 500"]
    correction_entries = [e for e in result.attempt_trail if e["source"] == "correction"]
    assert len(correction_entries) == 3
    assert correction_entries[0]["validation"] == "rejected"
    assert correction_entries[0]["executed"] is False
    assert "every row" in correction_entries[0]["rejection_reason"]
    assert correction_entries[1]["validation"] == "requires_confirmation"
    assert correction_entries[1]["executed"] is False
    assert correction_entries[2]["validation"] == "rejected"
    # The corrector was told about the safety rejection on the next round.
    assert "rejected by safety validator" in corrector.calls[1]["error"]


async def test_forbidden_correction_aborts_immediately(env):
    generator = FakeGenerator("SELECT total FROM orders")
    corrector = FakeCorrector(
        ["DROP TABLE orders", "SELECT id FROM orders"]  # second must never be requested
    )
    executor = FakeExecutor({"select total from orders limit 500": ("error", "original failure")})
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("anything")

    assert result.success is False
    assert len(corrector.calls) == 1, "forbidden statement must abort the loop instantly"
    assert result.attempts == 2
    assert "correction aborted" in result.error
    assert "correction_aborted_forbidden" in result.safety_flags
    assert result.sql is None, "a forbidden statement is never surfaced as the query"
    assert executor.calls == ["SELECT total FROM orders LIMIT 500"]


async def test_forbidden_correction_direct_loop_result(env):
    corrector = FakeCorrector(["DROP TABLE orders"])
    env.setattr(cerebras_client, "correct_sql", corrector)
    executor = FakeExecutor({}, default=("error", "boom"))

    result = await correction_loop.run_correction(
        "q", SCHEMA, failed_sql="SELECT id FROM orders LIMIT 500", error="boom", execute=executor
    )

    assert result.success is False
    assert result.aborted_reason is not None
    assert "forbidden" in result.aborted_reason
    assert result.attempts == 1
    assert executor.calls == []


# --- safety rejection of the *generated* SQL skips correction entirely --------


async def test_generation_safety_rejection_is_terminal(env):
    generator = FakeGenerator("SELECT revenue FROM orders")  # hallucinated column
    corrector = FakeCorrector(["SELECT id FROM orders"])
    executor = FakeExecutor({})
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("revenue")

    assert result.success is False
    assert "revenue" in result.error
    assert corrector.calls == [], "safety rejections must not trigger correction"
    assert executor.calls == []
    assert result.attempts == 1
    assert result.attempt_trail[0]["validation"] == "rejected"


# --- empty result: correction tried, then the empty answer accepted -----------


async def test_empty_result_accepted_when_correction_cannot_improve(env):
    generator = FakeGenerator("SELECT id FROM orders")
    corrector = FakeCorrector(
        [
            "SELECT customer_id FROM orders",
            "SELECT total FROM orders",
            "SELECT id, total FROM orders",
        ]
    )
    executor = FakeExecutor(
        {"select id from orders limit 500": ("rows", [])},
        default=("error", "corrections keep failing"),
    )
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("orders for a customer with none")

    assert result.success is True, "a clean empty result is an answer, not a failure"
    assert result.result == []
    assert result.sql == "SELECT id FROM orders LIMIT 500"
    assert "empty_result_accepted" in result.safety_flags
    assert result.attempts == 4
    assert result.explanation is not None
    assert len(result.attempt_trail) == 4


async def test_suspicious_all_null_row_triggers_correction(env):
    generator = FakeGenerator("SELECT total FROM orders")
    corrector = FakeCorrector(["SELECT id, total FROM orders"])
    executor = FakeExecutor(
        {
            # Aggregate-over-nothing signature: one row, every value NULL.
            "select total from orders limit 500": ("rows", [{"total": None}]),
            "select id, total from orders limit 500": ("rows", [{"id": 1, "total": 3}]),
        }
    )
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("sum of totals")

    assert result.success is True
    assert result.attempts == 2
    assert result.result == [{"id": 1, "total": 3}]
    assert result.attempt_trail[0]["result_summary"] == "1 row, all values NULL"
    assert "all values NULL" in corrector.calls[0]["error"] or "NULL" in corrector.calls[0]["error"]


# --- generation-reported ambiguity -------------------------------------------


async def test_generation_reported_ambiguity_returns_clarification(env):
    generator = FakeGenerator("", ambiguous=True, reason="'best' is not defined by the schema")
    corrector = FakeCorrector([])
    executor = FakeExecutor({})
    _wire(env, generator, corrector, executor)

    result = await pipeline.run_pipeline("who is the best customer")

    assert result.success is False
    assert result.error is None
    assert result.ambiguity_question == "'best' is not defined by the schema"
    assert result.sql is None
    assert executor.calls == []
    assert corrector.calls == []
    assert result.attempts == 1
