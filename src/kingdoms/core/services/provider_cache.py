"""Provider data cache: freshness rules between the bot and providers (#205).

Match data is expensive and mostly immutable: a completed match never
changes, a player's stats barely move in five minutes. This module is
the cache-aside layer every provider lookup goes through — backed by
the StateService (Redis), the single entry point to hot state.

Rules (kingdoms-services#205):

- **completed match**: fetched at most once, ever — the marker key is
  set atomically (``SET NX``); a second fetch is a no-op;
- **match details** (live, before/during): short TTL, overwritten by
  each provider answer;
- **player profile**: at most one fetch per 5 minutes (TTL on the
  cached answer);
- **registered players**: refreshed at least once a day by the daily
  sweep, which re-queues stale profiles.

Keys live under ``provider_cache:{game}:{kind}:{provider}:{id}`` and
the once-markers under ``provider_fetched:{game}:match:{provider}:{id}``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kingdoms.core.services.state import StateService

MATCH_TTL_S = 5
PROFILE_TTL_S = 300
SWEEP_PROFILE_STALE_S = 86_400

_SCOPE = "provider_cache"
_MARKER_SCOPE = "provider_fetched"


@dataclass(frozen=True, slots=True)
class CacheDecision:
    """Outcome of a cache lookup: hit, or miss needing a provider call."""

    value: dict[str, Any] | None
    should_fetch: bool


class ProviderDataCache:
    """Cache-aside layer for provider data, freshness rules enforced."""

    def __init__(self, state: StateService) -> None:
        self._state = state

    async def get_match_details(self, game_key: str, provider: str, match_ref: str) -> CacheDecision:
        """Live match details: 5s TTL, then a fresh provider call."""
        cached = await self._state.get_state(_SCOPE, f"{game_key}:match:{provider}:{match_ref}")
        if cached is not None:
            return CacheDecision(value=cached, should_fetch=False)
        return CacheDecision(value=None, should_fetch=True)

    async def store_match_details(self, game_key: str, provider: str, match_ref: str, details: dict[str, Any]) -> None:
        """Cache a live match answer; short TTL, overwritten by events."""
        await self._state.set_state(_SCOPE, f"{game_key}:match:{provider}:{match_ref}", details, ttl=MATCH_TTL_S)

    async def should_fetch_completed(self, game_key: str, provider: str, match_ref: str) -> bool:
        """Return true only for the first fetch of a completed match, ever.

        The marker is set atomically (set-if-absent): concurrent
        completions race, exactly one wins and fetches.
        """
        key = f"{game_key}:match:{provider}:{match_ref}"
        acquired = await self._state.set_state(_MARKER_SCOPE, key, {"fetched": True}, only_if_absent=True)
        return acquired

    async def store_completed(self, game_key: str, provider: str, match_ref: str, details: dict[str, Any]) -> None:
        """Persist a completed match answer for the day-scale horizon."""
        await self._state.set_state(_SCOPE, f"{game_key}:match:{provider}:{match_ref}", details)

    async def get_completed(self, game_key: str, provider: str, match_ref: str) -> dict[str, Any] | None:
        """Return a cached completed-match answer; None on miss."""
        return await self._state.get_state(_SCOPE, f"{game_key}:match:{provider}:{match_ref}")

    async def get_profile(self, game_key: str, provider: str, profile_id: str) -> CacheDecision:
        """Player profile: 5-minute freshness between two fetches."""
        cached = await self._state.get_state(_SCOPE, f"{game_key}:profile:{provider}:{profile_id}")
        if cached is not None:
            return CacheDecision(value=cached, should_fetch=False)
        return CacheDecision(value=None, should_fetch=True)

    async def store_profile(self, game_key: str, provider: str, profile_id: str, stats: dict[str, Any]) -> None:
        """Cache a profile answer; re-fetch allowed after the TTL."""
        await self._state.set_state(_SCOPE, f"{game_key}:profile:{provider}:{profile_id}", stats, ttl=PROFILE_TTL_S)

    async def stale_registered_profiles(
        self, game_key: str, provider: str, profile_ids: list[str], now_s: int
    ) -> list[str]:
        """Profiles of registered players not refreshed within a day.

        The daily sweep calls this with the full list of registered
        profiles; only the stale ones get re-queued for a fetch.
        """
        stale: list[str] = []
        for profile_id in profile_ids:
            key = f"{game_key}:profile_refresh:{provider}:{profile_id}"
            last = await self._state.get_state(_SCOPE, key)
            if last is None or now_s - int(last.get("at", 0)) >= SWEEP_PROFILE_STALE_S:
                stale.append(profile_id)
        return stale

    async def mark_profile_refreshed(self, game_key: str, provider: str, profile_id: str, now_s: int) -> None:
        """Record the last daily-sweep refresh of a profile."""
        await self._state.set_state(
            _SCOPE,
            f"{game_key}:profile_refresh:{provider}:{profile_id}",
            {"at": now_s},
        )

    async def record_match_extraction(self, game_key: str, match_ref: str, extracted: dict[str, Any]) -> None:
        """Keep the raw extracted fields for the comparison tooling."""
        await self._state.set_state(_SCOPE, f"{game_key}:extraction:{match_ref}", extracted)

    async def get_match_extraction(self, game_key: str, match_ref: str) -> dict[str, Any] | None:
        """Return the recorded extraction of a match; None on miss."""
        return await self._state.get_state(_SCOPE, f"{game_key}:extraction:{match_ref}")

    def dump_extraction_json(self, extracted: dict[str, Any]) -> str:
        """Serialize an extraction for storage or the comparison script."""
        return json.dumps(extracted, sort_keys=True, ensure_ascii=False)
