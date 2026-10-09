"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app_logging.setup import configure_logging
from backend.config import settings
from backend.db import introspect
from backend.db.connection import dispose_engines
from backend.dependencies import close_shared_clients
from backend.routes import health, query, schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the schema cache at boot; release pools at shutdown.

    A failed introspection is logged, not fatal. Refusing to start would take
    /health down with it, and /health reporting "neon_admin: down" is far more
    useful to whoever is paged than a process that exited. The first /query or
    /schema will retry the introspection anyway.
    """
    configure_logging()

    try:
        loaded = await introspect.refresh_schema()
        logger.info("schema cache warmed: %d tables", len(loaded))
    except Exception as exc:
        logger.warning("startup schema introspection failed, continuing: %s", exc)

    try:
        yield
    finally:
        await close_shared_clients()
        await dispose_engines()


def create_app() -> FastAPI:
    """Build the application and mount every router."""
    app = FastAPI(
        title="Cistern",
        version="0.1.0",
        description="Natural language questions, answered as SQL against Postgres.",
        lifespan=lifespan,
    )

    # The frontend is served from a different origin than the API (Vite dev
    # server in development, a separate static host in production), so every
    # browser call is cross-origin. Origins are configured, never "*" — the API
    # is expected to carry an X-API-Key.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    app.include_router(health.router)
    app.include_router(schema.router)
    app.include_router(query.router)

    return app


app = create_app()
