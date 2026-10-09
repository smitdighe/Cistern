"""POST /query — thin adapter over the orchestration pipeline.

No business logic lives here. This file parses a request, calls
``run_pipeline``, and maps the result onto the wire model. Any decision worth
testing belongs upstream in orchestration, safety or the LLM tiers.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.orchestration.pipeline import PipelineResult, run_pipeline
from backend.schemas.query import QueryRequest, QueryResponse

router = APIRouter(tags=["query"])


def _to_response(result: PipelineResult) -> QueryResponse:
    """Map the pipeline's internal result onto the public shape.

    ``attempt_trail`` is deliberately not exposed: it carries raw provider
    errors and rejected SQL, which belong in logs (phase 7), not in a client
    response.
    """
    return QueryResponse(
        status=result.status,
        sql=result.sql,
        result=result.result,
        explanation=result.explanation,
        safety_flags=result.safety_flags,
        attempts=result.attempts,
        clarifying_question=result.ambiguity_question,
        success=result.success,
        error=result.error,
    )


@router.post("/query", response_model=QueryResponse)
async def post_query(request: QueryRequest) -> QueryResponse:
    """Answer a natural language question against the database.

    Always returns 200. A failed or ambiguous request is described in the
    payload rather than raised as an HTTP error — the caller needs the
    clarifying question or the error text, and both are data.
    """
    result = await run_pipeline(request.question)
    return _to_response(result)
