"""Unit tests for the grouped live dashboard rendering (#147)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.live import STATE_IN_GAME, STATE_IN_LOBBY, STATE_OFFLINE
from kingdoms.discord.live import group_by_account, render_dashboard


def _snapshot(players: list[dict[str, Any]]) -> dict[str, Any]:
    return {"players": players, "generated_at": 0, "degraded": False}


def _player(user: str, profile: str, state: str, match: str = "", since: int = 0) -> dict[str, Any]:
    return {"user_id": user, "profile_id": profile, "state": state, "match_ref": match, "since": since}



def _stats(name: str, rating: str, wins: str, losses: str, ms: int) -> dict[str, Any]:
    return {
        "display_name": name,
        "boards": [{"key": "rm_1v1", "label": "RM 1v1", "rating": rating, "wins": wins, "losses": losses}],
        "last_match_ms": ms,
    }

def test_accounts_grouped_by_discord_user() -> None:
    accounts = group_by_account(
        [
            _player("10", "A", STATE_OFFLINE),
            _player("10", "B", STATE_IN_LOBBY),
            _player("20", "C", STATE_OFFLINE),
        ]
    )
    assert set(accounts) == {"10", "20"}
    assert [p["profile_id"] for p in accounts["10"]["profiles"]] == ["A", "B"]
    assert accounts["10"]["active"] is True
    assert accounts["20"]["active"] is False


def test_render_orders_online_first_then_last_match() -> None:
    body = render_dashboard(
        _snapshot(
            [
                _player("10", "A", STATE_OFFLINE),
                _player("20", "B", STATE_IN_GAME, "m2", 5_000),
                _player("30", "C", STATE_OFFLINE),
            ]
        ),
        stats={
            "A": _stats("Alice", "1500", "3", "1", 9_000),
            "B": _stats("Bob", "1600", "9", "2", 7_000),
            "C": _stats("Cara", "", "", "", 0),
        },
    )
    assert body.index("🟢 <@20>") < body.index("— offline —")
    assert "— offline —" in body
    assert body.index("— offline —") < body.index("⚫ <@10>")
    assert body.index("⚫ <@10>") < body.index("⚫ <@30>")
    assert "**Bob**" in body and "RM 1v1: 1600 elo" in body and "(9W/2L)" in body
    assert "**Alice**" in body and "match `m1`" not in body


def test_render_profile_sub_lines_carry_name_state_stats() -> None:
    body = render_dashboard(
        _snapshot(
            [
                _player("10", "A", STATE_IN_GAME, "m1", 1_000),
                _player("10", "B", STATE_OFFLINE),
            ]
        ),
        stats={
            "A": _stats("Alice", "1500", "3", "1", 4_000),
            "B": _stats("Bob", "1200", "1", "5", 2_000),
        },
    )
    assert "🟢 <@10>" in body
    assert "**Alice** 🟢 in_game" in body
    assert "**Bob** ⚫ offline" in body
    assert "match `m1`" in body


def test_render_without_stats_shows_profile_ids() -> None:
    body = render_dashboard(_snapshot([_player("10", "A", STATE_OFFLINE)]))
    assert "⚫ <@10>" in body
    assert "A" in body


def test_degraded_banner_still_shown() -> None:
    body = render_dashboard({"players": [], "generated_at": 0, "degraded": True})
    assert "Providers unreachable" in body
    assert "No linked players" in body
