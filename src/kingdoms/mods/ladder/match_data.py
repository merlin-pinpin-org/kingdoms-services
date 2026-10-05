"""Ladder match data: provider-backed enrichment through the cache (#205).

When a match completes, the ladder wants the real game data — map,
civs, duration — that only the providers hold (aoe2lobby during the
game, libre:match after). This service is the single consumer of the
provider data cache for the ladder mod:

- on completion, fetch the match details once (marker: never refetch a
  completed match) and store them on the match document;
- on demand, serve a player's stats with the 5-minute freshness rule;
- daily sweep: re-fetch the stats of every registered player at least
  once a day.

The provider seam is narrow: ``fetch_match(match_ref)`` returns the
raw provider answer or None; the service degrades silently — a
provider outage never blocks a match completion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from kingdoms.core.debug import capture
from kingdoms.core.services.provider_cache import ProviderDataCache


class MatchDataProvider(Protocol):
    """The narrow seam to a provider (aoe2lobby live, libre:match cold)."""

    provider_key: str

    async def fetch_match(self, match_ref: str) -> dict[str, Any] | None:
        """Fetch raw provider match details, or none if unavailable."""
        ...


@dataclass(frozen=True, slots=True)
class MatchDataReport:
    """Counts of an enrichment or sweep run, printed by callers."""

    enriched: int
    skipped_completed: int


class MatchDataService:
    """Fetch and cache match/profile data for the ladder, per game."""

    def __init__(
        self,
        cache: ProviderDataCache,
        game_key: str = "aoe2",
        live_provider: MatchDataProvider | None = None,
        cold_provider: MatchDataProvider | None = None,
    ) -> None:
        self._cache = cache
        self._game_key = game_key
        self._live = live_provider
        self._cold = cold_provider

    async def enrich_completed_match(self, match_ref: str, match_doc: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch a completed match's data once; None when already fetched.

        The marker (SET NX) makes the fetch race-safe: concurrent
        completions of the same match yield exactly one provider call.
        The answer lands in the cache and is merged into the match
        document's ``game`` block; the doc is returned so the caller
        persists it.
        """
        provider = self._cold or self._live
        if provider is None or not match_ref:
            capture("enrich.skip", game=self._game_key, match_ref=match_ref, reason="no_provider")
            return None
        if not await self._cache.should_fetch_completed(self._game_key, provider.provider_key, match_ref):
            capture("enrich.skip", game=self._game_key, match_ref=match_ref, reason="already_fetched")
            return None
        details = await provider.fetch_match(match_ref)
        capture(
            "provider.fetch",
            game=self._game_key,
            provider=provider.provider_key,
            match_ref=match_ref,
            answer=details,
        )
        if details is None:
            return None
        await self._cache.store_completed(self._game_key, provider.provider_key, match_ref, details)
        extracted = self.extract(details)
        capture("enrich.extract", game=self._game_key, match_ref=match_ref, extracted=extracted)
        await self._cache.record_match_extraction(self._game_key, match_ref, extracted)
        return extracted

    async def enrich_live_match(self, match_ref: str) -> dict[str, Any] | None:
        """Serve live match details through the 5s cache, or fetch."""
        provider = self._live or self._cold
        if provider is None or not match_ref:
            return None
        decision = await self._cache.get_match_details(self._game_key, provider.provider_key, match_ref)
        if not decision.should_fetch:
            return decision.value
        details = await provider.fetch_match(match_ref)
        capture(
            "provider.fetch",
            game=self._game_key,
            provider=provider.provider_key,
            match_ref=match_ref,
            answer=details,
        )
        if details is not None:
            await self._cache.store_match_details(self._game_key, provider.provider_key, match_ref, details)
        return details

    async def player_stats(self, profile_id: str, fetch_stats: Any) -> dict[str, Any] | None:
        """Serve a profile's stats with the 5-minute freshness rule.

        The stats fetcher is injected (the provider seam is match-
        oriented; stats come from the game module's player_stats), and
        only called on a cache miss.
        """
        provider = self._cold or self._live
        if provider is None or not profile_id:
            return None
        decision = await self._cache.get_profile(self._game_key, provider.provider_key, profile_id)
        if not decision.should_fetch:
            return decision.value
        stats = await fetch_stats(profile_id)
        if stats is not None:
            await self._cache.store_profile(self._game_key, provider.provider_key, profile_id, stats)
        return stats

    async def sweep_registered_profiles(self, profile_ids: list[str], fetch_stats: Any) -> MatchDataReport:
        """Re-fetch stats of registered players stale for over a day."""
        provider = self._cold or self._live
        if provider is None:
            return MatchDataReport(enriched=0, skipped_completed=0)
        now_s = int(time.time())
        stale = await self._cache.stale_registered_profiles(self._game_key, provider.provider_key, profile_ids, now_s)
        for profile_id in stale:
            stats = await fetch_stats(profile_id)
            if stats is not None:
                await self._cache.store_profile(self._game_key, provider.provider_key, profile_id, stats)
            await self._cache.mark_profile_refreshed(self._game_key, provider.provider_key, profile_id, now_s)
        return MatchDataReport(enriched=len(stale), skipped_completed=0)

    def extract(self, details: dict[str, Any]) -> dict[str, Any]:
        """Pull map, civs and duration out of a provider answer.

        The provider shape is the wire ``MatchDetails`` dict: slots
        carry the per-player civs; options carry the game settings;
        the duration comes from the timestamps when present.
        """
        slots = details.get("slots") or []
        civs = [
            {
                "profile_id": str(slot.get("profile_id", "")),
                "faction_key": str(slot.get("faction_key", "")),
                "team": int(slot.get("team", 0) or 0),
            }
            for slot in slots
            if slot.get("filled")
        ]
        started = int(details.get("started_at") or 0)
        ended = int(details.get("ended_at") or 0)
        duration = int(details.get("duration") or 0) or (
            (ended - started) if started and ended and ended > started else 0
        )
        return {
            "map": str(details.get("map_name", "")),
            "civs": civs,
            "duration_s": duration or None,
            "options": dict(details.get("options") or {}),
            "match_kind": str(details.get("match_kind", "")),
        }
