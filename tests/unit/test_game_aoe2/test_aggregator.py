"""Unit tests for the game-aoe2 aggregator (enriched player states)."""

from __future__ import annotations

from kingdoms.game_aoe2.aggregator import (
    STATE_IN_GAME,
    STATE_IN_LOBBY,
    STATE_OFFLINE,
    GameAoe2Aggregator,
)

SUMMARY = {
    "map": "Arabia",
    "format": "1v1",
    "players": "Alice(Britons),Bob(Franks)",
    "lobby_url": "https://www.aoe2lobby.com/j/111",
    "deep_link": "aoe2de://1/-111",
}


class FakeEvent:
    """Minimal MatchEvent stand-in carrying a match summary."""

    def __init__(
        self,
        type: str,
        profile_ids: tuple[str, ...],
        occurred_at: int,
        match_ref: str = "111",
        metadata: tuple = (),
    ) -> None:
        self.type = type
        self.profile_ids = profile_ids
        self.occurred_at = occurred_at
        self.match_ref = match_ref
        self.metadata = metadata


def _summary_event(type: str, occurred_at: int = 1_000) -> FakeEvent:
    return FakeEvent(type, ("123",), occurred_at, "111", tuple(SUMMARY.items()))


async def test_event_folds_enriched_state() -> None:
    agg = GameAoe2Aggregator()
    await agg.apply_event(_summary_event("lobby_opened"))
    snapshot = agg.snapshot()
    player = snapshot["players"][0]
    assert player["state"] == STATE_IN_LOBBY
    assert player["match"]["map"] == "Arabia"
    assert player["match"]["deep_link"] == "aoe2de://1/-111"
    assert snapshot["degraded"] is False


async def test_transitions_queued_with_summary() -> None:
    agg = GameAoe2Aggregator()
    await agg.apply_event(_summary_event("lobby_opened"))
    await agg.apply_event(_summary_event("game_started", 2_000))
    first = await agg.next_transition()
    assert first["old_status"] == STATE_OFFLINE
    assert first["new_status"] == STATE_IN_LOBBY
    assert first["match"]["format"] == "1v1"
    second = await agg.next_transition()
    assert second["new_status"] == STATE_IN_GAME


async def test_lobby_closed_grace_keeps_game_state() -> None:
    agg = GameAoe2Aggregator()
    await agg.apply_event(_summary_event("game_started", 100_000))
    await agg.apply_event(_summary_event("lobby_closed", 105_000))
    player = agg.snapshot()["players"][0]
    assert player["state"] == STATE_IN_GAME


async def test_offline_clears_the_match() -> None:
    agg = GameAoe2Aggregator()
    await agg.apply_event(_summary_event("game_started", 1_000))
    await agg.apply_event(_summary_event("game_ended", 50_000))
    player = agg.snapshot()["players"][0]
    assert player["state"] == STATE_OFFLINE
    assert player["match_ref"] == ""
    assert player["match"] == {}


async def test_unknown_event_type_ignored() -> None:
    agg = GameAoe2Aggregator()
    await agg.apply_event(FakeEvent("chat_message", ("123",), 1_000))
    assert agg.snapshot()["players"] == []
    assert agg.pending_transitions() == 0
