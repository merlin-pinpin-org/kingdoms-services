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
    """Ensure the guild's admin channel holds exactly one current pinned menu.

    Returns True when a message was (re-)created, False when the
    existing pin was already current. The menu is the fully dynamic pin
    surface (``admin_panel_dynamic``) \u2014 no captured state, click-time
    guards included \u2014 and the periodic re-checks make the surface
    self-healing. A pinned menu of the **legacy** id namespace
    (``admin:select:*``) is stale: it was sent with live closures
    (``by="system"``) whose double dispatch audited phantom changes \u2014
    it is replaced and unpinned.
    """
    if admin_channel_service is None:
        return False
    channel_id = await admin_channel_service.resolve_channel(guild_id, admin_ids)
    channel = _text_channel(bot, guild_id, str(channel_id)) if channel_id else None
    if channel is None:
        return False

    from typing import cast

    from kingdoms.core.services.pinned_menu import PinnedMenuChannel, PinnedMenuService
    from kingdoms.discord.admin_panel_dynamic import build_pin_main_menu

    async def _build(guild: str) -> object:
        return await build_pin_main_menu(
            logs_service,
            guild,
            catalog,
            await _current_locale(logs_service, guild),
            admin_channel_service,
        )

    delivery = _AdminPinDelivery(admin_channel_service, admin_ids, guild_id)
    service = PinnedMenuService(delivery)
    created = await service.ensure(
        str(guild_id),
        cast("PinnedMenuChannel", channel),
        marker="admin:pin:",
        build_layout=_build,
        pin_reason="kingdoms: pinned admin menu (admin channel home)",
    )
    for message in await _stale_pinned_menus(bot, guild_id, str(channel_id)):
        await _unpin_message(message)
    return created


class _AdminPinDelivery:
    """Deliver a layout through the admin channel service; the message id."""

    def __init__(self, service: AdminChannelService, admin_ids: tuple[str, ...], guild_id: str) -> None:
        self._service = service
        self._admin_ids = admin_ids
        self._guild_id = guild_id

    async def deliver(self, channel: object, layout: object) -> str:
        del channel
        message_id = await self._service.deliver(self._guild_id, layout, self._admin_ids)
        return str(message_id or "")


async def _current_locale(logs_service: LogService, guild_id: str) -> str:
    """Read the guild's locale, degrading to English (best-effort)."""
    try:
        return await logs_service.get_locale(guild_id)
    except Exception:
        return "en"


async def _stale_pinned_menus(bot: discord.Client, guild_id: str, channel_id: str) -> list[discord.Message]:
    """Return the pinned menus of the legacy id namespace (unpinned after the rebuild)."""
    channel = _text_channel(bot, guild_id, channel_id)
    if channel is None:
        return []
    try:
        pins = await channel.pins()
    except Exception:
        logger.warning("PINNED ADMIN MENU pin lookup failed (guild %s) \u2014 best-effort", guild_id)
        return []
    return [message for message in pins if _carries_legacy_menu(message)]


async def _unpin_message(message: discord.Message) -> None:
    """Unpin one stale menu message (best-effort)."""
    try:
        await message.unpin(reason="kingdoms: legacy pinned admin menu (replaced)")
    except Exception:
        logger.warning("PINNED ADMIN MENU unpin failed (message %s) \u2014 best-effort", getattr(message, "id", "?"))


def _carries_legacy_menu(message: discord.Message) -> bool:
    """Whether a pinned message carries a **legacy** pinned-menu component."""
    for child in _walk(message):
        custom_id = getattr(child, "custom_id", None)
        if custom_id is None:
            continue
        if _is_legacy_menu_id(str(custom_id)):
            return True
    return False


def _starts_with_admin_marker(custom_id: str) -> bool:
    """Match a current pinned-menu component id (admin:pin: namespace)."""
    return custom_id.startswith("admin:pin:")


def _is_legacy_menu_id(custom_id: str) -> bool:
    """Match a legacy pinned-menu id (the live /admin panel namespace)."""
    return custom_id.startswith(("admin:select:", "admin:channels:", "admin:button:"))


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


