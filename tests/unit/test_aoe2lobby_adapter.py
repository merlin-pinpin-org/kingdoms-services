"""Unit tests for the ext-aoe2lobby live adapter (WebSocket layer).

The adapter normalizes raw WS frames into event dicts: known types
mapped, unknown dropped, undecodable skipped — best-effort by contract.
"""

from __future__ import annotations

from kingdoms.ext_aoe2lobby.adapter import Aoe2LobbyAdapter


def test_normalize_known_event() -> None:
    """A lobby_created frame maps to the normalized lobby_opened shape."""
    adapter = Aoe2LobbyAdapter()
    frame = adapter._normalize(
        '{"type": "lobby_created", "lobby_id": 42, "timestamp": 1700000000, "players": [123, 456], "map": "Arabia"}'
    )
    assert frame == {
        "match_ref": "42",
        "type": "lobby_opened",
        "occurred_at": 1700000000,
        "profile_ids": ["123", "456"],
        "metadata": {"map": "Arabia"},
    }


def test_normalize_lobby_closed_and_game_started() -> None:
    """The lifecycle types map through their canonical names."""
    adapter = Aoe2LobbyAdapter()
    closed = adapter._normalize('{"event": "lobby_closed", "match_id": "m1"}')
    started = adapter._normalize('{"type": "game_started", "match_id": "m1"}')
    assert closed is not None and closed["type"] == "lobby_closed"
    assert started is not None and started["type"] == "game_started"


def test_normalize_unknown_type_dropped() -> None:
    """Frames with unknown event types are dropped, not errored."""
    adapter = Aoe2LobbyAdapter()
    assert adapter._normalize('{"type": "chat_message"}') is None


def test_normalize_undecodable_frame_skipped() -> None:
    """Undecodable frames are skipped with no exception."""
    adapter = Aoe2LobbyAdapter()
    assert adapter._normalize("not json {") is None


def test_normalize_non_object_frame_dropped() -> None:
    """A JSON scalar (not an object) is dropped."""
    adapter = Aoe2LobbyAdapter()
    assert adapter._normalize("[1, 2]") is None


def test_track_grace_feeds_the_window() -> None:
    """The adapter feeds the adapter-side grace window from the stream."""
    from kingdoms.core.games.aoe2.grace import GRACE_PERIOD_S

    adapter = Aoe2LobbyAdapter()
    adapter._track_grace(
        {"match_ref": "m1", "type": "lobby_closed", "occurred_at": 0, "profile_ids": [], "metadata": {}}
    )
    closed_at = adapter._grace._closed_at["m1"]
    assert adapter._grace.should_downgrade("m1", now=closed_at + GRACE_PERIOD_S - 1) is False
    assert adapter._grace.should_downgrade("m1", now=closed_at + GRACE_PERIOD_S) is True


def test_track_grace_game_started_cancels_downgrade() -> None:
    """A game_started inside the window cancels the pending downgrade."""
    adapter = Aoe2LobbyAdapter()
    adapter._track_grace(
        {"match_ref": "m1", "type": "lobby_closed", "occurred_at": 0, "profile_ids": [], "metadata": {}}
    )
    adapter._track_grace(
        {"match_ref": "m1", "type": "game_started", "occurred_at": 1, "profile_ids": [], "metadata": {}}
    )
    assert adapter._grace.should_downgrade("m1", now=1e9) is False


def test_track_grace_game_ended_forgets() -> None:
    """A game_ended drops the tracking entirely (terminal state)."""
    adapter = Aoe2LobbyAdapter()
    adapter._track_grace(
        {"match_ref": "m1", "type": "lobby_closed", "occurred_at": 0, "profile_ids": [], "metadata": {}}
    )
    adapter._track_grace({"match_ref": "m1", "type": "game_ended", "occurred_at": 1, "profile_ids": [], "metadata": {}})
    assert "m1" not in adapter._grace._closed_at


def test_normalize_lobby_snapshot() -> None:
    """A lobby_match_all frame maps to one aggregate lobby_snapshot event."""
    from kingdoms.ext_aoe2lobby.adapter import Aoe2LobbyAdapter

    adapter = Aoe2LobbyAdapter()
    frame = {
        "lobby_match_all": {
            "511184270": {
                "matchid": 511184270,
                "slots": {
                    "s1": {"profileid": 3367233, "status": 3},
                    "s2": {"profileid": None, "status": 0},
                },
            },
            "511737107": {
                "matchid": 511737107,
                "slots": {"s1": {"profileid": 42, "status": 3}},
            },
        }
    }
    import json as jsonlib

    event = adapter._normalize(jsonlib.dumps(frame))
    assert event is not None
    assert event["type"] == "lobby_snapshot"
    assert sorted(event["profile_ids"]) == ["3367233", "42"]
    assert event["metadata"]["lobby_count"] == "2"


def test_normalize_player_status_frame() -> None:
    """A player_status frame maps to a normalized status event."""
    import json as jsonlib

    from kingdoms.ext_aoe2lobby.adapter import Aoe2LobbyAdapter

    adapter = Aoe2LobbyAdapter()
    frame = {"player_status": {"19501096": {"status": "lobby", "matchid": "412015195"}}}
    event = adapter._normalize(jsonlib.dumps(frame))
    assert event is not None
    assert event["type"] == "player_status"
    assert event["profile_ids"] == ["19501096"]
    assert event["metadata"]["19501096"] == "lobby"
