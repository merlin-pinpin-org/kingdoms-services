"""Unit tests for the profile stats cache (Redis TTL + Mongo + provider)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.models.game import PlayerStats, StatsBlock, StatsEntry
from kingdoms.core.services.profile_stats import PROFILE_STATS_COLLECTION, ProfileStatsService, stats_entry


class FakeRedis:
    """In-memory redis-py stand-in (get/set with ex)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0) -> None:
        self.store[key] = value


class FakeCollection:
    """In-memory Mongo collection stand-in (find_one/replace_one)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.writes = 0

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return self.docs.get(str(query.get("_id", "")))

    async def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        self.docs[str(query.get("_id", ""))] = doc
        self.writes += 1


class FakeDb:
    """database[collection] stand-in."""

    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())


class FakeProvider:
    """Provider seam returning canned PlayerStats blocks."""

    def __init__(self, stats: PlayerStats | None) -> None:
        self._stats = stats
        self.calls = 0

    async def get_player_stats(self, profile_id: str) -> PlayerStats | None:
        self.calls += 1
        return self._stats


def _stats() -> PlayerStats:
    return PlayerStats(
        profile_id="123",
        blocks=(
            StatsBlock(
                name="rm_1v1",
                entries=(
                    StatsEntry(key="rating", value="1650"),
                    StatsEntry(key="wins", value="42"),
                    StatsEntry(key="losses", value="17"),
                ),
            ),
        ),
    )


async def test_cache_writes_redis_and_mongo_on_first_fetch() -> None:
    redis = FakeRedis()
    db = FakeDb()
    provider = FakeProvider(_stats())
    service = ProfileStatsService(provider, db, redis)

    stats = await service.get("123")

    assert stats is not None
    assert stats_entry(stats, "rm_1v1", "rating") == "1650"
    assert provider.calls == 1
    assert "stats:aoe2:123" in redis.store
    assert PROFILE_STATS_COLLECTION in db._collections
    assert db[PROFILE_STATS_COLLECTION].writes == 1


async def test_redis_hit_skips_provider_and_mongo() -> None:
    redis = FakeRedis()
    db = FakeDb()
    provider = FakeProvider(_stats())
    service = ProfileStatsService(provider, db, redis)
    await service.get("123")

    await service.get("123")

    assert provider.calls == 1
    assert db[PROFILE_STATS_COLLECTION].writes == 1


async def test_provider_none_serves_stale_mongo_copy() -> None:
    redis = FakeRedis()
    db = FakeDb()
    provider = FakeProvider(None)
    stale = {"profile_id": "123", "blocks": [], "fetched_at": 0}
    db[PROFILE_STATS_COLLECTION].docs["aoe2:123"] = stale
    service = ProfileStatsService(provider, db, redis)

    stats = await service.get("123")

    assert stats == stale


async def test_no_redis_degrades_to_mongo() -> None:
    db = FakeDb()
    provider = FakeProvider(_stats())
    service = ProfileStatsService(provider, db, None)

    stats = await service.get("123")

    assert stats is not None
    assert stats_entry(stats, "rm_1v1", "wins") == "42"


def test_stats_entry_missing_returns_empty() -> None:
    assert stats_entry(None, "rm_1v1", "rating") == ""
    assert stats_entry({"blocks": []}, "rm_1v1", "rating") == ""
