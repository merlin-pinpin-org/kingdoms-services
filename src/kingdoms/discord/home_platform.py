"""discord.py platform seam for the 🏛-kingdoms-home managed channel.

The home channel is the guild's front door: public by design (the
pinned menu's buttons answer ephemerally, guarded at click time), so
unlike the admin channel its policy applies no visibility restriction —
a read of the home grants nothing.
"""
from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.home.platform")


class DiscordHomeChannelPlatform:
    """discord.py implementation of the managed-channel seam (public policy)."""

    def __init__(self, bot: discord.Client) -> None:
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

    async def create_channel(self, guild_id: str, name: str, reason: str) -> str:
        """Create the channel; return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = await guild.create_text_channel(name, reason=reason)
        return str(channel.id)

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return False
        return guild.get_channel(int(channel_id)) is not None

    async def apply_policy(self, guild_id: str, channel_id: str) -> None:
        """Apply the pinned read-only default: public to read, closed to write."""
        from kingdoms.discord.pinned_views import apply_read_only_policy

        guild = await self._guild(guild_id)
        channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
        await apply_read_only_policy(guild, channel, "home", guild_id)

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        """Send a layout (a discord.py view) to the channel; the message id."""
        guild = await self._guild(guild_id)
        channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"home channel {channel_id} not reachable")
        message = await channel.send(view=layout)
        return str(message.id)
