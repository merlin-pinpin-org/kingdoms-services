"""Unit tests for the AoE2 lobby-closed grace period (reference 5.2).

The known AoE2 trap: the lobby closes a few moments before the game
starts, so a lobby_closed event must wait out the grace period before
any downgrade — a game_started arriving in between cancels it.
"""

from __future__ import annotations

from kingdoms.core.games.aoe2.grace import GRACE_PERIOD_S, LobbyClosedGrace


def test_no_lobby_closed_never_downgrades() -> None:
    """Without a recorded lobby_closed there is nothing to downgrade."""
    grace = LobbyClosedGrace()
    assert grace.should_downgrade("m-1") is False


def test_downgrade_only_after_grace_elapsed() -> None:
    """The downgrade fires only once the full grace period has passed."""
    grace = LobbyClosedGrace()
    grace.on_lobby_closed("m-1", now=100.0)
    assert grace.should_downgrade("m-1", now=100.0 + GRACE_PERIOD_S - 1) is False
    assert grace.should_downgrade("m-1", now=100.0 + GRACE_PERIOD_S) is True


def test_game_started_cancels_the_downgrade() -> None:
    """A game_started within the grace window cancels the downgrade."""
    grace = LobbyClosedGrace()
    grace.on_lobby_closed("m-1", now=100.0)
    grace.on_game_started("m-1")
    assert grace.should_downgrade("m-1", now=100.0 + GRACE_PERIOD_S * 2) is False


def test_events_are_idempotent() -> None:
    """Replayed external events have no side effect."""
    grace = LobbyClosedGrace()
    grace.on_lobby_closed("m-1", now=100.0)
    grace.on_lobby_closed("m-1", now=100.0)
    assert grace.should_downgrade("m-1", now=100.0 + GRACE_PERIOD_S) is True
    grace.on_game_started("m-1")
    grace.on_game_started("m-1")
    assert grace.should_downgrade("m-1", now=100.0 + GRACE_PERIOD_S * 2) is False


def test_forget_drops_tracking() -> None:
    """Terminal states drop the tracking entirely."""
    grace = LobbyClosedGrace()
    grace.on_lobby_closed("m-1", now=100.0)
    grace.forget("m-1")
    assert grace.should_downgrade("m-1", now=100.0 + GRACE_PERIOD_S * 3) is False


def test_configurable_grace_window() -> None:
    """The grace window is configurable adapter-side."""
    grace = LobbyClosedGrace(grace_s=5)
    grace.on_lobby_closed("m-1", now=100.0)
    assert grace.should_downgrade("m-1", now=104.0) is False
    assert grace.should_downgrade("m-1", now=105.0) is True
