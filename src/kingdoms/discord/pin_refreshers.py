"""The pinned surfaces' refreshers: pins re-render when their inputs change.

A pinned view must update itself when what it shows changes — a
parameter (or sub-parameter), the guild locale, the enrollments state.
Each surface registers its refresher here (the factory wires them at
setup); the admin handlers call :func:`refresh_registered_pins` after
every mutation, and every refresher edits the pin **in place** (a pin
never moves, never duplicates).
"""

from __future__ import annotations

import logging
from typing import Any

import discord

import kingdoms.discord.pinned_views as pinned_views

logger = logging.getLogger("kingdoms.pin_refreshers")


async def _refresh_admin_pin(bot: Any, guild_id: str) -> None:
    """Re-render the pinned admin menu in place."""
    logs_service = getattr(bot, "logs_service", None)
    admin_channel_service = getattr(bot, "admin_channel_service", None)
    catalog = getattr(bot, "messages", None)
    if logs_service is None or admin_channel_service is None:
        return
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return
    from kingdoms.discord.admin_panel_dynamic import build_pin_main_menu
    from kingdoms.discord.admin_panel_pin import PINNED_MENU_CUSTOM_ID

    async def _build(gid: str) -> discord.ui.LayoutView:
        locale = "en"
        try:
            locale = await logs_service.get_locale(gid)
        except Exception:
            locale = "en"
        return await build_pin_main_menu(logs_service, gid, catalog, locale, admin_channel_service)

    await pinned_views.refresh_pin_by_marker(bot, guild, PINNED_MENU_CUSTOM_ID, _build)


async def _refresh_home_pin(bot: Any, guild_id: str) -> None:
    """Re-render the pinned home menu in place (registry-addressed)."""
    registry = getattr(bot, "message_registry", None)
    if registry is None:
        return
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return
    from kingdoms.discord.home import HOME_MESSAGE_KEY, HOME_MESSAGE_PLATFORM
    from kingdoms.discord.ladder_channels import _ladder_flags
    from kingdoms.discord.ladder_home import build_ladder_menu_layout

    try:
        registered = await registry.resolve(HOME_MESSAGE_PLATFORM, HOME_MESSAGE_KEY, guild_id)
        if registered is None:
            return
        channel = guild.get_channel(int(registered.channel_id))
        message = await channel.fetch_message(int(registered.message_id))
        enrollments_open, queue_paused = await _ladder_flags(guild_id)
        await message.edit(view=build_ladder_menu_layout(enrollments_open, queue_paused))
    except Exception:
        logger.warning("home pin refresh failed — best-effort", exc_info=True)


async def _refresh_ladder_root_pin(bot: Any, guild_id: str) -> None:
    """Re-render the pinned ladder root (lifecycle) panel in place."""
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return
    from kingdoms.discord.mod_admin_channels import ModAdminPinInteraction

    async def _build(gid: str) -> discord.ui.LayoutView:
        from kingdoms.discord.admin_panel_ladder import ladder_mod_admin_view

        return await ladder_mod_admin_view(ModAdminPinInteraction(gid, bot))

    await pinned_views.refresh_pin_by_marker(bot, guild, "admin:pin:modladder", _build)


async def _refresh_season_salons(bot: Any, guild_id: str) -> None:
    """Re-render the season salons (config pin + dashboard & co) in place."""
    from kingdoms.discord.ladder_channels import ladder_channels_wiring_ready, sync_ladder_channels

    if not ladder_channels_wiring_ready():
        return
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return
    await sync_ladder_channels(guild, bot)


def register_pin_refreshers(bot: Any) -> None:
    """Register every pinned surface's refresher (the factory hookup)."""

    async def _admin(guild_id: str) -> None:
        await _refresh_admin_pin(bot, guild_id)

    async def _home(guild_id: str) -> None:
        await _refresh_home_pin(bot, guild_id)

    async def _ladder_root(guild_id: str) -> None:
        await _refresh_ladder_root_pin(bot, guild_id)

    async def _season(guild_id: str) -> None:
        await _refresh_season_salons(bot, guild_id)

    pinned_views.register_pin_refresher("admin", _admin)
    pinned_views.register_pin_refresher("home", _home)
    pinned_views.register_pin_refresher("ladder-root", _ladder_root)
    pinned_views.register_pin_refresher("season-salons", _season)
