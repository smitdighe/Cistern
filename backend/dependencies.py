"""Shared, process-lifetime resources and their FastAPI providers.

The LLM clients here exist because the alternative is a fresh
``httpx.AsyncClient`` — and therefore a fresh TCP connection and TLS handshake
— on every generation, correction, explanation and ambiguity check. At three
to five provider calls per question that is most of the latency budget spent
on setup.

Ownership rule: whoever creates an ``LLMClient`` closes it. These singletons
are created lazily and closed once by ``close_shared_clients()`` at
application shutdown. Because callers pass them in explicitly, the provider
functions in ``llm/`` treat them as borrowed and never close them — see the
``owns_client`` branch in ``groq_client``/``cerebras_client``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, settings
from backend.db.connection import get_admin_session, get_execution_session
from backend.llm.base import FallbackLLMClient, LLMClient
from backend.llm.cerebras_client import (
    CEREBRAS_BASE_URL,
    CEREBRAS_CHAT_PATH,
    CEREBRAS_DEFAULT_MODEL,
)
from backend.llm.groq_client import GROQ_BASE_URL, GROQ_CHAT_PATH, GROQ_DEFAULT_MODEL

logger = logging.getLogger(__name__)

_groq_client: LLMClient | None = None
_cerebras_client: LLMClient | None = None
_correction_fallback_client: LLMClient | None = None


def shared_groq_client() -> LLMClient:
    """Process-wide Groq client. Generation tier and ambiguity detection."""
    global _groq_client
    if _groq_client is None:
        _groq_client = LLMClient(
            provider="groq",
            base_url=GROQ_BASE_URL,
            chat_path=GROQ_CHAT_PATH,
            api_key=settings.GROQ_API_KEY,
            default_model=GROQ_DEFAULT_MODEL,
        )
    return _groq_client


def shared_cerebras_client() -> LLMClient:
    """Process-wide Cerebras client. Correction, explanation and judge tiers."""
    global _cerebras_client
    if _cerebras_client is None:
        _cerebras_client = LLMClient(
            provider="cerebras",
            base_url=CEREBRAS_BASE_URL,
            chat_path=CEREBRAS_CHAT_PATH,
            api_key=settings.CEREBRAS_API_KEY,
            default_model=CEREBRAS_DEFAULT_MODEL,
        )
    return _cerebras_client


def shared_correction_client() -> FallbackLLMClient:
    """Correction, explanation and judge tiers: Cerebras, then Groq.

    The fallback serves Cerebras' model family on Groq's infrastructure, so a
    Cerebras quota or outage degrades to a different provider rather than to a
    different model. /health keeps probing ``shared_cerebras_client`` directly,
    so it still reports Cerebras' own state.
    """
    global _correction_fallback_client
    if _correction_fallback_client is None:
        _correction_fallback_client = LLMClient(
            provider="groq-fallback",
            base_url=GROQ_BASE_URL,
            chat_path=GROQ_CHAT_PATH,
            api_key=settings.GROQ_API_KEY,
            default_model=settings.CORRECTION_FALLBACK_MODEL,
        )
    return FallbackLLMClient(shared_cerebras_client(), _correction_fallback_client)


async def close_shared_clients() -> None:
    """Release every pool. Called once on application shutdown."""
    global _groq_client, _cerebras_client, _correction_fallback_client
    for client in (_groq_client, _cerebras_client, _correction_fallback_client):
        if client is None:
            continue
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - shutdown must not raise
            logger.warning("failed to close %s client cleanly", client.provider)
    _groq_client = None
    _cerebras_client = None
    _correction_fallback_client = None


# --- FastAPI providers ------------------------------------------------------


def get_settings() -> Settings:
    """Application settings."""
    return settings


def get_groq_client() -> LLMClient:
    """Injectable Groq client."""
    return shared_groq_client()


def get_cerebras_client() -> LLMClient:
    """Injectable Cerebras client."""
    return shared_cerebras_client()


async def execution_session() -> AsyncGenerator[AsyncSession, None]:
    """Injectable read-only session. Query traffic only."""
    async for session in get_execution_session():
        yield session


async def admin_session() -> AsyncGenerator[AsyncSession, None]:
    """Injectable admin session. Introspection only, never query traffic."""
    async for session in get_admin_session():
        yield session
