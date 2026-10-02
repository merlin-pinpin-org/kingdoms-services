"""AoE2 lobby-closed grace period (reference 5.2, known trap).

The AoE2 lobby always closes a few moments before the game starts: a
``lobby_closed`` event in LOBBY status must wait out a grace period
(15 s default, configurable adapter-side) before any downgrade, because
a ``game_started`` may arrive in between. This module holds the rule
once, in the game module — both providers and the ladder core consume
it, none re-implements it.
"""

from __future__ import annotations

import time

GRACE_PERIOD_S = 15


class LobbyClosedGrace:
    """Track lobby_closed grace windows per match reference."""

    def __init__(self, grace_s: int = GRACE_PERIOD_S) -> None:
        self._grace_s = grace_s
        self._closed_at: dict[str, float] = {}

    def on_lobby_closed(self, match_ref: str, now: float | None = None) -> None:
        """Record a lobby_closed event for the match."""
        self._closed_at[match_ref] = now if now is not None else time.monotonic()

    def on_game_started(self, match_ref: str) -> None:
        """Cancel the pending downgrade: a game_started arrived."""
        self._closed_at.pop(match_ref, None)

    def should_downgrade(self, match_ref: str, now: float | None = None) -> bool:
        """Report whether the grace period has fully elapsed since lobby_closed.

        No recorded lobby_closed means nothing to downgrade (idempotent
        — external events can be received multiple times).
        """
        closed_at = self._closed_at.get(match_ref)
        if closed_at is None:
            return False
        current = now if now is not None else time.monotonic()
        return (current - closed_at) >= self._grace_s

    def forget(self, match_ref: str) -> None:
        """Drop the tracking once the match reached a terminal state."""
        self._closed_at.pop(match_ref, None)
