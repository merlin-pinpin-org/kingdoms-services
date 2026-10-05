"""Unit tests for the aoe2lobby snapshot bridge."""

from __future__ import annotations

from typing import Any

from kingdoms.mods.ladder.live_bridge import Aoe2LobbySnapshotBridge


def _event(kind: str, match_ref: str, **extra: Any) -> dict[str, Any]:
    return {
        "match_ref": match_ref,
        "type": kind,
        "occurred_at": 1790262333000,
        "profile_ids": [],
        "metadata": {},
        **extra,
    }


def test_apply_folds_events_into_snapshot() -> None:
    bridge = Aoe2LobbySnapshotBridge(None)
    bridge._apply(_event("lobby_opened", "m1", metadata={"mapname": "Frigid Lake"}, profile_ids=["1143826", "442163"]))
    bridge._apply(_event("game_started", "m1", occurred_at=1790262333000))
    snapshot = bridge._snapshots["m1"]
    assert snapshot["map_name"] == "Frigid Lake"
    assert snapshot["started_at"] == 1790262333000
    assert [s["profile_id"] for s in snapshot["slots"]] == ["1143826", "442163"]


def test_fetch_match_serves_snapshot_and_none_for_unknown() -> None:
    bridge = Aoe2LobbySnapshotBridge(None)
    bridge._apply(_event("lobby_opened", "m1", profile_ids=["1143826"]))
    assert bridge.fetch_match.__self__.provider_key == "aoe2lobby"
    import asyncio

    assert asyncio.run(bridge.fetch_match("m1")) is not None
    assert asyncio.run(bridge.fetch_match("unknown")) is None


def test_game_ended_sets_ended_and_duration_inputs() -> None:
    bridge = Aoe2LobbySnapshotBridge(None)
    bridge._apply(_event("game_started", "m1", occurred_at=1000))
    bridge._apply(_event("game_ended", "m1", occurred_at=2077))
    snapshot = bridge._snapshots["m1"]
    assert snapshot["started_at"] == 1000
    assert snapshot["ended_at"] == 2077


def test_empty_match_ref_is_ignored() -> None:
    bridge = Aoe2LobbySnapshotBridge(None)
    bridge._apply(_event("lobby_opened", ""))
    assert bridge._snapshots == {}
