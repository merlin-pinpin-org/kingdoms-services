"""Unit tests for the ext-aoe2lobby live adapter (real aoe2lobby protocol).

The adapter is the client-side reducer over the global lobby/spectate
deltas: snapshots and updates add state, removes park behind the grace
period, and per-profile transitions come out in the MatchEvent shape.
"""

from __future__ import annotations

import json
from typing import Any

from kingdoms.ext_aoe2lobby.adapter import Aoe2LobbyAdapter


def _frame(adapter: Aoe2LobbyAdapter, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return adapter.apply_frame(json.dumps(payload))


def _lobby(match_id: str, profile_ids: list[Any]) -> dict[str, Any]:
    return {
        "matchid": int(match_id),
        "map_name": "Arabia",
        "slots": {
            f"s{i}": {"profileid": pid, "status": 3}
            for i, pid in enumerate(profile_ids, start=1)
            if pid is not None
        },
    }


def test_lobby_snapshot_emits_lobby_opened_per_profile() -> None:
    adapter = Aoe2LobbyAdapter()
    events = _frame(
        adapter,
        {
            "lobby_match_all": {
                "111": _lobby("111", [123, 456]),
                "222": _lobby("222", [789]),
            }
        },
    )
    assert sorted(e["profile_ids"] for e in events) == [["123"], ["456"], ["789"]]
    opened = next(e for e in events if e["profile_ids"] == ["123"])
    assert opened["type"] == "lobby_opened"
    assert opened["match_ref"] == "111"
    assert opened["metadata"]["map"] == "Arabia"
    assert opened["metadata"]["match_url"] == "https://www.aoe2lobby.com/j/111"


def test_match_update_join_emits_transition_snapshot_idempotent() -> None:
    adapter = Aoe2LobbyAdapter()
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    join = _frame(adapter, {"lobby_match_update": {"111": _lobby("111", [123, 456])}})
    assert [e["profile_ids"] for e in join] == [["456"]]
    again = _frame(adapter, {"lobby_match_update": {"111": _lobby("111", [123, 456])}})
    assert again == []


def test_launch_lobby_remove_then_spectate_grace() -> None:
    adapter = Aoe2LobbyAdapter(grace_s=15)
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    removed = _frame(adapter, {"lobby_match_remove": ["111"]})
    assert removed == []
    started = _frame(adapter, {"spectate_match_update": {"111": _lobby("111", [123])}})
    assert [e["type"] for e in started] == ["game_started"]
    assert started[0]["metadata"]["match_url"] == "https://www.aoe2lobby.com/w/111"
    swept = adapter._sweep_pending_closes()
    assert swept == []


def test_lobby_remove_elapses_grace_then_lobby_closed() -> None:
    adapter = Aoe2LobbyAdapter(grace_s=15)
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    _frame(adapter, {"lobby_match_remove": ["111"]})
    profile_id = "123"
    closed_at = adapter._pending_closes[profile_id][1]
    adapter._pending_closes[profile_id] = (adapter._pending_closes[profile_id][0], closed_at - 16_000)
    events = adapter._sweep_pending_closes()
    assert [e["type"] for e in events] == ["lobby_closed"]
    assert events[0]["profile_ids"] == ["123"]
    assert adapter._sweep_pending_closes() == []


def test_spectate_remove_after_grace_emits_game_ended() -> None:
    adapter = Aoe2LobbyAdapter(grace_s=15)
    _frame(adapter, {"spectate_match_all": {"111": _lobby("111", [123])}})
    _frame(adapter, {"spectate_match_remove": ["111"]})
    profile_id = "123"
    closed_at = adapter._pending_closes[profile_id][1]
    adapter._pending_closes[profile_id] = (adapter._pending_closes[profile_id][0], closed_at - 16_000)
    events = adapter._sweep_pending_closes()
    assert [e["type"] for e in events] == ["game_ended"]
    assert events[0]["profile_ids"] == ["123"]


def test_player_remove_schedules_graced_close() -> None:
    adapter = Aoe2LobbyAdapter(grace_s=15)
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    events = _frame(adapter, {"lobby_player_remove": [123]})
    assert events == []
    assert "123" in adapter._pending_closes


def test_null_match_all_treated_as_empty() -> None:
    adapter = Aoe2LobbyAdapter()
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    removed = _frame(adapter, {"lobby_match_all": None})
    assert removed == []
    assert "123" in adapter._pending_closes


def test_profile_ids_compared_as_str() -> None:
    adapter = Aoe2LobbyAdapter()
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", ["123"])}})
    join = _frame(adapter, {"lobby_match_update": {"111": _lobby("111", [123])}})
    assert join == []


def test_unknown_keys_and_undecodable_frames_ignored() -> None:
    adapter = Aoe2LobbyAdapter()
    assert _frame(adapter, {"lobby_observers_update": {"111": {"observers": 3}}}) == []
    assert adapter.apply_frame("not json {") == []
    assert adapter.apply_frame("[1, 2]") == []


def test_rejoin_same_lobby_no_duplicate_close() -> None:
    adapter = Aoe2LobbyAdapter(grace_s=15)
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    _frame(adapter, {"lobby_match_remove": ["111"]})
    rejoin = _frame(adapter, {"lobby_match_update": {"111": _lobby("111", [123])}})
    assert rejoin == []
    assert adapter._sweep_pending_closes() == []


def test_lobby_to_game_same_profile_supersedes() -> None:
    adapter = Aoe2LobbyAdapter()
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    started = _frame(adapter, {"spectate_match_all": {"222": _lobby("222", [123])}})
    assert [e["type"] for e in started] == ["game_started"]
    assert started[0]["match_ref"] == "222"
    assert started[0]["metadata"]["old_status"] == "lobby"
    assert adapter._player_status["123"] == ("game", "222")


def test_reset_matches_keeps_statuses_for_snapshot_diff() -> None:
    adapter = Aoe2LobbyAdapter()
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    adapter._reset_matches()
    assert adapter._matches == {"lobby": {}, "spectate": {}}
    assert adapter._player_status == {"123": ("lobby", "111")}
    reseen = _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    assert reseen == []
    gone = _frame(adapter, {"lobby_match_all": {}})
    assert gone == []
    assert "123" in adapter._pending_closes


def test_snapshot_diff_emits_join_transition_after_reconnect() -> None:
    adapter = Aoe2LobbyAdapter()
    _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123])}})
    adapter._reset_matches()
    adapter._matches = {"lobby": {}, "spectate": {}}
    join = _frame(adapter, {"lobby_match_all": {"222": _lobby("222", [123])}})
    assert [e["type"] for e in join] == ["lobby_opened"]
    assert join[0]["match_ref"] == "222"


async def test_set_subscribed_profiles_reconciles_in_place() -> None:
    """The players subscription is reconciled on the live session, no reopen."""
    adapter = Aoe2LobbyAdapter()
    sent: list[dict] = []

    class _FakeWs:
        async def send(self, payload: str) -> None:
            sent.append(dict(json.loads(payload)))

    adapter._ws = _FakeWs()
    await adapter.set_subscribed_profiles(["10", "20"])
    assert sent == [
        {"action": "subscribe", "type": "players", "context": c, "ids": ["10", "20"]}
        for c in ("lobby", "spectate")
    ]
    await adapter.set_subscribed_profiles(["20", "30"])
    assert {m["action"] for m in sent} >= {"subscribe", "unsubscribe"}
    assert (sent[-2]["action"] == "subscribe" and sent[-2]["ids"] == ["30"]) or sent[-1]["ids"] == ["30"]
    assert adapter._subscribed_ids == {"20", "30"}


async def test_set_subscribed_profiles_without_session_queues() -> None:
    """Without a live session the list is kept for the next connect."""
    adapter = Aoe2LobbyAdapter()
    assert await adapter.set_subscribed_profiles(["10"]) is False
    assert adapter._subscribed_ids == {"10"}


async def test_set_subscribed_profiles_idempotent_no_traffic() -> None:
    """Reconciling the same list sends nothing."""
    adapter = Aoe2LobbyAdapter()
    await adapter.set_subscribed_profiles(["10"])
    adapter._ws = None
    adapter._subscribed_ids = {"10"}
    sent: list[str] = []

    class _FakeWs:
        async def send(self, payload: str) -> None:
            sent.append(payload)

    adapter._ws = _FakeWs()
    assert await adapter.set_subscribed_profiles(["10"]) is True
    assert sent == []


async def test_matches_reducer_filters_to_subscribed_profiles() -> None:
    """Match deltas only yield transitions for the subscribed profiles."""
    adapter = Aoe2LobbyAdapter()
    await adapter.set_subscribed_profiles(["123"])
    events = _frame(adapter, {"lobby_match_all": {"111": _lobby("111", [123, 456])}})
    assert [e["profile_ids"] for e in events] == [["123"]]


async def test_player_status_push_yields_transition() -> None:
    """A player_status push for a subscribed profile yields the transition."""
    adapter = Aoe2LobbyAdapter()
    await adapter.set_subscribed_profiles(["123"])
    events = _frame(
        adapter,
        {"player_status": {"123": {"status": "lobby", "matchid": "111", "steam_lobbyid": "x"}}},
    )
    assert [e["type"] for e in events] == ["lobby_opened"]
    assert events[0]["match_ref"] == "111"
    playing = _frame(
        adapter,
        {"player_status": {"123": {"status": "playing", "matchid": "111"}}},
    )
    assert [e["type"] for e in playing] == ["game_started"]


async def test_player_status_ignored_for_unsubscribed_profile() -> None:
    """player_status pushes for non-subscribed profiles are ignored."""
    adapter = Aoe2LobbyAdapter()
    await adapter.set_subscribed_profiles(["123"])
    events = _frame(
        adapter,
        {"player_status": {"999": {"status": "lobby", "matchid": "111"}}},
    )
    assert events == []


async def test_player_status_null_status_schedules_graced_close() -> None:
    """A null status parks the close behind the grace period."""
    adapter = Aoe2LobbyAdapter(grace_s=15)
    await adapter.set_subscribed_profiles(["123"])
    _frame(adapter, {"player_status": {"123": {"status": "lobby", "matchid": "111"}}})
    gone = _frame(adapter, {"player_status": {"123": {"status": None, "matchid": None}}})
    assert gone == []
    assert "123" in adapter._pending_closes
    assert adapter._sweep_pending_closes() == []
    closed_at = adapter._pending_closes["123"][1]
    adapter._pending_closes["123"] = (adapter._pending_closes["123"][0], closed_at - 16_000)
    closed = adapter._sweep_pending_closes()
    assert [e["type"] for e in closed] == ["lobby_closed"]
