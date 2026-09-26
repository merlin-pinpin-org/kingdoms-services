"""Discord wiring for the bot logs channel (kingdoms-services#109).

Implements the two seams the core :class:`LogService` depends on, with
real infrastructure only — no business logic lives here:

- :class:`MongoLogsDatabase` — the async MongoDB persistence of
  ``ChannelModel`` documents and per-guild access policies;
- :class:`DiscordLogsPlatform` — channel creation (``🤖-bot-logs``),
  the admin-only default permission overwrites, role grants and message
  delivery, all through discord.py.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.logs import (
    BOT_LOGS_CATEGORY,
    BOT_LOGS_CHANNEL_NAME,
    CHANNELS_COLLECTION,
    GUILD_SETTINGS_COLLECTION,
    POLICIES_COLLECTION,
)

logger = logging.getLogger("kingdoms.logs.discord")


class MongoLogsDatabase:
    """Async MongoDB persistence for the logs channel and its policies."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (get_async_database)."""
        self._channels = database[CHANNELS_COLLECTION]
        self._policies = database[POLICIES_COLLECTION]
        self._settings = database[GUILD_SETTINGS_COLLECTION]

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        """Find the persisted channel document for a guild category."""
        document = await self._channels.find_one({"_id": f"{guild_id}:{category}"})
        return ChannelModel.from_mongo(document) if document else None

    async def upsert_channel(self, channel: ChannelModel) -> None:
        """Insert or replace the channel document (idempotent provisioning)."""
        await self._channels.replace_one(
            {"_id": channel.id},
            channel.to_mongo(),
            upsert=True,
        )

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        """Drop a stale channel document; True when one was removed."""
        result = await self._channels.delete_one({"_id": f"{guild_id}:{category}"})
        return bool(result.deleted_count > 0)

    async def get_policy(self, guild_id: str, category: str) -> dict[str, Any] | None:
        """Read the persisted access policy for a guild category."""
        policy = await self._policies.find_one({"_id": f"{guild_id}:{category}"})
        return dict(policy) if policy else None

    async def set_policy(self, guild_id: str, category: str, policy: dict[str, Any]) -> None:
        """Persist the access policy (upsert, audit fields included)."""
        await self._policies.replace_one(
            {"_id": f"{guild_id}:{category}"},
            {**policy, "_id": f"{guild_id}:{category}"},
            upsert=True,
        )

    async def get_guild_settings(self, guild_id: str) -> dict[str, Any] | None:
        """Read the persisted per-guild settings (locale, ...)."""
        document = await self._settings.find_one({"_id": guild_id})
        return dict(document) if document else None

    async def set_guild_settings(self, guild_id: str, settings: dict[str, Any]) -> None:
        """Persist the per-guild settings (upsert, audit fields included)."""
        await self._settings.replace_one(
            {"_id": guild_id},
            {**settings, "_id": guild_id},
            upsert=True,
        )


class DiscordLogsPlatform:
    """discord.py implementation of the logs platform seam."""

    def __init__(self, bot: discord.Client) -> None:
        """Keep a client reference for guild lookups and message sends."""
        self._bot = bot

    async def _guild(self, guild_id: str) -> discord.Guild | None:
        guild = self._bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        if guild is None and guild_id.isdigit():
            try:
                guild = await self._bot.fetch_guild(int(guild_id))
            except Exception:
                return None
        return guild

    async def find_logs_channel(self, guild_id: str) -> str | None:
        """Find an existing 🤖-bot-logs channel by name (adoption)."""
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        for channel in guild.text_channels:
            if channel.name == BOT_LOGS_CHANNEL_NAME:
                return str(channel.id)
        return None

    async def create_logs_channel(self, guild_id: str) -> str:
        """Create the 🤖-bot-logs text channel in the guild."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = await guild.create_text_channel(
            BOT_LOGS_CHANNEL_NAME, reason="Kingdoms bot logs (kingdoms-services#109)"
        )
        return str(channel.id)

    async def apply_default_policy(self, guild_id: str, channel_id: str) -> None:
        """Admin-only default: @everyone denied, guild admins allowed."""
        guild = await self._guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            return
        overwrite_everyone = discord.PermissionOverwrite(view_channel=False, send_messages=False)
        overwrite_admins = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        # Self-allow first: the actor must view the channel to edit its
        # overwrites — denying @everyone first would lock the bot out of its
        # own channel.
        await channel.set_permissions(guild.me, overwrite=overwrite_admins, reason="bot logs: bot access")
        await channel.set_permissions(
            guild.default_role, overwrite=overwrite_everyone, reason="bot logs: admin-only default"
        )
        logger.info("BOT LOGS default policy applied: guild=%s channel=%s", guild_id, channel_id)

    async def apply_public_policy(self, guild_id: str, channel_id: str) -> None:
        """Public visibility: @everyone may view, the bot keeps its rights."""
        guild = await self._guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            return
        overwrite_everyone = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
        overwrite_admins = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        await channel.set_permissions(guild.me, overwrite=overwrite_admins, reason="bot logs: bot access")
        await channel.set_permissions(
            guild.default_role, overwrite=overwrite_everyone, reason="bot logs: public visibility (/admin)"
        )
        logger.info("BOT LOGS public policy applied: guild=%s channel=%s", guild_id, channel_id)

    async def list_text_channels(self, guild_id: str) -> list[dict[str, str]]:
        """List the guild's text channels (id, name), name-sorted."""
        guild = await self._guild(guild_id)
        if guild is None:
            return []
        return [
            {"id": str(channel.id), "name": channel.name}
            for channel in sorted(guild.text_channels, key=lambda c: c.name)
        ]

    async def grant_role_view(self, guild_id: str, channel_id: str, role_id: str) -> None:
        """Grant a role view access on the logs channel."""
        guild = await self._guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        role = guild.get_role(int(role_id)) if role_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel) or role is None:
            return
        overwrite = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
        await channel.set_permissions(role, overwrite=overwrite, reason="bot logs: role granted view (/admin)")

    async def send_log_message(self, guild_id: str, channel_id: str, content: str, layout: Any = None) -> None:
        """Deliver one lifecycle event to the logs channel (layout optional)."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"channel {channel_id} is not a text channel")
        if layout is not None:
            await channel.send(view=layout)
            return
        await channel.send(content=content)

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the persisted channel still exists on the platform."""
        guild = await self._guild(guild_id)
        if guild is None:
            return False
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if isinstance(channel, discord.TextChannel):
            return True
        try:
            fetched = await self._bot.fetch_channel(int(channel_id))
        except Exception:
            return False
        return isinstance(fetched, discord.TextChannel)


def logs_category() -> str:
    """Return the logs category key (kept for wiring readability)."""
    return BOT_LOGS_CATEGORY
