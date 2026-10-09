"""GET /schema — the introspected schema the generator works from."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from backend.db import introspect
from backend.schemas.schema import SchemaResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schema"])


@router.get("/schema", response_model=SchemaResponse)
async def get_schema(
    refresh: bool = Query(
        default=False,
        description="Re-query the database instead of serving the cached schema.",
    ),
) -> SchemaResponse:
    """Return the current database schema.

    Served from the in-process cache populated at startup. Pass
    ``?refresh=true`` after a migration to force a re-read.

    Returns 503 if the schema cannot be read. Unlike /health, this endpoint has
    nothing useful to say when the admin connection is down, and a bare
    traceback-driven 500 tells the caller less than an explicit unavailable.
    """
    try:
        schema = await introspect.refresh_schema() if refresh else await introspect.get_schema()
    except Exception as exc:
        logger.warning("schema introspection failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"schema introspection failed: {exc}",
        ) from exc

    return SchemaResponse.from_introspection(schema)
