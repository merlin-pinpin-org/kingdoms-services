"""Core membership service: register/unregister, generic for every mod.

Every mod — seasonal or not — manages a **membership**: a member
registers (inscription), unregisters (désinscription), and the mod's
membership role follows (the season player role for a seasonal mod).
The flow is the same everywhere, so the core owns it once:

- the mod-specific registration lives behind the ``MembershipHooks``
  seam (what "registering" means is the mod's business);
- the core orchestrates: hook → role sync → season label resolution;
- a mod derives by binding its own hooks (the ladder binds its
  LadderService calls on its ladder id).

The Discord surface is generic too: one command factory registers the
``register``/``unregister`` subcommands on the mod's group, for every
mod, without duplicated wiring.

Reference: §0/§6 (the core never imports platform code), ADR-0020.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("kingdoms.core.membership")


class MembershipRoles(Protocol):
    """Narrow seam on the season roles the membership needs."""

    async def sync_player_role(self, guild_id: str, user_id: str, season: str, member: bool) -> None:
        """Add/remove the season membership role."""
        ...


@dataclass(frozen=True, slots=True)
class MembershipResult:
    """One membership outcome: ok, and a human-readable summary."""

    ok: bool
    summary: str


class MembershipHooks(Protocol):
    """Narrow seam: the mod-specific registration business."""

    async def register_member(self, guild_id: str, user_id: str, display_name: str) -> MembershipResult:
        """Register one member; the outcome never raises domain errors."""
        ...

    async def unregister_member(self, guild_id: str, user_id: str) -> MembershipResult:
        """Unregister one member; the outcome never raises domain errors."""
        ...


class MembershipSeasons(Protocol):
    """Narrow seam: the active season label provider (None = not seasonal)."""

    async def active_season(self, guild_id: str) -> str | None:
        """Return the active season label; None for a non-seasonal mod."""
        ...


class NullSeasons:
    """The non-seasonal default: no season, no season role sync."""

    async def active_season(self, guild_id: str) -> str | None:
        """Never seasonal."""
        return None


class MembershipService:
    """Orchestrate register/unregister: the mod hooks + the role sync.

    A seasonal mod syncs its membership role on every change (the
    season player role); a non-seasonal mod simply skips the role sync
    (a ``season_roles`` seam of None or a null season provider).
    """

    def __init__(
        self,
        mod_name: str,
        hooks: MembershipHooks,
        seasons: MembershipSeasons | None = None,
        season_roles: MembershipRoles | None = None,
    ) -> None:
        self._mod_name = mod_name
        self._hooks = hooks
        self._seasons = seasons if seasons is not None else NullSeasons()
        self._season_roles = season_roles

    @property
    def mod_name(self) -> str:
        """The mod this membership belongs to."""
        return self._mod_name

    async def register(self, guild_id: str, user_id: str, display_name: str) -> MembershipResult:
        """Register a member, then sync the membership role."""
        result = await self._hooks.register_member(guild_id, user_id, display_name)
        await self._sync_role(guild_id, user_id, member=True)
        return result

    async def unregister(self, guild_id: str, user_id: str) -> MembershipResult:
        """Unregister a member, then sync the membership role."""
        result = await self._hooks.unregister_member(guild_id, user_id)
        await self._sync_role(guild_id, user_id, member=False)
        return result

    async def _sync_role(self, guild_id: str, user_id: str, member: bool) -> None:
        """Sync the season membership role (best-effort, seasonal mods only)."""
        if self._season_roles is None:
            return
        season = await self._safe_season(guild_id)
        if season is None:
            return
        try:
            await self._season_roles.sync_player_role(guild_id, user_id, season, member=member)
        except Exception:
            logger.warning(
                "MEMBERSHIP role sync failed (mod %s, guild %s) — best-effort",
                self._mod_name,
                guild_id,
                exc_info=True,
            )

    async def _safe_season(self, guild_id: str) -> str | None:
        """Read the active season label (best-effort)."""
        try:
            return await self._seasons.active_season(guild_id)
        except Exception:
            logger.warning("MEMBERSHIP season lookup failed (mod %s) — best-effort", self._mod_name, exc_info=True)
            return None
