"""Live dashboard aggregator: provider events → per-player state (#147).

svc-core consumes the providers' ``StreamMatchEvents`` (ext-aoe2lobby
live layer, ext-librematch the source of truth), resolves profile ids
to platform users through the registration bindings, and maintains the
per-player dashboard state served over ``kingdoms.v1.Live``.

Degradation is a first-class state: with no provider reachable, every
linked player shows offline and the snapshot is flagged degraded.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

STATE_OFFLINE = "offline"
STATE_IN_LOBBY = "in_lobby"
STATE_IN_GAME = "in_game"

EVENT_LOBBY_OPENED = "lobby_opened"
EVENT_LOBBY_CLOSED = "lobby_closed"
EVENT_GAME_STARTED = "game_started"
EVENT_GAME_ENDED = "game_ended"

GRACE_MS = 15_000


class BindingsDatabase(Protocol):
    """Narrow seam over the registration profile bindings (#133)."""

    async def list_bindings_for_game(self, game_key: str) -> list[dict[str, Any]]:
        """List every profile binding of a game (user_id + profile_id)."""
        ...


class MatchEventStream(Protocol):
    """One provider's live event stream (GameProviderClient.stream_match_events)."""

    provider_id: str

    def stream_match_events(self, since: int) -> Any:
        """Async iterator of MatchEvent from the provider."""
        ...


class LiveAggregator:
    """Aggregate provider events into per-player dashboard snapshots."""

    def __init__(self, database: BindingsDatabase) -> None:
        """Wire the bindings seam; the player state starts empty."""
        self._db = database
        self._players: dict[str, dict[str, Any]] = {}
        self._degraded = True

    async def snapshot(self, game_key: str) -> dict[str, Any]:
        """Build the current dashboard snapshot for a game."""
        bindings = await self._db.list_bindings_for_game(game_key)
        now = _now_ms()
        players = []
        for binding in sorted(bindings, key=lambda b: str(b.get("user_id", ""))):
            user_id = str(binding.get("user_id", ""))
            profile_id = str(binding.get("profile_id", ""))
            state = self._players.get(profile_id, {"state": STATE_OFFLINE, "match_ref": "", "since": 0})
            if self._degraded:
                state = {"state": STATE_OFFLINE, "match_ref": "", "since": 0}
            players.append(
                {
                    "user_id": user_id,
                    "profile_id": profile_id,
                    "state": state["state"],
                    "match_ref": state["match_ref"],
                    "since": state["since"],
                }
            )
        return {"players": players, "generated_at": now, "degraded": self._degraded}

    async def apply_event(self, event: Any) -> None:
        """Fold one provider MatchEvent into the player state.

        lobby_opened/game_started set per-profile state; lobby_closed
        keeps the state for the AoE2 grace period (the game module's
        15 s) before downgrading — approximated here by applying the
        downgrade immediately on lobby_closed/game_ended for the
        dashboard, which is display-only; the ladder domain applies the
        real grace period (#134/#146).
        """
        self._degraded = False
        profile_state = {
            EVENT_LOBBY_OPENED: STATE_IN_LOBBY,
            EVENT_GAME_STARTED: STATE_IN_GAME,
            EVENT_LOBBY_CLOSED: STATE_OFFLINE,
            EVENT_GAME_ENDED: STATE_OFFLINE,
        }.get(event.type)
        if profile_state is None:
            return
        for profile_id in event.profile_ids:
            current = self._players.get(profile_id)
            if profile_state == STATE_OFFLINE and current is not None and current["state"] == STATE_IN_GAME:
                if event.type == EVENT_LOBBY_CLOSED and event.occurred_at - current["since"] < GRACE_MS:
                    continue
            self._players[profile_id] = {
                "state": profile_state,
                "match_ref": event.match_ref if profile_state != STATE_OFFLINE else "",
                "since": event.occurred_at,
            }

    def mark_degraded(self) -> None:
        """Flag the aggregated state as provider-less (all offline)."""
        self._degraded = True


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    return int(time.time() * 1000)
