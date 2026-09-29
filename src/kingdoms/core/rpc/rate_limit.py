"""Redis-backed rate limiting for game provider calls (reference 2.4).

Every external API call from a provider adapter goes through this
throttle: Redis counters under ``game:{game_key}:rate``. The limit is
per provider process and degrades fail-open when Redis is unavailable
— a throttle outage must never take the provider down.
"""

from __future__ import annotations

import time

import redis.asyncio as redis


class ProviderRateLimiter:
    """Fixed-window rate limiter backed by a Redis counter.

    The window key is ``game:{game_key}:rate:{window_start}`` so
    concurrent processes share the same budget; each key expires one
    window after its start.
    """

    def __init__(self, client: redis.Redis, game_key: str, max_calls: int, window_s: int) -> None:
        self._client = client
        self._game_key = game_key
        self._max_calls = max_calls
        self._window_s = window_s

    async def check(self) -> bool:
        """Consume one call slot, returning False when over budget.

        Fail-open on Redis errors: a throttle outage must never block
        the provider (the external API will enforce its own limit).
        """
        window_start = int(time.time() // self._window_s)
        key = f"game:{self._game_key}:rate:{window_start}"
        try:
            count = await self._client.incr(key)
            if count == 1:
                await self._client.expire(key, self._window_s)
        except redis.RedisError:
            return True
        return count <= self._max_calls

    async def wait_next_window(self) -> int:
        """Return the epoch seconds until the next window opens."""
        now = time.time()
        return int(self._window_s - (now % self._window_s)) + 1
