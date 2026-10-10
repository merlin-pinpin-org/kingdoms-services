"""Discord wiring for the platform roles (kingdoms-services#115).

Implements the :class:`~kingdoms.core.services.roles.RolesPlatform`
seam with real discord.py calls only — no business logic lives here.
The :class:`MongoRolesDatabase` (kingdoms-services#26) persists the
mod-role mappings; the member-assignment seam serves ModRolesService.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.core.models.role_mapping import RoleMappingModel
from kingdoms.core.services.roles import ROLE_MAPPINGS_COLLECTION

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

    async def add_role_to_member(self, guild_id: str, user_id: str, role_id: str, reason: str) -> None:
        """Add a role to a member (audited through ``reason``)."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        role = guild.get_role(int(role_id))
        if role is None:
            raise LookupError(f"role {role_id} not found in guild {guild_id}")
        await member.add_roles(role, reason=reason)

    async def remove_role_from_member(self, guild_id: str, user_id: str, role_id: str, reason: str) -> None:
        """Remove a role from a member (audited through ``reason``)."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        role = guild.get_role(int(role_id))
        if role is None:
            raise LookupError(f"role {role_id} not found in guild {guild_id}")
        await member.remove_roles(role, reason=reason)

    async def get_member_role_ids(self, guild_id: str, user_id: str) -> list[str]:
        """Read a member's live platform role ids (empty when absent)."""
        guild = await self._guild(guild_id)
        if guild is None:
            return []
        member = guild.get_member(int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except Exception:
                return []
        return [str(role.id) for role in member.roles]


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
        from kingdoms.discord.pinned_views import get_pinned_read_only

        read_only = True
        try:
            read_only = await get_pinned_read_only(guild_id, "admin")
        except Exception:
            read_only = True
        role_overwrite = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=not read_only,
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

    async def edit_layout(self, guild_id: str, channel_id: str, message_id: str, layout: Any) -> None:
        """Edit one delivered layout message in place."""
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"channel {channel_id} is not a text channel")
        if not message_id.isdigit():
            raise RuntimeError(f"message id {message_id} is not numeric")
        await channel.get_partial_message(int(message_id)).edit(view=layout)


class MongoRolesDatabase:
    """Async MongoDB persistence for the mod-role mappings (#26)."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (get_async_database)."""
        self._mappings = database[ROLE_MAPPINGS_COLLECTION]

    async def find_role_mapping(self, guild_id: str, mod: str, role_key: str) -> RoleMappingModel | None:
        """Find the persisted mapping for a guild mod role key."""
        document = await self._mappings.find_one({"_id": f"{guild_id}:{mod}:{role_key}"})
        return RoleMappingModel.from_mongo(document) if document else None

    async def list_role_mappings(self, guild_id: str, mod: str) -> list[RoleMappingModel]:
        """List every persisted mapping of one guild mod (incl. per-season)."""
        cursor = self._mappings.find({"_id": {"$regex": f"^{guild_id}:{mod}:"}})
        return [RoleMappingModel.from_mongo(doc) async for doc in cursor]

    async def upsert_role_mapping(self, mapping: RoleMappingModel) -> None:
        """Insert or replace the role mapping document (idempotent)."""
        await self._mappings.replace_one({"_id": mapping.id}, mapping.to_mongo(), upsert=True)

    async def delete_role_mapping(self, guild_id: str, mod: str, role_key: str) -> bool:
        """Drop a stale role mapping document; True when one was removed."""
        result = await self._mappings.delete_one({"_id": f"{guild_id}:{mod}:{role_key}"})
        return bool(result.deleted_count > 0)
