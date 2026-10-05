"""Unit tests for the provider data cache (kingdoms-services#205).

Freshness rules: completed matches fetched once ever (atomic marker),
live match details 30s, profiles 5 minutes, daily sweep staleness.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.provider_cache import ProviderDataCache
from kingdoms.core.services.state import IStateStore


class FakeStore(IStateStore):
    """In-memory state store honoring only_if_absent and TTLs."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[str, int | None]] = {}
        self.now = 10_000

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        entry = self.data.get(key)
        return entry[0] if entry else None

    async def set(self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False) -> bool:
        if only_if_absent and key in self.data:
            return False
        self.data[key] = (value, ttl)
        return True

    async def delete(self, key: str) -> bool:
        return self.data.pop(key, None) is not None

    async def increment(self, key: str, window: int | None = None) -> int:
        return 1

    async def compare_delete(self, key: str, expected: str) -> bool:
        return False

    async def publish(self, channel: str, message: str) -> int:
        return 0

    async def subscribe(self, channel: str, callback: Any) -> None:
        return None

    async def unsubscribe(self, channel: str, callback: Any) -> None:
        return None


def _cache() -> tuple[ProviderDataCache, FakeStore]:
    from kingdoms.core.services.state import StateService

    store = FakeStore()
    return ProviderDataCache(StateService(store=store)), store


async def test_live_match_details_round_trip() -> None:
    cache, _ = _cache()
    decision = await cache.get_match_details("aoe2", "aoe2lobby", "m1")
    assert decision.should_fetch
    await cache.store_match_details("aoe2", "aoe2lobby", "m1", {"map": "Arabia"})
    decision = await cache.get_match_details("aoe2", "aoe2lobby", "m1")
    assert not decision.should_fetch
    assert decision.value == {"map": "Arabia"}


async def test_completed_match_fetched_once_ever() -> None:
    cache, _ = _cache()
    assert await cache.should_fetch_completed("aoe2", "librematch", "m1")
    assert not await cache.should_fetch_completed("aoe2", "librematch", "m1")
    assert not await cache.should_fetch_completed("aoe2", "librematch", "m1")


async def test_profile_five_minute_freshness() -> None:
    cache, store = _cache()
    decision = await cache.get_profile("aoe2", "librematch", "p1")
    assert decision.should_fetch
    await cache.store_profile("aoe2", "librematch", "p1", {"rating": 1200})
    decision = await cache.get_profile("aoe2", "librematch", "p1")
    assert not decision.should_fetch
    key = next(k for k in store.data if k.endswith("p1") and "profile" in k)
    assert store.data[key][1] == 300


async def test_daily_sweep_marks_stale_profiles() -> None:
    cache, _ = _cache()
    stale = await cache.stale_registered_profiles("aoe2", "librematch", ["a", "b"], now_s=90_000)
    assert stale == ["a", "b"]
    await cache.mark_profile_refreshed("aoe2", "librematch", "a", now_s=90_000)
    stale = await cache.stale_registered_profiles("aoe2", "librematch", ["a", "b"], now_s=90_000)
    assert stale == ["b"]


async def test_extraction_round_trip() -> None:
    cache, _ = _cache()
    extracted = {"map": "Frigid Lake", "civs": ["incas", "dravidians"], "duration": 1077}
    await cache.record_match_extraction("aoe2", "m245", extracted)
    assert await cache.get_match_extraction("aoe2", "m245") == extracted
    assert cache.dump_extraction_json(extracted)
