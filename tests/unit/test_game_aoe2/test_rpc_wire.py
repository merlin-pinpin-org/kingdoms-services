"""Unit tests for the GameData wire translations (summary round-trip)."""

from __future__ import annotations

from kingdoms.game_aoe2.rpc import (
    dashboard_from_wire,
    dashboard_to_wire,
    event_from_wire,
    event_to_wire,
    summary_to_wire,
)

SUMMARY = {
    "match_ref": "111",
    "map": "Arabia",
    "format": "1v1",
    "status": "lobby",
    "slots_taken": "2",
    "slots_total": "2",
    "host": "Alice",
    "lobby_url": "https://www.aoe2lobby.com/j/111",
    "deep_link": "aoe2de://1/-111",
}

def test_summary_round_trip() -> None:
    wire = summary_to_wire(SUMMARY)
    assert wire.map_name == "Arabia"
    assert wire.format == "1v1"
    assert wire.deep_link == "aoe2de://1/-111"
    back = {
        "match_ref": wire.match_ref,
        "map": wire.map_name,
        "format": wire.format,
    }
    assert back["map"] == "Arabia"

def test_dashboard_round_trip() -> None:
    snapshot = {
        "players": [
            {
                "profile_id": "123",
                "state": "in_lobby",
                "match_ref": "111",
                "since": 1_000,
                "user_id": "10",
                "match": SUMMARY,
            }
        ],
        "generated_at": 5_000,
        "degraded": False,
    }
    wire = dashboard_to_wire(snapshot)
    assert wire.players[0].match.map_name == "Arabia"
    back = dashboard_from_wire(wire)
    assert back["players"][0]["match"]["map"] == "Arabia"
    assert back["players"][0]["match"]["deep_link"] == "aoe2de://1/-111"

def test_event_round_trip() -> None:
    transition = {
        "profile_id": "123",
        "old_status": "offline",
        "new_status": "in_lobby",
        "occurred_at": 1_000,
        "match": SUMMARY,
    }
    wire = event_to_wire(transition)
    assert wire.game_key == "aoe2"
    assert wire.match.map_name == "Arabia"
    back = event_from_wire(wire)
    assert back["new_status"] == "in_lobby"
    assert back["match"]["format"] == "1v1"
