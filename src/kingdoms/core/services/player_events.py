"""Player status events: the generic mod notification mechanism.

Game- and provider-agnostic (reference §0): when a player's live status
changes (enters a lobby, a game starts, a game ends, a lobby closes),
every mod in which that player is enrolled must be notified. This
module owns the mechanism once:

- ``PlayerStatusEvent`` — one normalized transition (game_key,
  profile_id, user_id, old/new status, match reference and link);
- ``PlayerStatusSink`` — what a mod implements to receive transitions;
- ``ModMembership`` — the seam a mod implements so the router can ask
  "is this user enrolled in your mod?";
- ``PlayerStatusRouter`` — the registry + dispatcher: mods register,
  transitions are dispatched to every mod where the player is
  enrolled, best-effort (one mod's failure never blocks the others).

The router lives in the core so both processes reuse it: svc-core
dispatches provider-derived transitions to core-registered sinks, and
the bot feeds the same router from the Live event stream so Discord
surfaces (mod channels) receive them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("kingdoms.core.player_events")

STATUS_OFFLINE = "offline"
STATUS_IN_LOBBY = "in_lobby"
STATUS_IN_GAME = "in_game"

EVENT_STATUS_CHANGED = "player.status_changed"


@dataclass(frozen=True, slots=True)
class PlayerStatusEvent:
    """One player status transition, normalized and game-agnostic."""

    game_key: str
    profile_id: str
    user_id: str
    old_status: str
    new_status: str
    match_ref: str
    match_url: str
    occurred_at: int


class PlayerStatusSink(Protocol):
    """What a mod implements to receive player status transitions."""

    async def on_player_status(self, event: PlayerStatusEvent) -> None:
        """Handle one transition (best-effort: never raises)."""
        ...


class ModMembership(Protocol):
    """The seam a mod implements for enrollment lookups."""

    async def is_enrolled(self, game_key: str, user_id: str) -> bool:
        """Report whether the user is enrolled in the mod for the game."""
        ...


class PlayerStatusRouter:
    """Dispatch player status transitions to the enrolled mods' sinks."""

    def __init__(self) -> None:
        """Start with no mod registered."""
        self._mods: dict[str, tuple[ModMembership, PlayerStatusSink]] = {}

    def register_mod(self, mod_name: str, membership: ModMembership, sink: PlayerStatusSink) -> None:
        """Register one mod's membership seam and notification sink."""
        self._mods[mod_name] = (membership, sink)

    def unregister_mod(self, mod_name: str) -> None:
        """Drop one mod's registration (idempotent)."""
        self._mods.pop(mod_name, None)

    @property
    def mod_names(self) -> tuple[str, ...]:
        """The registered mod names (stable order: registration order)."""
        return tuple(self._mods)

    async def dispatch(self, event: PlayerStatusEvent) -> list[str]:
        """Notify every mod where the player is enrolled; return their names.

        Best-effort per mod: a lookup or sink failure is logged and
        skipped — one mod's outage never blocks the other notifications.
        """
        notified: list[str] = []
        for mod_name, (membership, sink) in self._mods.items():
            try:
                if not await membership.is_enrolled(event.game_key, event.user_id):
                    continue
                await sink.on_player_status(event)
                notified.append(mod_name)
            except Exception:
                logger.warning(
                    "player status dispatch to mod %s failed (player %s, game %s) — best-effort",
                    mod_name,
                    event.user_id,
                    event.game_key,
                    exc_info=True,
                )
        return notified
