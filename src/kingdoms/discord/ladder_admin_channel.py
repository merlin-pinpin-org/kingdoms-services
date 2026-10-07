"""The ladder-admin channel: the ladder staff's pinned home (admin surface).

The admin channel (🛡-bot-admins) hosts the global bot surface; a seasonal
mod like the ladder wants the same shape for its staff: a dedicated channel
(🛡-ladder-admin) visible to the mod's staff roles (the per-season staff
roles) and the bot admins, hosting the pinned ladder admin menu — the same
view the /admin panel's Mods select serves, guarded at click time.

Everything follows the existing managed-channel + pinned-menu patterns:

- the channel is provisioned by ``ManagedChannelService`` (cache-aside:
  Redis → Mongo → adoption → creation), category ``mod_ladder_admin``;
- the policy restricts visibility to the guild's ladder staff roles
  (every "Staff ladder <season>" role) plus the bot-admins role — an
  unreadable channel for everyone else, like the admin channel;
- the pinned menu's message id is resolved through the message registry
  (restart-proof) with an in-memory fallback; a live registered menu is
  merely re-pinned, a gone one is rebuilt and re-registered — the home
  pin contract (no rebuild while it lives).

Reference: kingdoms-services#115 (admin channel), the pinned-menu service.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import discord

from kingdoms.core.services.pinned_menu import PinnedMenuChannel, PinnedMenuService

logger = logging.getLogger("kingdoms.ladder.admin_channel")

LADDER_ADMIN_CHANNEL_NAME = "🛡-ladder-admin"
LADDER_ADMIN_CATEGORY = "mod_ladder_admin"
LADDER_ADMIN_MARKER = "admin:ladder:pin:"
LADDER_ADMIN_MESSAGE_KEY = "ladder-admin-menu"
LADDER_ADMIN_MESSAGE_PLATFORM = "discord"
LADDER_STAFF_ROLE_PREFIX = "Staff ladder"


def build_ladder_admin_channel_service(bot: discord.Client, mongo_uri: str, redis_uri: str) -> Any | None:
    """Wire the 🛡-ladder-admin managed channel; None when stores are absent."""
    if not mongo_uri or not redis_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.managed_channel import ManagedChannelService
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.logs_platform import MongoLogsDatabase

        return ManagedChannelService(
            platform=_LadderAdminChannelPlatform(bot),
            database=MongoLogsDatabase(get_async_database()),
            category=LADDER_ADMIN_CATEGORY,
            name=LADDER_ADMIN_CHANNEL_NAME,
            state=StateService(redis_uri=redis_uri),
        )
    except Exception:
        logger.exception("LADDER ADMIN CHANNEL WIRING FAILED - ladder admin stays in the bot admin channel")
        return None


class _LadderAdminChannelPlatform:
    """discord.py seam: find, create, check, restrict the ladder-admin channel."""

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
        guild = await self._guild(guild_id)
        if guild is None:
            return None
        channel = discord.utils.get(guild.text_channels, name=name)
        return str(channel.id) if channel is not None else None

    async def create_channel(self, guild_id: str, name: str, reason: str) -> str:
        guild = await self._guild(guild_id)
        if guild is None:
            raise RuntimeError(f"guild {guild_id} not reachable")
        channel = await guild.create_text_channel(name, reason=reason)
        return str(channel.id)

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return False
        return guild.get_channel(int(channel_id)) is not None

    async def apply_policy(self, guild_id: str, channel_id: str) -> None:
        """Visibility: the ladder staff roles + bot-admins role, nothing else."""
        guild = await self._guild(guild_id)
        if guild is None or not channel_id.isdigit():
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        await channel.set_permissions(
            guild.me,
            overwrite=discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            ),
            reason="kingdoms: ladder admin channel bot access",
        )
        await channel.set_permissions(
            guild.default_role,
            overwrite=discord.PermissionOverwrite(view_channel=False),
            reason="kingdoms: ladder admin channel staff-only",
        )
        for role in guild.roles:
            if not role.name.startswith(LADDER_STAFF_ROLE_PREFIX) and role.name != "bot-admins":
                continue
            await channel.set_permissions(
                role,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, read_message_history=True, send_messages=True
                ),
                reason="kingdoms: ladder admin channel staff access",
            )

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        guild = await self._guild(guild_id)
        channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"ladder admin channel {channel_id} not reachable")
        message = await channel.send(view=layout)
        return str(message.id)


async def ensure_pinned_ladder_admin_menu(bot: discord.Client, guild_id: str) -> bool:
    """Ensure the ladder-admin channel holds its pinned menu; True when rebuilt.

    The message id rides the message registry (Mongo-backed, restart-proof)
    with an in-memory fallback: a live registered menu is merely re-pinned;
    only a gone message triggers a rebuild whose id replaces the
    registration — the home-menu contract.
    """
    service = getattr(bot, "ladder_admin_channel_service", None)
    if service is None:
        return False
    channel_id = await service.resolve_channel(guild_id)
    if channel_id is None:
        return False
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() and guild else None
    if channel is None or not hasattr(channel, "fetch_message") or not hasattr(channel, "send"):
        return False
    if await _registered_menu_lives(bot, guild_id, channel):
        return False

    class _ChannelDelivery:
        last_message_id: str | None = None

        async def deliver(self, channel: Any, layout: Any) -> str:
            message = await channel.send(view=layout)
            self.last_message_id = str(message.id)
            return self.last_message_id

    delivery = _ChannelDelivery()
    pinned = PinnedMenuService(delivery)
    created = await pinned.ensure(
        guild_id,
        cast(PinnedMenuChannel, channel),
        marker=LADDER_ADMIN_MARKER,
        build_layout=lambda guild: _build_layout(bot, guild),
        pin_reason="kingdoms: pinned ladder admin menu (ladder staff home)",
    )
    if not created:
        return False
    if delivery.last_message_id is not None:
        await _register_menu_message(bot, guild_id, str(channel_id), delivery.last_message_id)
    return True


def _build_layout(bot: discord.Client, guild_id: str) -> discord.ui.LayoutView:
    """Build the pinned ladder admin menu (the admin panel's ladder section).

    ``ladder_admin_entry`` reads only ``interaction.guild_id`` and
    ``interaction.client`` to resolve the guild's ladder, so a minimal
    shim suffices at build time; the rendered buttons are the same
    registered DynamicItems the /admin panel serves (click-time guards).
    """
    from kingdoms.discord.admin_panel_ladder import ladder_admin_entry

    return ladder_admin_entry(_PinInteraction(guild_id, bot))


class _PinInteraction:
    """Interaction shim: guild + client is all the entry view reads."""

    def __init__(self, guild_id: str, bot: Any) -> None:
        self.guild_id = int(guild_id) if guild_id.isdigit() else None
        self.client = bot


async def _registered_menu_lives(bot: discord.Client, guild_id: str, channel: Any) -> bool:
    """Whether the registered menu message still exists; re-pin when unpinned."""
    message_id = await _resolve_menu_message_id(bot, guild_id)
    if message_id is None:
        return False
    try:
        message = await channel.fetch_message(int(message_id))
    except Exception:
        return False
    try:
        await message.pin(reason="kingdoms: pinned ladder admin menu (ladder staff home)")
    except Exception:
        logger.warning("LADDER ADMIN MENU re-pin failed - best-effort", exc_info=True)
    return True


async def _resolve_menu_message_id(bot: discord.Client, guild_id: str) -> str | None:
    """Resolve the registered menu id (registry first, memory fallback)."""
    registry = getattr(bot, "message_registry", None)
    if registry is not None:
        try:
            registered = await registry.resolve(LADDER_ADMIN_MESSAGE_PLATFORM, LADDER_ADMIN_MESSAGE_KEY, guild_id)
            if registered is not None:
                return str(registered.message_id)
        except Exception:
            logger.warning("LADDER ADMIN MENU registry resolve failed - best-effort")
    store = getattr(bot, "_ladder_admin_menu_message_ids", None)
    if isinstance(store, dict):
        value = store.get(guild_id)
        return str(value) if value is not None else None
    return None


async def _register_menu_message(bot: discord.Client, guild_id: str, channel_id: str, message_id: str) -> None:
    """Persist the new menu id: memory fallback + registry (durable)."""
    store = getattr(bot, "_ladder_admin_menu_message_ids", None)
    if not isinstance(store, dict):
        store = {}
        bot._ladder_admin_menu_message_ids = store  # type: ignore[attr-defined]
    store[guild_id] = message_id
    registry = getattr(bot, "message_registry", None)
    if registry is None:
        return
    try:
        await registry.register(
            platform=LADDER_ADMIN_MESSAGE_PLATFORM,
            message_key=LADDER_ADMIN_MESSAGE_KEY,
            entity_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
        )
    except Exception:
        logger.warning("LADDER ADMIN MENU registry register failed - best-effort")


async def maintain_pinned_ladder_admin_menus(bot: discord.Client) -> None:
    """Keep the pinned ladder admin menu alive in every guild (self-healing)."""
    import asyncio

    await asyncio.sleep(10)
    while True:
        for guild in list(bot.guilds):
            try:
                await ensure_pinned_ladder_admin_menu(bot, str(guild.id))
            except Exception:
                logger.warning(
                    "PINNED LADDER ADMIN MENU check failed (guild %s) - best-effort", guild.id, exc_info=True
                )
        await asyncio.sleep(300)
