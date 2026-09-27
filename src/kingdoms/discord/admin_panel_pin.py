"""The pinned admin menu: a permanent, self-healing panel surface.

The \U0001f6e1-bot-admins channel is the admin home: the panel must be
reachable without typing ``/admin``. The bot therefore maintains a
**pinned menu** in every guild's admin channel:

- the message carries the standard main menu (the same builder as
  /admin) \u2014 access is validated at click time by the very same
  guards, so a non-admin reading the channel and clicking a component
  is denied with the ephemeral reason (seeing a button never grants
  the right to use it);
- the menu id is ``admin:pin:menu`` \u2014 the marker identifying the
  pinned message among the channel pins;
- a periodic check (the factory's heartbeat) re-creates and re-pins
  the menu when it is gone (unpinned, deleted, or the channel itself
  re-provisioned), so the surface self-heals;
- everything is best-effort: a failing pin never blocks the bot, and
  the ``KINGDOMS_ANNOUNCE_ENABLED=0`` smoke bot never touches pins.

Reference: kingdoms-services#115 (admin channel), the developer
mandate on click-time guards.
"""

from __future__ import annotations

import logging

import discord

from kingdoms.core.services.admin_channel import AdminChannelService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import LogService
from kingdoms.core.services.roles import RolesService

logger = logging.getLogger("kingdoms.admin.pin")

PINNED_MENU_CUSTOM_ID = "admin:pin:menu"


async def ensure_pinned_admin_menu(
    bot: discord.Client,
    guild_id: str,
    logs_service: LogService,
    roles_service: RolesService | None,
    admin_channel_service: AdminChannelService | None,
    admin_ids: tuple[str, ...],
    catalog: MessageCatalog | None = None,
) -> bool:
    """Ensure the guild's admin channel holds exactly one pinned menu.

    Returns True when a message was (re-)created, False when the
    existing pin was already in place. The menu itself reuses the
    standard main-menu builder \u2014 click-time guards included \u2014 and
    the periodic re-checks make the surface self-healing.
    """
    if admin_channel_service is None:
        return False
    channel_id = await admin_channel_service.resolve_channel(guild_id, admin_ids)
    if channel_id is None:
        return False
    if await _pinned_menu_exists(bot, guild_id, channel_id):
        return False
    from kingdoms.discord.admin import build_main_menu

    layout = await build_main_menu(
        logs_service,
        guild_id,
        "system",
        admin_ids,
        roles_service,
        catalog,
        admin_channel_service,
    )
    message_id = await admin_channel_service.deliver(guild_id, layout, admin_ids)
    if message_id is None:
        logger.warning("PINNED ADMIN MENU delivery failed (guild %s) \u2014 best-effort", guild_id)
        return False
    pinned = await _pin_message(bot, guild_id, channel_id, message_id)
    if pinned:
        logger.info("PINNED ADMIN MENU created (guild %s, message %s)", guild_id, message_id)
    return pinned


async def _pinned_menu_exists(bot: discord.Client, guild_id: str, channel_id: str) -> bool:
    """Whether the channel pins already hold the pinned menu message."""
    channel = _text_channel(bot, guild_id, channel_id)
    if channel is None:
        return False
    try:
        pins = await channel.pins()
    except Exception:
        logger.warning("PINNED ADMIN MENU pin lookup failed (guild %s) \u2014 best-effort", guild_id)
        return False
    for message in pins:
        if _carries_pinned_menu(message):
            return True
    return False


def _carries_pinned_menu(message: discord.Message) -> bool:
    """Whether a pinned message carries the pinned-menu component."""
    for child in _walk(message):
        custom_id = getattr(child, "custom_id", None)
        if custom_id is None:
            continue
        if _starts_with_admin_marker(str(custom_id)):
            return True
    return False


def _starts_with_admin_marker(custom_id: str) -> bool:
    """Match every admin panel component id (admin:... convention)."""
    return custom_id.startswith("admin:")


def _walk(message: discord.Message) -> list[object]:
    """Walk the components of a message (V2 layout or classic view)."""
    items: list[object] = []

    def _items_of(component: object) -> None:
        items.append(component)
        for attr in ("children", "components"):
            children = getattr(component, attr, None)
            if children and isinstance(children, (list, tuple)):
                for child in children:
                    _items_of(child)

    for component in message.components:
        _items_of(component)
    return items


def _text_channel(bot: discord.Client, guild_id: str, channel_id: str) -> discord.TextChannel | None:
    """Resolve the admin channel as a live text channel; None when absent."""
    if not guild_id.isdigit() or not channel_id.isdigit():
        return None
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


async def _pin_message(bot: discord.Client, guild_id: str, channel_id: str, message_id: str) -> bool:
    """Pin one message by id; False when the pin fails (best-effort)."""
    channel = _text_channel(bot, guild_id, channel_id)
    if channel is None:
        return False
    try:
        message = await channel.fetch_message(int(message_id))
        await message.pin(reason="kingdoms: pinned admin menu (admin channel home)")
        return True
    except Exception:
        logger.warning("PINNED ADMIN MENU pin failed (guild %s, message %s) \u2014 best-effort", guild_id, message_id)
        return False
