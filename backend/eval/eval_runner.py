"""Run the benchmark through the real pipeline and score every pair.

Calls ``run_pipeline`` directly rather than going over HTTP. That is not a
shortcut — it is the point. The pipeline owns the audit write (phase 7), so a
benchmark run produces the same ``query_log`` rows a real request would, and
the eval exercises the same code path users hit rather than a parallel one
that can silently drift.

Gold queries are executed through the same sandboxed read-only executor as
generated ones, and the same LIMIT is applied to both. Comparing a capped
result against an uncapped one would mark every large answer wrong.

Run with:  python -m backend.eval.eval_runner
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot.errors import ParseError

from backend.app_logging.setup import configure_logging
from backend.db import introspect
from backend.db.executor import execute_query
from backend.db.models import BenchmarkResult
from backend.eval import scoring
from backend.orchestration.pipeline import run_pipeline
from backend.safety import limits

logger = logging.getLogger(__name__)

BENCHMARK_PATH = Path(__file__).parent / "benchmark_subset.json"


class BenchmarkSchemaMismatch(RuntimeError):
    """The benchmark's gold SQL does not fit the live database."""


@dataclass(slots=True)
class CaseOutcome:
    """Everything one benchmark case produced."""

    case_id: str
    question: str
    gold_sql: str
    generated_sql: str | None
    exact_match: bool
    execution_match: bool
    judge_verdict: bool | None
    judge_reasoning: str | None
    judge_decision: str
    pipeline_status: str
    attempts: int
    latency_ms: int
    gold_error: str | None
    generated_error: str | None
    clarifying_question: str | None


@dataclass(slots=True)
class EvalSummary:
    """Aggregate result of one run."""

    run_id: str
    total_count: int
    exact_match_pct: float
    execution_match_pct: float
    semantic_match_pct: float
    judged_count: int
    pipeline_failure_count: int
    ambiguous_count: int
    gold_error_count: int
    warnings: list[str] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)


def load_benchmark(path: Path = BENCHMARK_PATH) -> dict[str, Any]:
    """Read and sanity-check the benchmark file."""
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    cases = payload.get("cases") or []
    if not cases:
        raise ValueError(f"benchmark at {path} contains no cases")

    seen: set[str] = set()
    for case in cases:
        for key in ("id", "question", "gold_sql"):
            if not case.get(key):
                raise ValueError(f"benchmark case missing '{key}': {case!r}")
        if case["id"] in seen:
            raise ValueError(f"duplicate benchmark case id: {case['id']}")
        seen.add(case["id"])

        try:
            sqlglot.parse_one(case["gold_sql"], dialect=scoring.DIALECT)
        except ParseError as exc:
            raise ValueError(f"gold_sql for case '{case['id']}' does not parse: {exc}") from exc

    return payload


def check_schema_assumptions(payload: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Compare the benchmark's declared schema against the live one.

    Returns a list of mismatches. A benchmark written for a different schema
    scores 0% on everything, which looks exactly like a broken pipeline — this
    check exists so that failure mode is reported as what it is.
    """
    assumptions = payload.get("schema_assumptions") or {}
    if not assumptions:
        return []

    live = {
        table.lower(): {c["name"].lower() for c in (info or {}).get("columns") or []}
        for table, info in (schema or {}).items()
    }

    problems: list[str] = []
    for table, columns in assumptions.items():
        live_columns = live.get(table.lower())
        if live_columns is None:
            problems.append(f"table '{table}' does not exist in the live schema")
            continue
        missing = sorted({c.lower() for c in columns} - live_columns)
        if missing:
            problems.append(f"table '{table}' is missing column(s): {', '.join(missing)}")

    return problems


async def _execute_gold(gold_sql: str):
    """Run the gold query under the same row cap the pipeline applies.

    Without this, a gold query returning 900 rows would be compared against a
    generated query the safety layer capped at 500 and marked wrong for a
    difference neither model caused.
    """
    try:
        statement = sqlglot.parse_one(gold_sql, dialect=scoring.DIALECT)
        bounded, _ = limits.apply_limit(statement)
        sql = bounded.sql(dialect=scoring.DIALECT)
    except ParseError:
        sql = gold_sql

    return await execute_query(sql)


async def run_case(case: dict[str, Any], *, use_judge: bool = True) -> CaseOutcome:
    """Run one question through the pipeline and score it against gold."""
    question = case["question"]
    gold_sql = case["gold_sql"]

    pipeline_result = await run_pipeline(question)
    gold = await _execute_gold(gold_sql)

    generated_rows = pipeline_result.result if pipeline_result.success else None
    gold_rows = gold.rows if gold.ok else None

    score = await scoring.score_pair(
        question=question,
        gold_sql=gold_sql,
        generated_sql=pipeline_result.sql,
        gold_rows=gold_rows,
        generated_rows=generated_rows,
        gold_error=gold.error,
        generated_error=pipeline_result.error,
        use_judge=use_judge,
    )

    return CaseOutcome(
        case_id=case["id"],
        question=question,
        gold_sql=gold_sql,
        generated_sql=pipeline_result.sql,
        exact_match=score.exact_match,
        execution_match=score.execution_match,
        judge_verdict=score.judge_verdict,
        judge_reasoning=score.judge_reasoning,
        judge_decision=score.judge_decision,
        pipeline_status=pipeline_result.status,
        attempts=pipeline_result.attempts,
        latency_ms=pipeline_result.latency_ms,
        gold_error=gold.error,
        generated_error=pipeline_result.error,
        clarifying_question=pipeline_result.ambiguity_question,
    )


async def persist_results(run_id: str, outcomes: list[CaseOutcome]) -> int:
    """Write one ``benchmark_results`` row per case. Returns rows written.

    Uses the admin engine, for the same reason ``app_logging/query_log.py`` does:
    the execution role is SELECT-only and cannot write. Judge verdicts are not
    persisted — ``benchmark_results`` has no column for them. See report.
    """
    from backend.db.connection import AdminSessionLocal

    written = 0
    try:
        async with AdminSessionLocal() as session:
            for outcome in outcomes:
                session.add(
                    BenchmarkResult(
                        run_id=run_id,
                        question=outcome.question,
                        gold_sql=outcome.gold_sql,
                        generated_sql=outcome.generated_sql or "",
                        exact_match=outcome.exact_match,
                        execution_match=outcome.execution_match,
                    )
                )
                written += 1
            await session.commit()
    except Exception as exc:
        logger.error("failed to persist benchmark_results", extra={"persist_error": str(exc)})
        return 0

    return written


def _pct(count: int, total: int) -> float:
    return round(100.0 * count / total, 1) if total else 0.0


def build_summary(run_id: str, outcomes: list[CaseOutcome]) -> EvalSummary:
    """Aggregate outcomes, flagging results that look like scoring bugs."""
    total = len(outcomes)
    exact = sum(1 for o in outcomes if o.exact_match)
    execution = sum(1 for o in outcomes if o.execution_match)
    semantic = sum(1 for o in outcomes if o.exact_match or o.execution_match or o.judge_verdict)
    judged = sum(1 for o in outcomes if o.judge_verdict is not None)
    failures = sum(1 for o in outcomes if o.pipeline_status == "failed")
    ambiguous = sum(1 for o in outcomes if o.pipeline_status == "ambiguous")
    gold_errors = sum(1 for o in outcomes if o.gold_error)

    warnings: list[str] = []

    # A uniform score is far more often a broken harness than a real result.
    if total and execution == 0 and exact == 0:
        warnings.append(
            "0% on BOTH metrics across every case — suspect a scoring or schema "
            "problem before believing the pipeline is this bad"
        )
    if total and exact == total:
        warnings.append(
            "100% exact match across every case — suspect canonicalisation is "
            "collapsing distinct queries, or gold leaked into generation"
        )
    if total and execution == total and exact == 0:
        warnings.append(
            "100% execution match with 0% exact match — plausible, but verify the "
            "result sets are distinctive and not all empty"
        )
    if gold_errors:
        warnings.append(
            f"{gold_errors} gold quer{'y' if gold_errors == 1 else 'ies'} failed to "
            "execute — those cases score against nothing and are not meaningful"
        )
    if ambiguous:
        warnings.append(
            f"{ambiguous} case(s) were short-circuited as ambiguous and scored as "
            "misses; benchmark questions should be answerable"
        )

    return EvalSummary(
        run_id=run_id,
        total_count=total,
        exact_match_pct=_pct(exact, total),
        execution_match_pct=_pct(execution, total),
        semantic_match_pct=_pct(semantic, total),
        judged_count=judged,
        pipeline_failure_count=failures,
        ambiguous_count=ambiguous,
        gold_error_count=gold_errors,
        warnings=warnings,
        cases=[asdict(o) for o in outcomes],
    )


async def run_eval(
    *,
    path: Path = BENCHMARK_PATH,
    use_judge: bool = True,
    persist: bool = True,
    strict_schema: bool = True,
    limit: int | None = None,
) -> EvalSummary:
    """Run the whole benchmark and return the summary."""
    payload = load_benchmark(path)
    cases = payload["cases"]
    if limit is not None:
        cases = cases[:limit]

    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    logger.info("starting eval run", extra={"run_id": run_id, "cases": len(cases)})

    schema = await introspect.get_schema()
    problems = check_schema_assumptions(payload, schema)
    if problems:
        message = "benchmark does not match the live schema:\n  - " + "\n  - ".join(problems)
        if strict_schema:
            raise BenchmarkSchemaMismatch(
                message + "\n\nRefusing to run: every case would score 0% for a reason that has "
                "nothing to do with the pipeline. Fix benchmark_subset.json (or pass "
                "--no-strict-schema to run anyway)."
            )
        logger.warning("running against a mismatched schema: %s", message)

    outcomes = [await run_case(case, use_judge=use_judge) for case in cases]

    summary = build_summary(run_id, outcomes)

    if persist:
        written = await persist_results(run_id, outcomes)
        logger.info("persisted benchmark rows", extra={"written": written, "run_id": run_id})
        if written != len(outcomes):
            summary.warnings.append(
                f"only {written} of {len(outcomes)} benchmark_results rows were written"
            )

    return summary


def print_summary(summary: EvalSummary) -> None:
    """Human-readable report to stdout."""
    print(f"\nrun_id            : {summary.run_id}")
    print(f"total_count       : {summary.total_count}")
    print(f"exact_match_pct   : {summary.exact_match_pct}%")
    print(f"execution_match_pct: {summary.execution_match_pct}%")
    print(f"semantic_match_pct : {summary.semantic_match_pct}% (includes judge rulings)")
    print(f"judged_count      : {summary.judged_count}")
    print(f"pipeline_failures : {summary.pipeline_failure_count}")
    print(f"ambiguous         : {summary.ambiguous_count}")
    print(f"gold_errors       : {summary.gold_error_count}")

    print(f"\n{'case':<32} {'exact':<7} {'exec':<7} {'judge':<7} {'status':<10} attempts")
    for case in summary.cases:
        judge = "-" if case["judge_verdict"] is None else str(case["judge_verdict"])
        print(
            f"{case['case_id']:<32} {str(case['exact_match']):<7} "
            f"{str(case['execution_match']):<7} {judge:<7} "
            f"{case['pipeline_status']:<10} {case['attempts']}"
        )

    if summary.warnings:
        print("\nWARNINGS:")
        for warning in summary.warnings:
            print(f"  ! {warning}")


async def _run_eval_cli(**kwargs) -> EvalSummary:
    """Run the eval and release the shared clients and pools before exit.

    The pipeline borrows the process-wide LLM clients; in the API server the
    lifespan hook closes them, but this CLI has no lifespan, so it must clean
    up itself — inside the same event loop the clients were created on.
    """
    from backend.db.connection import dispose_engines
    from backend.dependencies import close_shared_clients

    try:
        return await run_eval(**kwargs)
    finally:
        await close_shared_clients()
        await dispose_engines()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cistern benchmark.")
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases.")
    parser.add_argument("--no-judge", action="store_true", help="Skip all judge calls.")
    parser.add_argument("--no-persist", action="store_true", help="Do not write to the database.")
    parser.add_argument(
        "--no-strict-schema",
        action="store_true",
        help="Run even if the benchmark does not match the live schema.",
    )
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = parser.parse_args()

    configure_logging()

    summary = asyncio.run(
        _run_eval_cli(
            path=args.benchmark,
            use_judge=not args.no_judge,
            persist=not args.no_persist,
            strict_schema=not args.no_strict_schema,
            limit=args.limit,
        )
    )

    if args.json:
        print(json.dumps(asdict(summary), indent=2, default=str))
    else:
        print_summary(summary)


if __name__ == "__main__":
    main()
