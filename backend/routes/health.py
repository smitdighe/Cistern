"""GET /health — per-dependency reachability.

Four checks, reported independently: Groq, Cerebras, and Neon under *each* of
the two roles. The roles are probed separately on purpose — they are distinct
Postgres users with different grants, and a credential or permission problem
on one says nothing about the other. Collapsing them into one "database: up"
would hide exactly the failure most likely to occur.

The endpoint always returns 200. A monitor needs to read which dependency is
down; a 500 that says "something is wrong" makes that harder, not easier. The
aggregate verdict is in ``status``.

Provider probes use ``GET /models`` — it authenticates the key and touches the
real API without spending a token or invoking a model.

Probe results are memoised (see ``HEALTH_CACHE_SECONDS``). The request itself
is never served from a cache — only the downstream probes are — so a caller
still learns on every poll whether *this* service is reachable, which is the
part a health check exists to answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Query
from sqlalchemy import text

from backend.config import settings
from backend.db.connection import AdminSessionLocal, ExecutionSessionLocal
from backend.dependencies import shared_cerebras_client, shared_groq_client
from backend.llm.base import LLMClient
from backend.llm.cerebras_client import CEREBRAS_BASE_URL
from backend.llm.groq_client import GROQ_BASE_URL

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

GROQ_MODELS_PATH = "/openai/v1/models"
CEREBRAS_MODELS_PATH = "/v1/models"

_PROBE_TIMEOUT_SECONDS = 5.0

STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_UNCONFIGURED = "unconfigured"

# A degraded reading is held for much less time than a healthy one. Caching an
# outage for a full minute would make recovery look slow for no benefit — the
# saving this cache exists for comes from the steady state, which is healthy.
# Never longer than the configured TTL, so lowering that stays meaningful.
_DEGRADED_CACHE_SECONDS = 10

# Memoised probe result, guarded by ``_probe_lock``.
_cached_report: dict[str, Any] | None = None
_cache_expires_at: float = 0.0

# Serialises refreshes. Without it, every request arriving after the TTL lapses
# starts its own set of probes — under polling from several open tabs that is a
# burst of provider calls each time the cache turns over, which is the exact
# thing being avoided.
_probe_lock = asyncio.Lock()


def _check(name: str, status: str, latency_ms: int, detail: str | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "latency_ms": latency_ms, "detail": detail}


async def _probe_provider(
    name: str, client: LLMClient, base_url: str, models_path: str, api_key: str
) -> dict[str, Any]:
    """Cheapest call that proves network reachability and a valid key."""
    if not api_key:
        return _check(name, STATUS_UNCONFIGURED, 0, "API key is not set")

    started = time.perf_counter()
    try:
        # Reuses the shared connection pool. The Authorization header is built
        # here rather than reaching into the client's private header helper.
        response = await client.client.get(
            models_path,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(_PROBE_TIMEOUT_SECONDS),
        )
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return _check(name, STATUS_DOWN, elapsed, f"{type(exc).__name__}: {exc}")

    elapsed = int((time.perf_counter() - started) * 1000)

    if response.status_code == 200:
        return _check(name, STATUS_UP, elapsed)
    if response.status_code in (401, 403):
        return _check(name, STATUS_DOWN, elapsed, "API key rejected")
    return _check(name, STATUS_DOWN, elapsed, f"HTTP {response.status_code}")


async def _probe_database(name: str, session_factory, role: str) -> dict[str, Any]:
    """SELECT 1 on one specific role's engine."""
    started = time.perf_counter()
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return _check(name, STATUS_DOWN, elapsed, f"{type(exc).__name__}: {exc}")

    elapsed = int((time.perf_counter() - started) * 1000)
    return _check(name, STATUS_UP, elapsed, f"role: {role}")


async def _run_probes() -> dict[str, Any]:
    """Probe every dependency once, concurrently. No caching."""
    checks = await asyncio.gather(
        _probe_provider(
            "groq",
            shared_groq_client(),
            GROQ_BASE_URL,
            GROQ_MODELS_PATH,
            settings.GROQ_API_KEY,
        ),
        _probe_provider(
            "cerebras",
            shared_cerebras_client(),
            CEREBRAS_BASE_URL,
            CEREBRAS_MODELS_PATH,
            settings.CEREBRAS_API_KEY,
        ),
        _probe_database("neon_execution", ExecutionSessionLocal, "read-only"),
        _probe_database("neon_admin", AdminSessionLocal, "admin"),
        return_exceptions=False,
    )

    healthy = all(check["status"] == STATUS_UP for check in checks)

    return {
        "status": "ok" if healthy else "degraded",
        "env": settings.ENV,
        "checks": {check["name"]: check for check in checks},
    }


def _reset_cache() -> None:
    """Drop the memoised report. For tests and for lifespan teardown."""
    global _cached_report, _cache_expires_at
    _cached_report = None
    _cache_expires_at = 0.0


@router.get("/health")
async def health(
    refresh: bool = Query(
        default=False,
        description="Bypass the cached probe result and re-probe every dependency.",
    ),
) -> dict[str, Any]:
    """Report the state of every external dependency.

    Always 200. ``status`` is "ok" only when all four checks are up;
    "degraded" otherwise.

    The four probes behind this are not free — two of them are authenticated
    calls to metered provider APIs, and the other two hold a database
    connection open, which is enough to stop a serverless Postgres from ever
    autosuspending. A dashboard polling every few seconds therefore cannot be
    allowed to translate one-to-one into upstream traffic: a single browser tab
    left open overnight would spend thousands of provider calls reporting that
    nothing had changed.

    So the *result* is memoised for ``HEALTH_CACHE_SECONDS`` and the cost of
    reporting health becomes a function of wall-clock time rather than of how
    many clients are watching or how fast they poll. The freshness given up is
    bounded by that TTL, and is the right trade for a status indicator.
    """
    global _cached_report, _cache_expires_at

    now = time.monotonic()
    if not refresh and _cached_report is not None and now < _cache_expires_at:
        return _cached_report

    async with _probe_lock:
        # Re-check under the lock: while waiting, whoever held it may already
        # have refreshed, and probing again would defeat the point of queueing.
        now = time.monotonic()
        if not refresh and _cached_report is not None and now < _cache_expires_at:
            return _cached_report

        report = await _run_probes()

        ttl = settings.HEALTH_CACHE_SECONDS
        if report["status"] != "ok":
            ttl = min(ttl, _DEGRADED_CACHE_SECONDS)

        _cached_report = report
        _cache_expires_at = time.monotonic() + ttl

    return report
