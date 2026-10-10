"""Unit tests for the live dashboard aggregator and rendering (#147)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.live import (
    EVENT_GAME_ENDED,
    EVENT_GAME_STARTED,
    EVENT_LOBBY_CLOSED,
    EVENT_LOBBY_OPENED,
    STATE_IN_GAME,
    STATE_IN_LOBBY,
    STATE_OFFLINE,
    LiveAggregator,
)
from kingdoms.discord.live import render_dashboard


class FakeBindings:
    """In-memory bindings seam."""

    def __init__(self, bindings: list[dict[str, Any]]) -> None:
        self._bindings = bindings

    async def list_bindings_for_game(self, game_key: str) -> list[dict[str, Any]]:
        return [b for b in self._bindings if b.get("game_key") == game_key]


class FakeEvent:
    """Minimal MatchEvent stand-in."""

    def __init__(self, type: str, profile_ids: tuple[str, ...], occurred_at: int, match_ref: str = "m1") -> None:
        self.type = type
        self.profile_ids = profile_ids
        self.occurred_at = occurred_at
        self.match_ref = match_ref


def _bindings() -> FakeBindings:
    return FakeBindings(
        [
            {"user_id": "10", "game_key": "aoe2", "profile_id": "A"},
            {"user_id": "20", "game_key": "aoe2", "profile_id": "B"},
            {"user_id": "30", "game_key": "chess", "profile_id": "C"},
        ]
    )


async def test_snapshot_degraded_shows_all_offline() -> None:
    agg = LiveAggregator(_bindings())
    snap = await agg.snapshot("aoe2")
    assert snap["degraded"] is True
    assert len(snap["players"]) == 2
    assert all(p["state"] == STATE_OFFLINE for p in snap["players"])


async def test_lobby_and_game_events_update_state() -> None:
    agg = LiveAggregator(_bindings())
    await agg.apply_event(FakeEvent(EVENT_LOBBY_OPENED, ("A",), 1_000, "match-1"))
    snap = await agg.snapshot("aoe2")
    player_a = next(p for p in snap["players"] if p["profile_id"] == "A")
    assert player_a["state"] == STATE_IN_LOBBY
    assert player_a["match_ref"] == "match-1"
    assert snap["degraded"] is False

    await agg.apply_event(FakeEvent(EVENT_GAME_STARTED, ("A",), 2_000, "match-1"))
    snap = await agg.snapshot("aoe2")
    player_a = next(p for p in snap["players"] if p["profile_id"] == "A")
    assert player_a["state"] == STATE_IN_GAME

    await agg.apply_event(FakeEvent(EVENT_GAME_ENDED, ("A",), 3_000, "match-1"))
    snap = await agg.snapshot("aoe2")
    player_a = next(p for p in snap["players"] if p["profile_id"] == "A")
    assert player_a["state"] == STATE_OFFLINE
    assert player_a["match_ref"] == ""


async def test_lobby_closed_grace_keeps_fresh_game_state() -> None:
    agg = LiveAggregator(_bindings())
    await agg.apply_event(FakeEvent(EVENT_GAME_STARTED, ("A",), 100_000, "match-1"))
    await agg.apply_event(FakeEvent(EVENT_LOBBY_CLOSED, ("A",), 105_000, "match-1"))
    snap = await agg.snapshot("aoe2")
    player_a = next(p for p in snap["players"] if p["profile_id"] == "A")
    assert player_a["state"] == STATE_IN_GAME


async def test_mark_degraded_resets_to_offline() -> None:
    agg = LiveAggregator(_bindings())
    await agg.apply_event(FakeEvent(EVENT_LOBBY_OPENED, ("A",), 1_000))
    agg.mark_degraded()
    snap = await agg.snapshot("aoe2")
    assert snap["degraded"] is True
    assert all(p["state"] == STATE_OFFLINE for p in snap["players"])


def test_render_dashboard_degraded_and_empty() -> None:
    degraded = render_dashboard({"players": [], "generated_at": 0, "degraded": True})
    assert "No linked players" in degraded
    populated = render_dashboard(
        {
            "players": [{"user_id": "10", "profile_id": "A", "state": STATE_IN_GAME, "match_ref": "m1", "since": 1000}],
            "generated_at": 1,
            "degraded": False,
        }
    )
    assert "🟢 <@10>" in populated


def test_snapshot_fingerprint_ignores_generated_at() -> None:
    """An unchanged roster never triggers an edit: generated_at is excluded."""
    from kingdoms.discord.live import _snapshot_fingerprint

    base = {"players": [{"profile_id": "A", "state": "in_lobby"}], "degraded": False}
    first = _snapshot_fingerprint(base)
    second = _snapshot_fingerprint({**base, "generated_at": 12345})
    third = _snapshot_fingerprint({**base, "generated_at": 99999})
    assert first == second == third


def test_snapshot_fingerprint_tracks_state_changes() -> None:
    """A real state change produces a different fingerprint."""
    from kingdoms.discord.live import _snapshot_fingerprint

    idle = {"players": [{"profile_id": "A", "state": "offline"}], "degraded": False}
    in_game = {"players": [{"profile_id": "A", "state": "in_game"}], "degraded": False}
    assert _snapshot_fingerprint(idle) != _snapshot_fingerprint(in_game)
