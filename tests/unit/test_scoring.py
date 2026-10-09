"""Scoring metrics and the judge-escalation heuristic."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://ro:pw@placeholder.invalid/db")
os.environ.setdefault("DATABASE_ADMIN_URL", "postgresql://admin:pw@placeholder.invalid/db")

import pytest  # noqa: E402

from backend.eval import scoring  # noqa: E402
from backend.llm import cerebras_client  # noqa: E402

# --- exact_match ------------------------------------------------------------


@pytest.mark.parametrize(
    ("generated", "gold", "expected"),
    [
        ("select   ID , name\nfrom Orders ;", "SELECT id, name FROM orders", True),
        ('SELECT "id" FROM "orders"', "SELECT id FROM orders", True),
        ("SELECT id FROM orders", "SELECT id FROM customers", False),
        ("SELECT COUNT(*) AS n FROM orders", "SELECT COUNT(*) AS order_count FROM orders", False),
        ("SELCT bad(((", "SELECT id FROM orders", False),
        ("", "SELECT id FROM orders", False),
    ],
)
def test_exact_match_canonicalises(generated, gold, expected):
    assert scoring.exact_match(generated, gold) is expected


def test_injected_limit_is_discounted():
    """The safety layer adds LIMIT 500; the model did not write it."""
    assert scoring.exact_match("SELECT id FROM orders LIMIT 500", "SELECT id FROM orders") is True


def test_model_written_limit_is_not_discounted():
    """A LIMIT the model chose is a real difference from gold."""
    assert scoring.exact_match("SELECT id FROM orders LIMIT 5", "SELECT id FROM orders") is False


# --- execution_match --------------------------------------------------------


def test_row_order_ignored_without_order_by():
    a = [{"id": 1}, {"id": 2}]
    b = [{"id": 2}, {"id": 1}]
    assert scoring.execution_match(a, b, "SELECT id FROM orders") is True


def test_row_order_enforced_with_order_by():
    a = [{"id": 1}, {"id": 2}]
    b = [{"id": 2}, {"id": 1}]
    assert scoring.execution_match(a, b, "SELECT id FROM orders ORDER BY id") is False
    assert scoring.execution_match(a, a, "SELECT id FROM orders ORDER BY id") is True


def test_duplicates_are_significant():
    """Multiset, not set: a wrongly de-duplicated result is a wrong answer."""
    assert (
        scoring.execution_match([{"c": "x"}, {"c": "x"}], [{"c": "x"}], "SELECT c FROM t") is False
    )


def test_column_order_ignored():
    assert scoring.execution_match([{"a": 1, "b": 2}], [{"b": 2, "a": 1}], "SELECT a, b FROM t")


def test_int_and_float_compare_equal():
    """COUNT returns int, SUM over numeric returns float; 1 == 1.0."""
    assert scoring.execution_match([{"n": 1}], [{"n": 1.0}], "SELECT n FROM t") is True


def test_bool_does_not_collide_with_int():
    assert scoring.execution_match([{"v": True}], [{"v": 1}], "SELECT v FROM t") is False


def test_order_by_detection_ignores_string_literals():
    assert scoring.has_order_by("SELECT id FROM t WHERE label = 'order by'") is False
    assert scoring.has_order_by("SELECT id FROM t ORDER BY id") is True


# --- judge escalation heuristic ---------------------------------------------

WIDE = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
SCALAR = [{"n": 7}]


@pytest.mark.parametrize(
    ("label", "kwargs", "expected"),
    [
        ("exact wins", dict(generated_sql="S", exact=True, execution=True, gold_rows=WIDE), False),
        ("no sql", dict(generated_sql=None, exact=False, execution=False, gold_rows=WIDE), False),
        (
            "rows differ",
            dict(generated_sql="S", exact=False, execution=False, gold_rows=WIDE),
            True,
        ),
        (
            "strong match",
            dict(generated_sql="S", exact=False, execution=True, gold_rows=WIDE),
            False,
        ),
        (
            "weak scalar",
            dict(generated_sql="S", exact=False, execution=True, gold_rows=SCALAR),
            True,
        ),
        ("weak empty", dict(generated_sql="S", exact=False, execution=True, gold_rows=[]), True),
    ],
)
def test_needs_judge(label, kwargs, expected):
    escalate, reason = scoring.needs_judge(**kwargs)
    assert escalate is expected, f"{label}: {reason}"
    assert reason


# --- judge independence -----------------------------------------------------


async def test_judge_receives_no_attribution(monkeypatch):
    """Cerebras must not learn which SQL came from the pipeline."""
    seen = {}

    class FakeJudge:
        async def __call__(
            self,
            question,
            gold_sql,
            generated_sql,
            gold_result_summary,
            generated_result_summary,
            **kwargs,
        ):
            seen["payload"] = " ".join(
                [
                    question,
                    gold_sql,
                    generated_sql,
                    gold_result_summary,
                    generated_result_summary,
                ]
            ).lower()

            class Verdict:
                verdict = True
                reasoning = "equivalent"

            return Verdict()

    monkeypatch.setattr(cerebras_client, "judge", FakeJudge())

    result = await scoring.score_pair(
        question="how many orders",
        gold_sql="SELECT COUNT(*) FROM orders",
        generated_sql="SELECT COUNT(id) FROM orders",
        gold_rows=[{"c": 5}],
        generated_rows=[{"c": 5}],
    )

    for leak in ("groq", "cerebras", "llama", "pipeline", "generated by", "correction stage"):
        assert leak not in seen["payload"], f"judge payload leaked {leak!r}"

    assert result.judge_verdict is True
    assert result.semantic_match is True


async def test_judge_failure_leaves_pair_unadjudicated(monkeypatch):
    from backend.llm.base import LLMError

    async def exploding(*args, **kwargs):
        raise LLMError("cerebras is down")

    monkeypatch.setattr(cerebras_client, "judge", exploding)

    result = await scoring.score_pair(
        question="q",
        gold_sql="SELECT a FROM t",
        generated_sql="SELECT b FROM t",
        gold_rows=[{"a": 1}],
        generated_rows=[{"b": 2}],
    )

    assert result.judge_verdict is None
    assert "judge unavailable" in result.judge_decision
    assert result.semantic_match is False


async def test_judge_not_called_when_disabled(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("judge must not be called when use_judge=False")

    monkeypatch.setattr(cerebras_client, "judge", should_not_run)

    result = await scoring.score_pair(
        question="q",
        gold_sql="SELECT a FROM t",
        generated_sql="SELECT b FROM t",
        gold_rows=[{"a": 1}],
        generated_rows=[{"b": 2}],
        use_judge=False,
    )

    assert result.judge_verdict is None


# --- summary warnings -------------------------------------------------------


def test_uniform_zero_score_is_flagged_as_suspicious():
    from backend.eval.eval_runner import CaseOutcome, build_summary

    outcomes = [
        CaseOutcome(
            case_id=f"c{i}",
            question="q",
            gold_sql="SELECT 1",
            generated_sql="SELECT 2",
            exact_match=False,
            execution_match=False,
            judge_verdict=None,
            judge_reasoning=None,
            judge_decision="",
            pipeline_status="failed",
            attempts=1,
            latency_ms=10,
            gold_error=None,
            generated_error="boom",
            clarifying_question=None,
        )
        for i in range(3)
    ]

    summary = build_summary("run-1", outcomes)

    assert summary.exact_match_pct == 0.0
    assert summary.execution_match_pct == 0.0
    assert any("0% on BOTH metrics" in w for w in summary.warnings)
