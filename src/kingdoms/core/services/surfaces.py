"""Named surfaces & permission queries (kingdoms-services#130).

A **surface** is a mod-declared, named place the bot manages: a channel
category, resolved through the ChannelService and addressed by its
``mod:key`` logical key (never by raw Discord ID). The ladder needs:
play, leaderboard, matches, players, admins (kingdoms
docs/MODS/ladder).

Permission queries ("is ladder admin/player") stay on the platform side
and delegate to the existing seams: the runtime PermissionService
(component actions) and the mod-role resolver (logical role keys).
This service composes them into the two questions mods actually ask,
keeping mod code free of platform details.
"""

from __future__ import annotations

import logging
from typing import Protocol

from kingdoms.core.interfaces.platform import IChannel

logger = logging.getLogger("kingdoms.core.surfaces")


class SurfaceChannels(Protocol):
    """Narrow seam on the ChannelService: resolve a mod-scoped category."""

    async def get_channel_for_category(self, guild_id: str, category: str) -> IChannel:
        """Resolve a channel for a category key (``mod:key`` or platform key)."""
        ...


class SurfacePermissions(Protocol):
    """Narrow seam on the runtime PermissionService (kingdoms-services#55)."""

    async def user_has_role(self, guild_id: str, user_id: str, mod: str, role_key: str) -> bool:
        """Whether a member holds one mod role, resolved through the mapping."""
        ...


class SurfaceService:
    """Resolve named surfaces and answer surface-level permission queries."""

    def __init__(self, channels: SurfaceChannels, permissions: SurfacePermissions) -> None:
        """Wire the channel and permission seams."""
        self._channels = channels
        self._permissions = permissions

    async def surface_channel(self, guild_id: str, mod: str, surface_key: str) -> IChannel:
        """Resolve a mod's named surface to its channel (provisions on demand)."""
        return await self._channels.get_channel_for_category(guild_id, f"{mod}:{surface_key}")

    async def has_surface_role(self, guild_id: str, user_id: str, mod: str, role_key: str) -> bool:
        """Whether a user holds a mod role on this surface (e.g. ladder admin/player)."""
        try:
            return await self._permissions.user_has_role(guild_id, user_id, mod, role_key)
        except Exception:
            logger.warning(
                "SURFACE ROLE CHECK FAILED (guild %s, %s:%s) — denying", guild_id, mod, role_key, exc_info=True
            )
            return False
