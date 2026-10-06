"""Unit tests for the provider bridges (typed adapters -> wire dicts)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.models.game import (
    MatchDetails,
    PlayerStats,
    Slot,
    StatsBlock,
    StatsEntry,
)
from kingdoms.mods.ladder.match_data import MatchDataService
from kingdoms.mods.ladder.provider_bridge import (
    LibrematchProviderBridge,
    match_details_to_wire,
)


class FakeLibrematch:
    """Minimal stand-in honoring match_details/player_stats signatures."""

    async def match_details(self, match_ref: str) -> MatchDetails | None:
        """Serve one known match, None for anything else."""
        if match_ref != "508879537":
            return None
        return MatchDetails(
            match_ref=match_ref,
            map_name="Frigid Lake",
            slots=(
                Slot(slot_index=0, profile_id="1143826", faction_key="incas", filled=True),
                Slot(slot_index=1, profile_id="442163", faction_key="dravidians", filled=True),
                Slot(slot_index=2),
            ),
            options=(("victory_condition", "conquest"),),
            started_at=1790262333,
            match_kind="lobby",
        )

    async def player_stats(self, profile_id: str) -> PlayerStats | None:
        """Serve one stats block."""
        return PlayerStats(
            profile_id=profile_id,
            blocks=(StatsBlock(name="rm_1v1", entries=(StatsEntry(key="elo", value="1116"),)),),
        )


async def test_wire_conversion_roundtrip() -> None:
    details = await FakeLibrematch().match_details("508879537")
    assert details is not None
    wire = match_details_to_wire(details)
    assert wire["map_name"] == "Frigid Lake"
    assert len(wire["slots"]) == 3
    assert wire["slots"][0]["faction_key"] == "incas"
    assert wire["options"] == {"victory_condition": "conquest"}


async def test_bridge_serves_extractable_details() -> None:
    bridge = LibrematchProviderBridge(FakeLibrematch())  # type: ignore[arg-type]
    service = MatchDataService.__new__(MatchDataService)
    wire = await bridge.fetch_match("508879537")
    assert wire is not None
    extracted = service.extract(wire)
    assert extracted["map"] == "Frigid Lake"
    assert {c["faction_key"] for c in extracted["civs"]} == {"incas", "dravidians"}
    assert len(extracted["civs"]) == 2


async def test_bridge_degrades_on_unknown_match() -> None:
    bridge = LibrematchProviderBridge(FakeLibrematch())  # type: ignore[arg-type]
    assert await bridge.fetch_match("unknown") is None


async def test_bridge_converts_player_stats() -> None:
    bridge = LibrematchProviderBridge(FakeLibrematch())  # type: ignore[arg-type]
    stats: dict[str, Any] | None = await bridge.fetch_player_stats("1143826")
    assert stats is not None
    assert stats["blocks"][0]["entries"][0] == {"key": "elo", "value": "1116"}
