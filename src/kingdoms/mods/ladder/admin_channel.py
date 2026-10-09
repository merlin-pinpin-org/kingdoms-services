"""The ladder's admin surfaces, registered on the core mod-admin mechanism.

The core owns the generic pattern — a root admin channel per mod
(cross-season, hosting the pinned lifecycle panel) and, for seasonal
mods, a per-season admin salon inside the season's category (hosting
the pinned config panel): see :mod:`kingdoms.discord.mod_admin_channels`.

The ladder only declares its :data:`LADDER_ADMIN_CHANNEL_SPEC` — the
🛡-ladder-admins channel (root, cross-season, staff + bot-admins) whose
pinned panel is the seasons' lifecycle (create / activate / end /
enrollments), and the per-season config panel rendered in the season's
own admin salon. Everything else (provisioning, visibility policy,
self-healing pins, registry-backed message ids) is the core's business.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.discord.mod_admin_channels import (
    ModAdminChannelSpec,
    ensure_pinned_mod_admin_menu,
    maintain_pinned_mod_admin_menus,
    register_mod_admin_channel,
)

logger = logging.getLogger("kingdoms.ladder.admin_channel")

LADDER_ADMIN_CHANNEL_NAME = "🛡-ladder-admins"
LADDER_SEASON_ADMIN_CHANNEL_NAME = "🛡-season-admin"
LADDER_STAFF_ROLE_PREFIX = "Staff ladder"


async def _build_root_layout(bot: discord.Client, guild_id: str) -> discord.ui.LayoutView:
    """Build the pinned lifecycle panel (the seasons' cross-season view)."""
    from kingdoms.discord.mod_admin_channels import ModAdminPinInteraction
    from kingdoms.mods.ladder.admin_panel import ladder_mod_admin_view

    return await ladder_mod_admin_view(ModAdminPinInteraction(guild_id, bot))


async def _build_season_layout(bot: discord.Client, guild_id: str, scope: str) -> discord.ui.LayoutView:
    """Build the pinned per-season config panel (settings/pool/enroll/pause)."""
    del scope
    from kingdoms.discord.mod_admin_channels import ModAdminPinInteraction
    from kingdoms.mods.ladder.admin_panel import ladder_admin_entry

    return await ladder_admin_entry(ModAdminPinInteraction(guild_id, bot), pinned=True)


LADDER_ADMIN_CHANNEL_SPEC = ModAdminChannelSpec(
    mod="ladder",
    channel_name=LADDER_ADMIN_CHANNEL_NAME,
    seasonal=True,
    staff_role_prefixes=(LADDER_STAFF_ROLE_PREFIX,),
    build_root_layout=_build_root_layout,
    build_season_layout=_build_season_layout,
    season_channel_name=LADDER_SEASON_ADMIN_CHANNEL_NAME,
)


def register_ladder_mod_admin_channel(bot: discord.Client) -> None:
    """Register the ladder's admin surfaces on the core mechanism."""
    register_mod_admin_channel(bot, LADDER_ADMIN_CHANNEL_SPEC)


def build_ladder_admin_channel_service(bot: discord.Client, mongo_uri: str, redis_uri: str) -> Any | None:
    """Wire the 🛡-ladder-admins managed channel; None when stores are absent."""
    from kingdoms.discord.mod_admin_channels import build_mod_admin_channel_service

    return build_mod_admin_channel_service(bot, LADDER_ADMIN_CHANNEL_SPEC, mongo_uri, redis_uri)


async def ensure_pinned_ladder_admin_menu(bot: discord.Client, guild_id: str) -> bool:
    """Ensure the ladder-admins channel holds its pinned lifecycle panel."""
    return await ensure_pinned_mod_admin_menu(bot, LADDER_ADMIN_CHANNEL_SPEC, guild_id)


async def maintain_pinned_ladder_admin_menus(bot: discord.Client) -> None:
    """Keep every registered mod's root admin menu alive (self-healing)."""
    await maintain_pinned_mod_admin_menus(bot)
