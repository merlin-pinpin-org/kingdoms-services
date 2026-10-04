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
        '{"type": "lobby_created", "lobby_id": 42, "timestamp": 1700000000,'
        ' "players": [123, 456], "map": "Arabia"}'
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
