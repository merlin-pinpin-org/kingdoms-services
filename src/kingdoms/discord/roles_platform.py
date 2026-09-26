"""Discord wiring for the platform roles (kingdoms-services#115).

Implements the :class:`~kingdoms.core.services.roles.RolesPlatform`
seam with real discord.py calls only — no business logic lives here.
"""

from __future__ import annotations

import logging

import discord

logger = logging.getLogger("kingdoms.roles.discord")


class DiscordRolesPlatform:
    """discord.py implementation of the roles platform seam."""

    def __init__(self, bot: discord.Client) -> None:
        """Keep a client reference for guild lookups and role creation."""
        self._bot = bot

    async def _guild(self, guild_id: str) -> discord.Guild | None:
        guild = self._bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        if guild is None and guild_id.isdigit():
            try:
                guild = await self._bot.fetch_guild(int(guild_id))
            except Exception:
                return None
        return guild

    async def find_role_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a guild role id by its exact name; None when absent."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        role = discord.utils.get(guild.roles, name=name)
        return str(role.id) if role is not None else None

    async def create_role(self, guild_id: str, name: str, reason: str) -> str:
        """Create a guild role; return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        role = await guild.create_role(name=name, reason=reason)
        return str(role.id)
