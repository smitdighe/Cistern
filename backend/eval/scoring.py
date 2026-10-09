"""Scoring a generated query against a gold query.

Three signals, in increasing cost and decreasing certainty:

* ``exact_match`` — both queries canonicalise to the same AST-derived string.
  Conclusive when true, weak when false: two very different-looking strings
  are frequently the same query.
* ``execution_match`` — both return the same rows. Strong evidence, but only
  as strong as the result set is distinctive. Two queries that both return
  zero rows agree on nothing in particular.
* ``judge`` — Cerebras adjudicates. Costs a network call, so it runs only
  where the cheap signals are genuinely inconclusive (see ``needs_judge``).

The judge receives the question and both SQL strings and nothing else. It is
never told which query came from the pipeline, which came from the benchmark,
or which pipeline stage produced the candidate — that omission is what makes
its verdict independent rather than a second opinion on its own work.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from backend.dependencies import shared_correction_client
from backend.llm import cerebras_client
from backend.llm.base import LLMError
from backend.safety import limits

logger = logging.getLogger(__name__)

DIALECT = "postgres"

# Reasons a comparison was or was not escalated, recorded per pair.
JUDGE_SKIPPED_EXACT = "skipped: exact match is conclusive"
JUDGE_SKIPPED_NO_SQL = "skipped: no generated SQL to compare"
JUDGE_SKIPPED_STRONG = "skipped: execution match on a distinctive result set"
JUDGE_REASON_DIFFERENT_ROWS = "escalated: SQL differs and result sets differ"
JUDGE_REASON_WEAK_EVIDENCE = "escalated: execution match on a weak result set"


def _canonical(sql: str, *, drop_injected_limit: bool = False) -> str | None:
    """Parse and re-serialise a query into a comparable canonical form.

    Normalises whitespace, keyword casing, identifier quoting and trailing
    semicolons by going through the AST — never by string munging.

    ``drop_injected_limit`` removes a ``LIMIT`` equal to the configured
    default. The safety layer injects that limit into every unbounded SELECT,
    so the pipeline's output always carries it and a hand-written gold query
    never does. Comparing without accounting for it would score every single
    pair as a mismatch on a clause the model did not write.
    """
    if not sql or not sql.strip():
        return None

    try:
        statement = sqlglot.parse_one(sql, dialect=DIALECT)
    except ParseError:
        return None

    if statement is None:
        return None

    if drop_injected_limit:
        limit = statement.args.get("limit")
        if limit is not None:
            value = limit.expression
            if isinstance(value, exp.Literal) and not value.is_string:
                try:
                    if int(value.name) == limits.default_query_limit():
                        statement.set("limit", None)
                except (TypeError, ValueError):
                    pass

    statement = normalize_identifiers(statement, dialect=DIALECT)

    # normalize_identifiers folds case but leaves quoting as written, so
    # `SELECT "id"` and `SELECT id` still serialise differently. In Postgres
    # those name the same column whenever the quoted form is already lowercase
    # and needs no quoting, so drop the quotes in exactly that case. A quoted
    # identifier that genuinely requires quoting ("Mixed Case", "select")
    # keeps them, because there it really is a different column.
    for identifier in statement.find_all(exp.Identifier):
        if identifier.quoted and _is_plain_identifier(identifier.name):
            identifier.set("quoted", False)

    return statement.sql(dialect=DIALECT, normalize=True, pretty=False)


def _is_plain_identifier(name: str) -> bool:
    """True when a name needs no quoting to mean itself in Postgres."""
    if not name or not (name[0].isalpha() or name[0] == "_"):
        return False
    if name != name.lower():
        return False
    if not all(c.isalnum() or c == "_" for c in name):
        return False
    return name.upper() not in sqlglot.dialects.postgres.Postgres.Tokenizer.KEYWORDS


def exact_match(generated_sql: str, gold_sql: str) -> bool:
    """True when both queries canonicalise identically."""
    gold_canonical = _canonical(gold_sql)
    generated_canonical = _canonical(generated_sql, drop_injected_limit=True)

    if gold_canonical is None or generated_canonical is None:
        return False

    return gold_canonical == generated_canonical


def has_order_by(sql: str) -> bool:
    """True when the query pins its own row order.

    Read off the AST. A textual search would fire on the string "order by"
    appearing inside a literal or a column name.
    """
    try:
        statement = sqlglot.parse_one(sql, dialect=DIALECT)
    except ParseError:
        return False
    if statement is None:
        return False
    return statement.find(exp.Order) is not None


def _value_key(value: object) -> str:
    """Normalise one cell so equal values from different types compare equal.

    Postgres hands back COUNT as an int and SUM over numeric as a Decimal that
    the executor converts to float; ``1`` and ``1.0`` are the same answer and
    must not be scored as a mismatch. Booleans are checked first because
    ``bool`` is a subclass of ``int`` and True would otherwise key as 1.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"b:{value}"
    if isinstance(value, int | float):
        as_float = float(value)
        if as_float.is_integer():
            return f"n:{int(as_float)}"
        return f"n:{as_float!r}"
    return f"s:{value}"


def _row_key(row: dict) -> tuple:
    """A hashable, order-insensitive identity for one row.

    Keys are sorted so column order in the SELECT list does not matter.
    """
    return tuple(sorted((str(key), _value_key(value)) for key, value in row.items()))


def execution_match(generated_rows: list[dict], gold_rows: list[dict], gold_sql: str) -> bool:
    """Compare two result sets.

    Row order is significant only when the gold query has an ORDER BY. Without
    one, Postgres makes no ordering guarantee, so two correct queries may
    legitimately return the same rows in different orders — comparing as
    ordered sequences would fail them for a difference the SQL never claimed.

    Duplicates are significant either way: this is a multiset comparison, not
    a set comparison. ``SELECT city`` returning three rows and a wrongly
    de-duplicated ``SELECT DISTINCT city`` returning one are different answers.
    """
    if generated_rows is None or gold_rows is None:
        return False

    if len(generated_rows) != len(gold_rows):
        return False

    if has_order_by(gold_sql):
        return [_row_key(r) for r in generated_rows] == [_row_key(r) for r in gold_rows]

    return Counter(_row_key(r) for r in generated_rows) == Counter(_row_key(r) for r in gold_rows)


def _is_weak_evidence(rows: list[dict]) -> bool:
    """True when agreeing on this result set proves little.

    Two queries that both return nothing agree by accident as often as by
    correctness, and a single scalar (one row, one column) collides constantly
    — every COUNT that happens to land on the same number looks like a match.
    Anything wider or longer is distinctive enough to trust.
    """
    if not rows:
        return True
    if len(rows) == 1 and len(rows[0]) <= 1:
        return True
    return False


def needs_judge(
    *,
    generated_sql: str | None,
    exact: bool,
    execution: bool,
    gold_rows: list[dict] | None,
) -> tuple[bool, str]:
    """Decide whether to spend a judge call. Returns ``(escalate, reason)``.

    The heuristic, stated plainly:

    * exact match — conclusive, never escalate. The queries are the same query.
    * no generated SQL — nothing to adjudicate; the pipeline failed outright
      and that is a clear result, not an ambiguous one.
    * SQL differs, results differ — escalate. This is where the cheap signals
      are least trustworthy: differing column aliases, an extra projected
      column, or a different aggregation shape all produce unequal rows for
      queries that answer the question equally well. A real bug looks
      identical here, which is exactly why a judgement is needed.
    * SQL differs, results match, result set is distinctive — do not escalate.
      Matching on a wide, multi-row result is strong enough on its own.
    * SQL differs, results match, result set is weak (empty, or a single
      scalar) — escalate. Agreement on nothing in particular is not evidence.
    """
    if exact:
        return False, JUDGE_SKIPPED_EXACT

    if not generated_sql or not generated_sql.strip():
        return False, JUDGE_SKIPPED_NO_SQL

    if not execution:
        return True, JUDGE_REASON_DIFFERENT_ROWS

    if _is_weak_evidence(gold_rows or []):
        return True, JUDGE_REASON_WEAK_EVIDENCE

    return False, JUDGE_SKIPPED_STRONG


def summarise_rows(rows: list[dict] | None, *, error: str | None = None, cap: int = 5) -> str:
    """Compact, human-readable description of a result set for the judge.

    Truncated on purpose: the judge is deciding semantic equivalence, and
    pasting a 500-row result into the prompt buys nothing but tokens.
    """
    if error:
        return f"query failed: {error}"
    if rows is None:
        return "query did not run"
    if not rows:
        return "0 rows"

    columns = ", ".join(rows[0].keys())
    head = "; ".join(str(dict(row)) for row in rows[:cap])
    suffix = "" if len(rows) <= cap else f" ... (+{len(rows) - cap} more rows)"
    return f"{len(rows)} rows, columns: [{columns}]. First rows: {head}{suffix}"


@dataclass(slots=True)
class ScoreResult:
    """Both metrics for one pair, plus the judge's ruling when one was sought."""

    exact_match: bool
    execution_match: bool
    judge_verdict: bool | None = None
    judge_reasoning: str | None = None
    judge_decision: str = ""

    @property
    def semantic_match(self) -> bool:
        """Best available verdict: exact, then execution, then the judge."""
        if self.exact_match or self.execution_match:
            return True
        return bool(self.judge_verdict)


async def score_pair(
    *,
    question: str,
    gold_sql: str,
    generated_sql: str | None,
    gold_rows: list[dict] | None,
    generated_rows: list[dict] | None,
    gold_error: str | None = None,
    generated_error: str | None = None,
    use_judge: bool = True,
) -> ScoreResult:
    """Score one benchmark pair, escalating to the judge only where it helps."""
    exact = bool(generated_sql) and exact_match(generated_sql or "", gold_sql)

    execution = (
        execution_match(generated_rows, gold_rows, gold_sql)
        if generated_rows is not None and gold_rows is not None
        else False
    )

    escalate, decision = needs_judge(
        generated_sql=generated_sql,
        exact=exact,
        execution=execution,
        gold_rows=gold_rows,
    )

    if not (escalate and use_judge):
        return ScoreResult(
            exact_match=exact,
            execution_match=execution,
            judge_decision=decision if use_judge else "skipped: judge disabled",
        )

    try:
        verdict = await cerebras_client.judge(
            question,
            gold_sql=gold_sql,
            generated_sql=generated_sql or "",
            gold_result_summary=summarise_rows(gold_rows, error=gold_error),
            generated_result_summary=summarise_rows(generated_rows, error=generated_error),
            client=shared_correction_client(),
        )
    except LLMError as exc:
        logger.warning("judge call failed, leaving pair unadjudicated: %s", exc)
        return ScoreResult(
            exact_match=exact,
            execution_match=execution,
            judge_decision=f"{decision} (judge unavailable: {exc})",
        )

    return ScoreResult(
        exact_match=exact,
        execution_match=execution,
        judge_verdict=verdict.verdict,
        judge_reasoning=verdict.reasoning,
        judge_decision=decision,
    )
