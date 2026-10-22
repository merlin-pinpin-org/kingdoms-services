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
    """One line per user: active first, then offline by recency."""
    body = render_dashboard(
        _snapshot(
            [
                _player("10", "A", STATE_OFFLINE),
                _player("20", "B", STATE_IN_GAME, "m2", 5_000),
                _player("30", "C", STATE_OFFLINE),
            ]
        ),
        stats={
            "A": _stats("Alice", "1500", "3", "1", 9_900_000),
            "B": _stats("Bob", "1600", "9", "2", 9_700_000),
            "C": _stats("Cara", "", "", "", 9_950_000),
        },
        now_ms=10_000_000,
    )
    assert "🟢 <@20>" in body
    assert "— offline —" in body
    assert body.index("🟢 <@20>") < body.index("— offline —")
    assert body.index("— offline —") < body.index("⚫ <@10>")
    assert "last match" in body


def test_render_one_line_per_discord_user() -> None:
    """A user's profiles are fused into one line — no per-profile sub-lines."""
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
    assert "Alice" not in body and "Bob" not in body
    assert "RM 1v1" not in body


def test_render_omits_users_offline_over_an_hour() -> None:
    """Users offline for more than the grace window are omitted."""
    body = render_dashboard(
        _snapshot([_player("40", "D", STATE_OFFLINE)]),
        stats={"D": _stats("Dan", "", "", "", 0)},
        now_ms=10_000_000,
    )
    assert "<@40>" not in body
    assert "offline for more than an hour" in body


def test_render_without_stats_shows_the_user_line() -> None:
    """Without stats an offline user within the grace window still renders."""
    body = render_dashboard(_snapshot([_player("10", "A", STATE_OFFLINE)]), now_ms=1_000_000)
    assert "⚫ <@10>" in body


def test_degraded_banner_still_shown() -> None:
    body = render_dashboard({"players": [], "generated_at": 0, "degraded": True})
    assert "Providers unreachable" in body
    assert "No linked players" in body
