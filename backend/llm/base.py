"""Shared async HTTP transport for every LLM provider.

Both provider modules go through :class:`LLMClient`. Retry policy, timeout
handling, key redaction and response normalisation live here once — the
provider modules only supply a base URL, a key and a model, and only differ in
the prompts they send and the results they parse.

Both Groq and Cerebras expose OpenAI-compatible ``/chat/completions``
endpoints, so a single request/response shape covers both.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.5
DEFAULT_BACKOFF_MAX_SECONDS = 20.0

# Providers may return Retry-After far larger than we are willing to block for.
RETRY_AFTER_CAP_SECONDS = 60.0

_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(RuntimeError):
    """Base class for all LLM transport and parsing failures."""


class LLMHTTPError(LLMError):
    """Provider returned a non-retryable error status."""

    def __init__(self, status_code: int, body: str, provider: str) -> None:
        super().__init__(f"{provider} returned HTTP {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body
        self.provider = provider


class LLMRetryExhausted(LLMError):
    """Retryable failures continued past the retry budget."""


class LLMResponseError(LLMError):
    """Provider returned a 200 whose body did not match the expected shape."""


@dataclass(slots=True)
class LLMResponse:
    """One completed provider call, normalised across providers."""

    text: str
    raw: dict
    latency_ms: int
    model: str
    usage: dict | None = None


@dataclass(slots=True)
class RetryPolicy:
    """Exponential backoff with full jitter, bounded by a retry budget."""

    max_retries: int = DEFAULT_MAX_RETRIES
    base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS
    retry_statuses: set[int] = field(default_factory=lambda: set(_RETRY_STATUS))

    def backoff(self, attempt: int) -> float:
        """Full-jitter backoff for a zero-indexed attempt number."""
        ceiling = min(self.max_seconds, self.base_seconds * (2**attempt))
        return random.uniform(0, ceiling)


def redact_key(api_key: str) -> str:
    """Render a key safe to log: provider prefix and last four characters only."""
    if not api_key:
        return "<unset>"
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def parse_retry_after(headers: httpx.Headers) -> float | None:
    """Read a provider's Retry-After hint, in seconds.

    Checks the non-standard millisecond header first (Groq and Cerebras both
    send it and it is more precise), then integer/float seconds, then an
    HTTP-date. Returns ``None`` when no usable hint is present.
    """
    ms_header = headers.get("retry-after-ms")
    if ms_header is not None:
        try:
            return float(ms_header) / 1000.0
        except (TypeError, ValueError):
            pass

    raw = headers.get("retry-after")
    if raw is None:
        return None

    try:
        return float(raw)
    except (TypeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if retry_at is None:
        return None

    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=_dt.UTC)
    delta = (retry_at - now).total_seconds()
    return delta if delta > 0 else None


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Tolerates markdown fences and leading prose, which models emit even under
    JSON mode. Raises :class:`LLMResponseError` if nothing parses.
    """
    candidate = text.strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = _JSON_FENCE_RE.search(candidate)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(candidate[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise LLMResponseError(f"no JSON object found in model output: {text[:300]!r}")


class LLMClient:
    """Async OpenAI-compatible chat client with retry and timeout handling."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        chat_path: str,
        default_model: str,
        timeout_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.chat_path = chat_path
        self.default_model = default_model
        self._api_key = api_key
        # NOTE: config exposes only QUERY_TIMEOUT_SECONDS today. A dedicated
        # LLM_REQUEST_TIMEOUT_SECONDS belongs in config.py — see report.
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(settings.QUERY_TIMEOUT_SECONDS)
        )
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _require_key(self) -> None:
        if not self._api_key:
            raise LLMError(f"{self.provider} API key is not configured; set it in the environment")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request, retrying transient failures."""
        self._require_key()

        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if extra_body:
            payload.update(extra_body)

        started = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(self.retry_policy.max_retries + 1):
            try:
                response = await self.client.post(
                    self.chat_path, json=payload, headers=self._headers()
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.retry_policy.max_retries:
                    break
                await self._sleep_before_retry(attempt, None, repr(exc))
                continue

            if response.status_code in self.retry_policy.retry_statuses:
                last_error = LLMHTTPError(response.status_code, response.text, self.provider)
                if attempt >= self.retry_policy.max_retries:
                    break
                await self._sleep_before_retry(
                    attempt, parse_retry_after(response.headers), f"HTTP {response.status_code}"
                )
                continue

            if response.status_code >= 400:
                # 401/403/404/422 and friends: retrying cannot help.
                raise LLMHTTPError(response.status_code, response.text, self.provider)

            latency_ms = int((time.perf_counter() - started) * 1000)
            return self._parse(response, latency_ms, payload)

        raise LLMRetryExhausted(
            f"{self.provider} failed after {self.retry_policy.max_retries + 1} attempts "
            f"(key {redact_key(self._api_key)}): {last_error!r}"
        ) from last_error

    async def _sleep_before_retry(
        self, attempt: int, retry_after: float | None, reason: str
    ) -> None:
        if retry_after is not None:
            delay = min(retry_after, RETRY_AFTER_CAP_SECONDS)
        else:
            delay = self.retry_policy.backoff(attempt)
        logger.warning(
            "%s retry %d/%d in %.2fs (%s)",
            self.provider,
            attempt + 1,
            self.retry_policy.max_retries,
            delay,
            reason,
        )
        await asyncio.sleep(delay)

    def _parse(
        self, response: httpx.Response, latency_ms: int, payload: dict[str, Any]
    ) -> LLMResponse:
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError(
                f"{self.provider} returned non-JSON body: {response.text[:300]!r}"
            ) from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"{self.provider} response missing choices[0].message.content: {body!r}"
            ) from exc

        if content is None:
            raise LLMResponseError(f"{self.provider} returned null message content")

        return LLMResponse(
            text=content,
            raw=body,
            latency_ms=latency_ms,
            model=str(body.get("model") or payload.get("model") or "unknown"),
            usage=body.get("usage"),
        )


class FallbackLLMClient:
    """A primary provider, then a second provider serving the same model family.

    Exists for quota and outage failures — a 402 from an exhausted free tier, a
    429 that outlasted the retry budget, a provider that is simply down. Any
    ``LLMError`` from the primary's transport sends the identical request to the
    fallback. Parse failures in the caller (``extract_json`` on a 200) are not
    transport failures and never reach here.

    The fallback must serve the *same model family* as the primary, not merely
    any working model: the correction and judge tiers are deliberately a
    different family from generation, and a fallback that quietly swapped in
    the generator's family would have it grading its own output.

    Both clients are borrowed. Whoever created them closes them.
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def provider(self) -> str:
        return self.primary.provider

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        try:
            return await self.primary.chat(messages, model=model, **kwargs)
        except LLMError as exc:
            logger.warning(
                "%s failed, falling back to %s: %s",
                self.primary.provider,
                self.fallback.provider,
                exc,
            )
        # A model name chosen for the primary means nothing to the fallback's
        # catalogue; it always uses its own configured default.
        return await self.fallback.chat(messages, **kwargs)

    async def aclose(self) -> None:
        """No-op: both clients are borrowed, never owned."""
