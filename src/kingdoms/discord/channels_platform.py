"""Discord wiring for the ChannelService platform seam (kingdoms-services#5).

Implements the :class:`~kingdoms.core.services.channel.ChannelsPlatform`
seam with real discord.py calls only — no business logic lives here.
"""

from __future__ import annotations

import logging

import discord

logger = logging.getLogger("kingdoms.channels.discord")


class DiscordChannelsPlatform:
    """discord.py implementation of the channels platform seam."""

    def __init__(self, bot: discord.Client) -> None:
        """Keep a client reference for guild and channel lookups."""
        self._bot = bot

    async def _guild(self, guild_id: str) -> discord.Guild | None:
        guild = self._bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        if guild is None and guild_id.isdigit():
            try:
                guild = await self._bot.fetch_guild(int(guild_id))
            except Exception:
                return None
        return guild

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a guild channel id by its exact name; None when absent."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        channel = discord.utils.get(guild.text_channels, name=name)
        return str(channel.id) if channel is not None else None

    async def create_channel(self, guild_id: str, name: str) -> str:
        """Create a text channel; return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = await guild.create_text_channel(name, reason=f"kingdoms: provision the {name} channel")
        return str(channel.id)

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return False
        return guild.get_channel(int(channel_id)) is not None
