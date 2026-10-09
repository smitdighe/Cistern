"""Request/response models for POST /query."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """A natural language question to answer against the database."""

    question: str = Field(
        min_length=1,
        max_length=2000,
        description="Natural language question to translate into SQL and execute.",
    )


class QueryResponse(BaseModel):
    """The pipeline's answer, a clarifying question, or an error.

    ``status`` is the discriminator — one of "success", "ambiguous", "failed":

    * "success" — ``sql`` and ``result`` populated
    * "ambiguous" — ``clarifying_question`` populated, ``error`` null. Not a
      failure; the system is asking, not broken.
    * "failed" — ``error`` set

    ``success`` remains as the boolean shorthand for ``status == "success"``.
    """

    status: str = Field(
        default="failed",
        description='Terminal state of the request: "success", "ambiguous" or "failed".',
    )

    sql: str | None = Field(
        default=None, description="The SQL that produced the result, if any ran."
    )
    result: list[dict[str, Any]] | None = Field(
        default=None, description="Result rows, or null if nothing executed."
    )
    explanation: str | None = Field(
        default=None, description="Plain-language description of what the query did."
    )
    safety_flags: list[str] = Field(
        default_factory=list,
        description="Validator and pipeline annotations for this request.",
    )
    attempts: int = Field(
        default=0, description="SQL-producing attempts: generation plus corrections."
    )
    clarifying_question: str | None = Field(
        default=None,
        description="Populated when the question was too ambiguous to answer.",
    )
    success: bool = False
    error: str | None = None
