"""Probe memoisation on GET /health.

The endpoint's four probes are metered: two authenticated provider calls and
two database connections per refresh. A dashboard polling every few seconds
must not translate one-to-one into that traffic, so the result is cached. These
tests pin the properties that make the cache worth having — and the ones that
stop it from hiding an outage.
"""

import asyncio

import pytest

from backend.config import settings
from backend.routes import health as health_route


@pytest.fixture(autouse=True)
def _clean_cache():
    """No test may inherit another's memoised report."""
    health_route._reset_cache()
    yield
    health_route._reset_cache()


def _report(status: str = "ok") -> dict:
    return {"status": status, "env": "test", "checks": {}}


@pytest.fixture
def counting_probe(monkeypatch):
    """Replace the real probes with a counter, so calls can be asserted on."""
    calls = {"n": 0}
    status = {"value": "ok"}

    async def fake_run_probes() -> dict:
        calls["n"] += 1
        return _report(status["value"])

    monkeypatch.setattr(health_route, "_run_probes", fake_run_probes)
    return calls, status


async def test_second_call_within_ttl_does_not_reprobe(counting_probe, monkeypatch):
    calls, _ = counting_probe
    monkeypatch.setattr(settings, "HEALTH_CACHE_SECONDS", 60)

    first = await health_route.health(refresh=False)
    second = await health_route.health(refresh=False)

    assert calls["n"] == 1, "the cached report should have been reused"
    assert first == second


async def test_cache_expiry_triggers_a_fresh_probe(counting_probe, monkeypatch):
    calls, _ = counting_probe
    # A zero TTL means every call is a miss, which is the boundary worth
    # pinning: an off-by-one here would serve a stale report forever.
    monkeypatch.setattr(settings, "HEALTH_CACHE_SECONDS", 0)

    await health_route.health(refresh=False)
    await health_route.health(refresh=False)

    assert calls["n"] == 2


async def test_refresh_parameter_bypasses_the_cache(counting_probe, monkeypatch):
    calls, _ = counting_probe
    monkeypatch.setattr(settings, "HEALTH_CACHE_SECONDS", 3600)

    await health_route.health(refresh=False)
    await health_route.health(refresh=True)

    assert calls["n"] == 2


async def test_degraded_result_is_held_far_more_briefly(counting_probe, monkeypatch):
    """An outage cached for a full minute would make recovery look slow. The
    saving this cache exists for comes from the healthy steady state."""
    calls, status = counting_probe
    monkeypatch.setattr(settings, "HEALTH_CACHE_SECONDS", 3600)
    monkeypatch.setattr(health_route, "_DEGRADED_CACHE_SECONDS", 0)

    status["value"] = "degraded"
    await health_route.health(refresh=False)
    await health_route.health(refresh=False)

    assert calls["n"] == 2, "a degraded report must not inherit the healthy TTL"


async def test_degraded_ttl_never_exceeds_the_configured_one(counting_probe, monkeypatch):
    """Lowering HEALTH_CACHE_SECONDS below the degraded floor must still take
    effect — the floor is a ceiling on staleness, not a minimum."""
    calls, status = counting_probe
    monkeypatch.setattr(settings, "HEALTH_CACHE_SECONDS", 0)
    monkeypatch.setattr(health_route, "_DEGRADED_CACHE_SECONDS", 3600)

    status["value"] = "degraded"
    await health_route.health(refresh=False)
    await health_route.health(refresh=False)

    assert calls["n"] == 2


async def test_concurrent_callers_share_one_probe(monkeypatch):
    """The reason for the lock.

    Without it, every request arriving after the TTL lapses starts its own set
    of probes — so several open tabs turn each cache turnover into a burst of
    provider calls, which is the exact cost this cache exists to remove.
    """
    calls = {"n": 0}

    async def slow_probes() -> dict:
        calls["n"] += 1
        # Long enough that the other callers are certain to arrive mid-flight.
        await asyncio.sleep(0.05)
        return _report()

    monkeypatch.setattr(health_route, "_run_probes", slow_probes)
    monkeypatch.setattr(settings, "HEALTH_CACHE_SECONDS", 60)

    results = await asyncio.gather(*(health_route.health(refresh=False) for _ in range(8)))

    assert calls["n"] == 1
    assert all(r == results[0] for r in results)
