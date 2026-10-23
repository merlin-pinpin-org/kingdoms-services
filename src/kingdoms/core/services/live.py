"""Live dashboard aggregator: provider events → per-player state (#147).

svc-core consumes the providers' ``StreamMatchEvents`` (ext-aoe2lobby
live layer, ext-librematch the source of truth), resolves profile ids
to platform users through the registration bindings, and maintains the
per-player dashboard state served over ``kingdoms.v1.Live``.

Every real status change also emits a normalized ``PlayerStatusEvent``
(the generic, game-agnostic transition) into an internal queue — the
``WatchPlayerEvents`` stream serves it to the bot, which dispatches it
to the mods where the player is enrolled (reference: player_events
module). Degradation is a first-class state: with no provider
reachable, every linked player shows offline and the snapshot is
flagged degraded.
"""

from __future__ import annotations

import asyncio
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

_EVENT_STATE = {
    EVENT_LOBBY_OPENED: STATE_IN_LOBBY,
    EVENT_GAME_STARTED: STATE_IN_GAME,
    EVENT_LOBBY_CLOSED: STATE_OFFLINE,
    EVENT_GAME_ENDED: STATE_OFFLINE,
}


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

    def __init__(self, database: BindingsDatabase, game_key: str = "aoe2") -> None:
        """Wire the bindings seam; the player state starts empty."""
        self._db = database
        self._game_key = game_key
        self._players: dict[str, dict[str, Any]] = {}
        self._degraded = True
        self._profile_to_user: dict[str, str] = {}
        self._transitions: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def refresh_bindings(self) -> None:
        """Reload the profile→user resolution from the bindings store."""
        bindings = await self._db.list_bindings_for_game(self._game_key)
        self._profile_to_user = {
            str(b.get("profile_id")): str(b.get("user_id")) for b in bindings
        }

    def profile_ids(self) -> tuple[str, ...]:
        """Return the bound profile ids — the provider's subscription list."""
        return tuple(sorted(self._profile_to_user))

    def user_ids(self) -> list[str]:
        """Return the profile owners, aligned with the sorted profile ids.

        The game service's roster needs each profile's platform user —
        the dashboard groups by account; the ids ride the enrollments
        push parallel to the profile ids.
        """
        return [self._profile_to_user[pid] for pid in sorted(self._profile_to_user)]

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
        15 s) before downgrading. Every effective change also queues a
        normalized ``PlayerStatusEvent`` for the Live event stream.
        """
        self._degraded = False
        profile_state = _EVENT_STATE.get(event.type)
        if profile_state is None:
            return
        for profile_id in event.profile_ids:
            current = self._players.get(profile_id)
            if profile_state == STATE_OFFLINE and current is not None and current["state"] == STATE_IN_GAME:
                if event.type == EVENT_LOBBY_CLOSED and event.occurred_at - current["since"] < GRACE_MS:
                    continue
            new_state = {
                "state": profile_state,
                "match_ref": event.match_ref if profile_state != STATE_OFFLINE else "",
                "since": event.occurred_at,
            }
            self._players[profile_id] = new_state
            old_state = current["state"] if current else STATE_OFFLINE
            if old_state != profile_state or (current and current["match_ref"] != new_state["match_ref"]):
                self._queue_transition(profile_id, old_state, profile_state, event)

    def _queue_transition(
        self, profile_id: str, old_state: str, new_state: str, event: Any
    ) -> None:
        """Queue one normalized transition for the player-event stream."""
        metadata = dict(getattr(event, "metadata", ()) or {})
        self._transitions.put_nowait(
            {
                "game_key": self._game_key,
                "profile_id": profile_id,
                "user_id": self._profile_to_user.get(profile_id, ""),
                "old_status": old_state,
                "new_status": new_state,
                "match_ref": event.match_ref if new_state != STATE_OFFLINE else "",
                "match_url": str(metadata.get("match_url", "")),
                "occurred_at": event.occurred_at,
            }
        )

    async def next_transition(self) -> dict[str, Any]:
        """Await the next normalized transition (WatchPlayerEvents)."""
        return await self._transitions.get()

    def pending_transitions(self) -> int:
        """Report the number of queued transitions (diagnostics and tests)."""
        return self._transitions.qsize()

    def mark_degraded(self) -> None:
        """Flag the aggregated state as provider-less (all offline)."""
        self._degraded = True

    def mark_healthy(self) -> None:
        """Clear the degraded flag once a provider stream is live."""
        self._degraded = False


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    return int(time.time() * 1000)
