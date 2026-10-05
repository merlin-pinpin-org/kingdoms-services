"""Bridges between the provider adapters and the MatchDataProvider protocol.

The ladder consumes providers through the narrow ``MatchDataProvider``
seam (``fetch_match -> wire dict``). The concrete adapters return typed
dataclasses (``MatchDetails``); these bridges convert without touching
the adapters themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kingdoms.core.models.game import MatchDetails
    from kingdoms.ext_librematch.adapter import LibrematchAdapter


def match_details_to_wire(details: MatchDetails) -> dict[str, Any]:
    """Convert a typed MatchDetails to the wire dict the ladder extracts."""
    return {
        "match_ref": details.match_ref,
        "map_name": details.map_name,
        "slots": [
            {
                "slot_index": slot.slot_index,
                "profile_id": slot.profile_id,
                "faction_key": slot.faction_key,
                "team": slot.team,
                "filled": slot.filled,
                "slot_kind": slot.slot_kind,
            }
            for slot in details.slots
        ],
        "options": dict(details.options),
        "started_at": details.started_at,
        "match_kind": details.match_kind,
    }


class LibrematchProviderBridge:
    """MatchDataProvider adapter over LibrematchAdapter (cold fetch path)."""

    provider_key = "librematch"

    def __init__(self, adapter: LibrematchAdapter) -> None:
        self._adapter = adapter

    async def fetch_match(self, match_ref: str) -> dict[str, Any] | None:
        """Fetch a match's details as a wire dict, or None when unknown."""
        details = await self._adapter.match_details(match_ref)
        if details is None:
            return None
        return match_details_to_wire(details)

    async def fetch_player_stats(self, profile_id: str) -> dict[str, Any] | None:
        """Fetch a profile's stats blocks as a plain dict, or None."""
        stats = await self._adapter.player_stats(profile_id)
        if stats is None:
            return None
        return {
            "profile_id": stats.profile_id,
            "blocks": [
                {
                    "name": block.name,
                    "entries": [{"key": entry.key, "value": entry.value} for entry in block.entries],
                }
                for block in stats.blocks
            ],
        }
