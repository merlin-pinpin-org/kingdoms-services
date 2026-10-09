"""Entity play-stats: aggregate the matches collection per map and civ.

The matches documents carry the played map, each side's faction and the
winner (the CF dump's shape): ``map_name``, ``host_discord_id`` /
``host_faction_key``, ``guest_...``, ``winner_discord_id``, ``status``.
This module folds that history into per-entity stats (games, wins,
winrate) the map posts render. Everything degrades: an absent
or empty collection yields empty stats, never an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("kingdoms.core.entity_stats")

MATCHES_COLLECTION = "matches"


@dataclass(frozen=True)
class EntityStats:
    """One entity's aggregated play stats."""

    games: int
    wins: int

    @property
    def winrate(self) -> float:
        """The win rate, 0.0 on an empty record."""
        return self.wins / self.games if self.games else 0.0


def _is_completed(doc: dict[str, Any]) -> bool:
    """Whether the match reached a result (the dump marks COMPLETED)."""
    return str(doc.get("status", "")).lower() == "completed"


def _sides(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract the (discord_id, faction_key) sides of one match."""
    sides: list[tuple[str, str]] = []
    for prefix in ("host", "guest"):
        user_id = str(doc.get(f"{prefix}_discord_id", "") or "")
        faction = str(doc.get(f"{prefix}_faction_key", "") or "")
        if user_id and faction:
            sides.append((user_id, faction))
    return sides


class EntityStatsService:
    """Aggregate per-entity play stats from the matches collection."""

    def __init__(self, database: Any) -> None:
        """Store the async Mongo database seam."""
        self._database = database

    async def map_stats(self, map_name: str) -> EntityStats:
        """Return one map's play stats (games, wins across its players)."""
        return await self._entity_stats("map_name", map_name)

    async def _entity_stats(self, field: str, value: str) -> EntityStats:
        """Fold the matches sharing one field's value (maps today)."""
        games = 0
        wins = 0
        try:
            cursor = self._database[MATCHES_COLLECTION].find({})
            async for doc in cursor:
                if not _is_completed(doc):
                    continue
                if str(doc.get(field, "") or "").strip().lower() != value.strip().lower():
                    continue
                games += 1
                if str(doc.get("winner_discord_id", "") or ""):
                    wins += 1
        except Exception:
            logger.debug("entity stats: matches scan failed — empty", exc_info=True)
            return EntityStats(games=0, wins=0)
        return EntityStats(games=games, wins=wins)
