"""Core season-roles service: per-season membership roles for every mod.

A seasonal mod (the ladder) wants the **same pair of roles every
season**: one for its members ("Joueur ladder s1") and one for its
staff ("Staff ladder s1") — the staff can act as an admin inside the
mod's scope. Every mod wants the same thing, so the core owns the
shape once:

- a logical role key is namespaced per season:
  ``player:s1`` and ``staff:s1`` under the mod's role keys;
- the mapping is persisted through the existing ``ModRolesService``
  (logical key → platform role id, admin-rebindable);
- display names are formatted from the mod's declared templates:
  ``{role} {mod} {season}`` — e.g. "Joueur ladder s1".

The core never decides *when* a player gets the role: the mod emits
the intent (registered / unregistered / staffed), a platform wiring
calls ``sync_player_role`` / ``sync_staff_role``. Season end is the
mod's business too — the roles of a past season simply stop being
assigned.

Reference: §0/§6 (the core never imports platform code), ADR-0020.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kingdoms.core.services.mod_definition import RoleDef

logger = logging.getLogger("kingdoms.core.season_roles")

PLAYER_ROLE_KEY = "player"
STAFF_ROLE_KEY = "staff"


class SeasonRolesMembers(Protocol):
    """Narrow seam on the mod-roles service the season roles need."""

    async def resolve_role_id(self, guild_id: str, mod: str, role_key: str) -> str | None:
        """Resolve one logical role key; None when unmapped."""
        ...

    async def adopt_or_create_role(self, guild_id: str, mod_name: str, role_def: RoleDef) -> str | None:
        """Adopt or create the platform role; its id, None on failure."""
        ...

    async def assign_platform_role(self, guild_id: str, user_id: str, role_id: str, mod: str, role_key: str) -> None:
        """Add one platform role to a member (best-effort)."""
        ...

    async def remove_platform_role(self, guild_id: str, user_id: str, role_id: str, mod: str, role_key: str) -> None:
        """Remove one platform role from a member (best-effort)."""
        ...


class SeasonRolesService:
    """Provision and sync the per-season player/staff roles of a mod."""

    def __init__(self, mod_roles: SeasonRolesMembers, mod_name: str) -> None:
        self._mod_roles = mod_roles
        self._mod_name = mod_name

    def role_key(self, kind: str, season: str) -> str:
        """Build the logical role key for one season (player/staff)."""
        if kind not in (PLAYER_ROLE_KEY, STAFF_ROLE_KEY):
            raise ValueError(f"unknown season role kind: {kind!r}")
        return f"{kind}:{season}"

    def display_name(self, kind: str, season: str) -> str:
        """Format the platform display name: e.g. 'Joueur ladder s1'."""
        kind_label = "Joueur" if kind == PLAYER_ROLE_KEY else "Staff"
        return f"{kind_label} {self._mod_name} {season}"

    async def ensure_role(self, guild_id: str, kind: str, season: str) -> str | None:
        """Provision one season role on demand; the platform role id.

        The role is created (or adopted by name) only when missing —
        the same idempotence as the declared mod roles, but for keys
        a season invents at runtime.
        """
        role_key = self.role_key(kind, season)
        resolved = await self._mod_roles.resolve_role_id(guild_id, self._mod_name, role_key)
        if resolved is not None:
            return resolved
        role_def = RoleDef(key=role_key, display_name=self.display_name(kind, season))
        adopted = await self._mod_roles.adopt_or_create_role(guild_id, self._mod_name, role_def)
        if adopted is None:
            logger.warning("SEASON ROLE provisioning failed (%s:%s)", self._mod_name, role_key)
        return adopted

    async def sync_player_role(self, guild_id: str, user_id: str, season: str, member: bool) -> None:
        """Add/remove the season player role to reflect membership."""
        await self._sync(guild_id, user_id, PLAYER_ROLE_KEY, season, member)

    async def sync_staff_role(self, guild_id: str, user_id: str, season: str, member: bool) -> None:
        """Add/remove the season staff role."""
        await self._sync(guild_id, user_id, STAFF_ROLE_KEY, season, member)

    async def _sync(self, guild_id: str, user_id: str, kind: str, season: str, member: bool) -> None:
        role_id = await self.ensure_role(guild_id, kind, season)
        if role_id is None:
            return
        if member:
            await self._mod_roles.assign_platform_role(guild_id, user_id, role_id, self._mod_name, kind)
        else:
            await self._mod_roles.remove_platform_role(guild_id, user_id, role_id, self._mod_name, kind)


def season_label(season: Any) -> str:
    """Extract the short season label (e.g. 's1') from a season document."""
    if isinstance(season, str):
        return season
    if isinstance(season, dict):
        for key in ("label", "short_name", "name", "season_id", "id"):
            value = season.get(key)
            if isinstance(value, str) and value:
                return value
    return str(season)
