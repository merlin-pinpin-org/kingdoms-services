"""Discord wiring for the platform roles (kingdoms-services#115).

Implements the :class:`~kingdoms.core.services.roles.RolesPlatform`
seam with real discord.py calls only — no business logic lives here.
"""

from __future__ import annotations

import logging
from typing import Any

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


class DiscordAdminChannelPlatform:
    """discord.py implementation of the admin channel platform seam.

    The 🛡-bot-admins channel is visible to the bot-admins role and
    guild administrators only; the BOT_ADMINS are synced into the role
    for transparency — their privileges never depend on it (the guards
    check BOT_ADMINS first, environment-sourced).
    """

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
        if guild is None:
            return False
        return guild.get_channel(int(channel_id)) is not None if channel_id.isdigit() else False

    async def sync_admin_role_members(
        self,
        guild_id: str,
        role_id: str,
        admin_ids: tuple[str, ...],
    ) -> None:
        """Ensure every BOT_ADMIN holds the role (add, never remove).

        The transparency direction is one-way: an operator manually
        stripped from the role is not re-added here (the sync runs at
        provisioning and resolution); their privileges stay — the
        guards read BOT_ADMINS first.
        """
        guild = await self._guild(guild_id)
        if guild is None or not role_id.isdigit():
            return
        role = guild.get_role(int(role_id))
        if role is None:
            return
        for admin_id in admin_ids:
            if not admin_id.isdigit():
                continue
            member = guild.get_member(int(admin_id))
            if member is None:
                continue
            if role not in member.roles:
                try:
                    await member.add_roles(role, reason="kingdoms: BOT_ADMINS transparency sync")
                except Exception:
                    logger.warning(
                        "BOT-ADMINS ROLE SYNC FAILED (guild %s, admin %s) — best-effort",
                        guild_id,
                        admin_id,
                    )

    async def apply_channel_policy(self, guild_id: str, channel_id: str, role_id: str) -> None:
        """Admin-channel visibility: the role, guild admins and the bot."""
        guild = await self._guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            return
        bot_overwrite = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
        )
        role_overwrite = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True
        )
        everyone_overwrite = discord.PermissionOverwrite(view_channel=False)
        await channel.set_permissions(guild.me, overwrite=bot_overwrite, reason="kingdoms: admin channel bot access")
        await channel.set_permissions(
            guild.default_role, overwrite=everyone_overwrite, reason="kingdoms: admin channel role-only"
        )
        if role_id.isdigit():
            role = guild.get_role(int(role_id))
            if role is not None:
                await channel.set_permissions(
                    role, overwrite=role_overwrite, reason="kingdoms: admin channel role access"
                )

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        """Deliver a UI SDK layout to the channel; the message id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"channel {channel_id} is not a text channel")
        message = await channel.send(view=layout)
        return str(message.id)
