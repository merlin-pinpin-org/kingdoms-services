"""Discord wiring for the ChannelService platform seam (kingdoms-services#5).

Implements the :class:`~kingdoms.core.services.channel.ChannelsPlatform`
seam with real discord.py calls only — no business logic lives here.
"""

from __future__ import annotations

import logging
from typing import Any

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

    async def create_channel(self, guild_id: str, name: str, category_id: str | None = None) -> str:
        """Create a text channel (inside a category when given); return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        category: discord.CategoryChannel | None = None
        if category_id is not None and category_id.isdigit():
            found = guild.get_channel(int(category_id))
            category = found if isinstance(found, discord.CategoryChannel) else None
        suffix = f" (in {category.name})" if category is not None else ""
        channel = await guild.create_text_channel(
            name,
            reason=f"kingdoms: provision the {name} channel{suffix}",
            category=category,
        )
        return str(channel.id)

    async def find_category_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a Discord category by its name (slug-compared); None when absent.

        Discord stores category names slugged (lowercase, no accents,
        dashes) — see ``kingdom_setup._slug`` — so the lookup compares
        the normalized forms, never the raw display name.
        """
        from kingdoms.discord.kingdom_setup import _slug

        guild = await self._guild(guild_id)
        if guild is None:
            return None
        wanted = _slug(name)
        for category in guild.categories:
            if _slug(category.name) == wanted:
                return str(category.id)
        return None

    async def create_category(self, guild_id: str, name: str) -> str:
        """Create a Discord category channel; return its id."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        category = await guild.create_category(name, reason=f"kingdoms: provision the {name} category")
        return str(category.id)

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return False
        return guild.get_channel(int(channel_id)) is not None

    async def apply_access_policy(
        self, guild_id: str, channel_id: str, policy: dict[str, Any]
    ) -> None:
        """Apply a category's declared access policy as overwrites (#57).

        The bot overwrite is set first so the bot can never lock itself
        out; then @everyone, then the declared roles (resolved by name
        through the guild's role list — logical keys map to role names).
        """
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        if bool(policy.get("bot_overwrite", True)):
            await channel.set_permissions(
                guild.me,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
                ),
                reason="kingdoms: access policy bot overwrite",
            )
        everyone_view = bool(policy.get("everyone_view", True))
        everyone_post = bool(policy.get("everyone_post", False))
        await channel.set_permissions(
            guild.default_role,
            overwrite=discord.PermissionOverwrite(
                view_channel=everyone_view,
                send_messages=everyone_post if not everyone_view else None,
                read_message_history=everyone_view,
            ),
            reason="kingdoms: access policy everyone overwrite",
        )
        role_keys: list[str] = list(policy.get("view") or ()) + list(policy.get("post") or ())
        for role_key in role_keys:
            role = discord.utils.get(guild.roles, name=role_key)
            if role is None:
                continue
            await channel.set_permissions(
                role,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=role_key in (policy.get("post") or ()),
                    read_message_history=True,
                ),
                reason="kingdoms: access policy role overwrite",
            )

    async def get_channel_overwrites(
        self, guild_id: str, channel_id: str
    ) -> dict[str, dict[str, bool]] | None:
        """Actual permission overwrites: target id -> {permission: bool}."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return None
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return None
        result: dict[str, dict[str, bool]] = {}
        for target, overwrite in channel.overwrites.items():
            key = "@everyone" if target == guild.default_role else str(target.id)
            allow, deny = overwrite.pair()
            permissions: dict[str, bool] = {}
            if allow.view_channel:
                permissions["view_channel"] = True
            if deny.view_channel:
                permissions["view_channel"] = False
            if allow.send_messages:
                permissions["send_messages"] = True
            if deny.send_messages:
                permissions["send_messages"] = False
            result[key] = permissions
        return result
