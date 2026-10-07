"""Provider-backed profile stats with a Redis-TTL + Mongo cache (kingdoms #147).

The dashboard needs each bound profile's name and stats (rating, W/L).
The source of truth is the provider (ext-librematch leaderboards); calling
it per render is both slow and rate-limited, so the values are cached:

- Redis first (``stats:<game_key>:<profile_id>``, TTL, the hot path);
- then Mongo (``profile_stats`` collection, the persistent copy a Redis
  flush survives);
- then the provider (a fetch refreshes both layers).

Every layer degrades: no provider → the stale Mongo copy; no Mongo →
the last Redis value until it expires; nothing → no stats line.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.profile_stats")

PROFILE_STATS_COLLECTION = "profile_stats"
DEFAULT_TTL_S = 600

GAME_KEY = "aoe2"


class StatsProvider(Protocol):
    """The provider seam (GameProviderClient.get_player_stats)."""

    async def get_player_stats(self, profile_id: str) -> Any | None:
        """Fetch a profile's stats blocks; None when the provider has none."""
        ...


class ProfileStatsService:
    """Two-layer cached provider stats, shared by the Discord surfaces."""

    def __init__(
        self,
        provider: StatsProvider,
        database: Any,
        redis: Any = None,
        game_key: str = GAME_KEY,
        ttl_s: int = DEFAULT_TTL_S,
    ) -> None:
        self._provider = provider
        self._db = database
        self._redis = redis
        self._game_key = game_key
        self._ttl_s = ttl_s

    def _redis_key(self, profile_id: str) -> str:
        return f"stats:{self._game_key}:{profile_id}"

    async def get(self, profile_id: str) -> dict[str, Any] | None:
        """Return the profile's cached stats, refreshing them when stale.

        The answer is a plain dict (``blocks``: list of named entries) so
        the Discord layer never imports provider models.
        """
        cached = await self._redis_get(profile_id)
        if cached is not None:
            return cached
        stored = await self._mongo_get(profile_id)
        if stored is not None and not self._is_stale(stored):
            await self._redis_set(profile_id, stored)
            return stored
        fetched = await self._fetch(profile_id)
        if fetched is not None:
            await self._store(profile_id, fetched)
            return fetched
        return stored

    async def get_many(self, profile_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Resolve many profiles at once; missing ones stay absent."""
        results: dict[str, dict[str, Any]] = {}
        for profile_id in profile_ids:
            stats = await self.get(profile_id)
            if stats is not None:
                results[profile_id] = stats
        return results

    async def _fetch(self, profile_id: str) -> dict[str, Any] | None:
        try:
            stats = await self._provider.get_player_stats(profile_id)
        except Exception:
            logger.warning("provider stats fetch failed (%s) — degrading", profile_id, exc_info=True)
            return None
        if stats is None:
            return None
        return {
            "profile_id": profile_id,
            "blocks": [
                {
                    "name": block.name,
                    "entries": [{"key": e.key, "value": e.value} for e in block.entries],
                }
                for block in stats.blocks
            ],
            "fetched_at": _now_s(),
        }

    async def _store(self, profile_id: str, stats: dict[str, Any]) -> None:
        await self._mongo_set(profile_id, stats)
        await self._redis_set(profile_id, stats)

    async def _redis_get(self, profile_id: str) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._redis_key(profile_id))
        except Exception:
            logger.warning("redis stats read failed — degrading to mongo", exc_info=True)
            return None
        return json.loads(raw) if raw else None

    async def _redis_set(self, profile_id: str, stats: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(self._redis_key(profile_id), json.dumps(stats), ex=self._ttl_s)
        except Exception:
            logger.warning("redis stats write failed — degrading", exc_info=True)

    async def _mongo_get(self, profile_id: str) -> dict[str, Any] | None:
        try:
            return await self._db[PROFILE_STATS_COLLECTION].find_one({"_id": f"{self._game_key}:{profile_id}"})
        except Exception:
            logger.warning("mongo stats read failed — degrading", exc_info=True)
            return None

    async def _mongo_set(self, profile_id: str, stats: dict[str, Any]) -> None:
        try:
            await self._db[PROFILE_STATS_COLLECTION].replace_one(
                {"_id": f"{self._game_key}:{profile_id}"},
                {**stats, "_id": f"{self._game_key}:{profile_id}"},
                upsert=True,
            )
        except Exception:
            logger.warning("mongo stats write failed — degrading", exc_info=True)

    @staticmethod
    def _is_stale(stats: dict[str, Any]) -> bool:
        fetched_at = stats.get("fetched_at")
        if not fetched_at:
            return True
        return (fetched_at + DEFAULT_TTL_S) < _now_s()


def _now_s() -> int:
    import time

    return int(time.time())


def stats_entry(stats: dict[str, Any] | None, block_name: str, key: str) -> str:
    """Read one stats value out of a cached profile stats dict."""
    if not stats:
        return ""
    for block in stats.get("blocks", []):
        if block.get("name") != block_name:
            continue
        for entry in block.get("entries", []):
            if entry.get("key") == key:
                return str(entry.get("value", ""))
    return ""
