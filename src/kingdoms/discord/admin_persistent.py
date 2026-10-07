"""Persistent admin panel handlers: restart-proof via custom_id (#122).

The pinned admin menu (``admin_panel_pin``) is a **permanent** message:
it must answer clicks after every restart, but any closure wired into
the original view dies with the process. The components follow the §3b
state reconstruction contract: every state the callback needs is either
the interaction itself (guild, user) or a database read through the
services \u2014 never a captured session.

Mechanics:

- the surface classes live in :mod:`kingdoms.discord.admin_panel_dynamic`
  under the dedicated ``admin:pin:`` namespace \u2014 one custom_id, one
  dispatch mechanism (the historical ``admin:select:*`` ids were served
  by both a dynamic handler and the live view's captured closures, so
  a click applied the change twice: once as the user, once as
  ``by="system"``);
- the **category** rides the routing payload
  (``admin:pin:route:bot_logs`` vs ``admin:pin:route:bot_admins``) \u2014
  the legacy ``admin:channels:logs`` id was shared by both channel menus
  and always routed to the logs;
- one :class:`AdminPanelWiring` \u2014 the services plus the parsed
  ``BOT_ADMINS`` \u2014 resolves from the live **bot** at click time
  (:func:`register_admin_panel_bot`): the factory builds the services
  after ``setup_hook``, so a wiring snapshot taken at startup can be
  stale (services None);
- access is validated at click time (the developer mandate): the same
  ``require_admin`` guard as everywhere else \u2014 after the defer, so the
  guard's role lookup never eats into the 3-second window;
- the panel re-renders fresh from the services
  (:func:`build_pin_main_menu` / :func:`build_pin_channel_menu`), so a
  post-restart click shows the guild's current settings \u2014 not a
  stale snapshot.

Reference: kingdoms-services#122, #115; the restart bug report (the
pinned panel stops answering after a deploy) and the ``<@system>``
double-dispatch audit bug.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import discord

from kingdoms.core.services.logs import BOT_LOGS_CATEGORY, LogService
from kingdoms.discord.admin import VISIBILITY_PUBLIC
from kingdoms.discord.guards import require_admin

logger = logging.getLogger("kingdoms.admin.persistent")

__all__ = [
    "AdminPanelWiring",
    "register_admin_panel_bot",
    "register_admin_panel_wiring",
    "register_admin_persistent_items",
]


@dataclass(frozen=True, slots=True)
class AdminPanelWiring:
    """The services a reconstructed admin panel needs at click time."""

    logs_service: LogService | None
    bot_admins: tuple[str, ...]
    roles_service: Any = None
    catalog: Any = None
    admin_channel_service: Any = None
    error_reporter: Any = None


_WIRING_RESOLVER: Callable[[], AdminPanelWiring] | None = None


def register_admin_panel_wiring(wiring: AdminPanelWiring) -> None:
    """Register the panel wiring (compatibility shim, superseded by the bot resolver)."""
    global _WIRING_RESOLVER

    def _resolve() -> AdminPanelWiring:
        return wiring

    _WIRING_RESOLVER = _resolve


def register_admin_panel_bot(bot: Any) -> None:
    """Register the bot as the wiring source (services resolve at click time).

    The factory builds the services after ``setup_hook`` runs, so a
    wiring snapshot taken at startup can be stale (services None).
    Resolving from the live bot at click time closes the ordering
    window: the click always sees the current services.
    """
    global _WIRING_RESOLVER

    def _resolve() -> AdminPanelWiring:
        logs: LogService | None = getattr(bot, "logs_service", None)
        admins: tuple[str, ...] = tuple(getattr(bot.status_service, "bot_admins", ()))
        return AdminPanelWiring(
            logs_service=logs,
            bot_admins=admins,
            roles_service=getattr(bot, "roles_service", None),
            catalog=getattr(bot, "messages", None),
            admin_channel_service=getattr(bot, "admin_channel_service", None),
            error_reporter=getattr(bot, "crash_report", None),
        )

    resolver: Callable[[], AdminPanelWiring] = _resolve
    _WIRING_RESOLVER = resolver


def _wiring() -> AdminPanelWiring | None:
    if _WIRING_RESOLVER is None:
        return None
    try:
        return _WIRING_RESOLVER()
    except Exception:
        logger.warning("ADMIN PANEL (persistent): wiring resolution failed", exc_info=True)
        return None


async def _locale_of(wiring: AdminPanelWiring, guild_id: str) -> str:
    if wiring.logs_service is None:
        return "en"
    try:
        return await wiring.logs_service.get_locale(guild_id)
    except Exception:
        return "en"


async def _require_wiring(interaction: discord.Interaction) -> AdminPanelWiring | None:
    """Resolve the wiring or answer the degradation note (awaited)."""
    wiring = _wiring()
    if wiring is None or wiring.logs_service is None:
        await _degrade(interaction, "Admin panel unavailable")
        return None
    return wiring


async def _guard(interaction: discord.Interaction, wiring: AdminPanelWiring) -> bool:
    """Validate the click (the developer mandate), denying ephemerally."""
    return await require_admin(interaction, wiring.bot_admins, wiring.roles_service)


async def _degrade(interaction: discord.Interaction, message: str) -> None:
    try:
        await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        logger.warning("ADMIN PANEL (persistent): degradation answer failed", exc_info=True)


async def _fail(interaction: discord.Interaction, message: str) -> None:
    """Answer with the failure note after the acknowledgment (followup)."""
    try:
        await interaction.followup.send(message, ephemeral=True)
    except Exception:
        logger.warning("ADMIN PANEL (persistent): failure answer failed", exc_info=True)


async def _chosen_values(interaction: discord.Interaction) -> list[str]:
    """Read the chosen values of a select interaction."""
    data = getattr(interaction, "data", None) or {}
    return [str(v) for v in data.get("values", [])]


async def _report(wiring: AdminPanelWiring, interaction: discord.Interaction, exc: BaseException) -> None:
    """Forward a callback failure to the crash reporter, best-effort."""
    reporter = wiring.error_reporter
    if reporter is None:
        return
    try:
        await reporter(interaction, exc)
    except Exception:
        logger.warning("ADMIN PANEL (persistent): error reporting failed \u2014 best-effort", exc_info=True)


async def _handle_locale(interaction: discord.Interaction) -> None:
    """Apply the guild locale, then re-render the pinned main menu."""
    wiring = await _require_wiring(interaction)
    if wiring is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await _degrade(interaction, "Admin panel unavailable")
        return
    values = await _chosen_values(interaction)
    if not values:
        return
    logs_service = wiring.logs_service
    if logs_service is None:
        await _degrade(interaction, "Admin panel unavailable")
        return
    await interaction.response.defer()
    if not await _guard(interaction, wiring):
        return
    by = str(interaction.user.id)
    try:
        await logs_service.set_locale(guild_id, values[0], by=by)
    except Exception as exc:
        logger.exception("ADMIN PANEL (persistent): locale change failed for guild %s", guild_id)
        await _report(wiring, interaction, exc)
        await _fail(interaction, "Language change failed")
        return
    from kingdoms.discord.pinned_views import refresh_registered_pins

    await refresh_registered_pins(guild_id)
    await _fail(interaction, "Language updated")
    await _resync_guild_commands(interaction, guild_id)


async def _resync_guild_commands(interaction: discord.Interaction, guild_id: str) -> None:
    """Re-sync this guild's slash commands so their names follow the new language."""
    import discord

    tree = getattr(interaction.client, "tree", None)
    if tree is None:
        return
    try:
        await tree.sync(guild=discord.Object(id=int(guild_id)))
        logger.info("COMMAND RESYNC after locale change (guild %s)", guild_id)
    except Exception:
        logger.warning("COMMAND RESYNC failed (guild %s) — best-effort", guild_id, exc_info=True)


async def _handle_channel(interaction: discord.Interaction) -> None:
    """Open the secondary menu of the selected managed channel."""
    wiring = await _require_wiring(interaction)
    if wiring is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await _degrade(interaction, "Admin panel unavailable")
        return
    values = await _chosen_values(interaction)
    if not values:
        return
    logs_service = wiring.logs_service
    if logs_service is None:
        await _degrade(interaction, "Admin panel unavailable")
        return
    await interaction.response.defer()
    if not await _guard(interaction, wiring):
        return
    from kingdoms.discord.admin_panel_dynamic import build_pin_channel_menu

    locale = await _locale_of(wiring, guild_id)
    await interaction.edit_original_response(
        view=await build_pin_channel_menu(
            logs_service,
            guild_id,
            values[0],
            wiring.catalog,
            locale,
            wiring.admin_channel_service,
        )
    )


async def _handle_visibility(interaction: discord.Interaction) -> None:
    """Apply the visibility choice, then re-render the logs channel menu."""
    wiring = await _require_wiring(interaction)
    if wiring is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await _degrade(interaction, "Admin panel unavailable")
        return
    values = await _chosen_values(interaction)
    if not values:
        return
    logs_service = wiring.logs_service
    if logs_service is None:
        await _degrade(interaction, "Admin panel unavailable")
        return
    await interaction.response.defer()
    if not await _guard(interaction, wiring):
        return
    by = str(interaction.user.id)
    try:
        await logs_service.set_visibility(guild_id, values[0] == VISIBILITY_PUBLIC, by=by)
    except Exception as exc:
        logger.exception("ADMIN PANEL (persistent): visibility change failed for guild %s", guild_id)
        await _report(wiring, interaction, exc)
        await _fail(interaction, "Visibility change failed")
        return
    from kingdoms.discord.admin_panel_dynamic import build_pin_channel_menu

    locale = await _locale_of(wiring, guild_id)
    await interaction.edit_original_response(
        view=await build_pin_channel_menu(
            logs_service,
            guild_id,
            BOT_LOGS_CATEGORY,
            wiring.catalog,
            locale,
            wiring.admin_channel_service,
        )
    )


async def _handle_route(interaction: discord.Interaction, category: str) -> None:
    """Route the category's channel, then re-render its menu.

    The category rides the custom_id payload (``admin:pin:route:<category>``)
    \u2014 the legacy ``admin:channels:logs`` id was shared by both channel
    menus and always routed to the logs channel.
    """
    wiring = await _require_wiring(interaction)
    if wiring is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await _degrade(interaction, "Admin panel unavailable")
        return
    values = await _chosen_values(interaction)
    if not values:
        return
    logs_service = wiring.logs_service
    if logs_service is None:
        await _degrade(interaction, "Admin panel unavailable")
        return
    await interaction.response.defer()
    if not await _guard(interaction, wiring):
        return
    by = str(interaction.user.id)
    try:
        if category == BOT_LOGS_CATEGORY:
            await logs_service.set_channel(guild_id, values[0], by=by)
        else:
            if wiring.admin_channel_service is None:
                raise RuntimeError("admin channel management is unavailable (no AdminChannelService wired)")
            await wiring.admin_channel_service.set_channel(guild_id, values[0])
    except Exception as exc:
        logger.exception("ADMIN PANEL (persistent): channel routing failed for guild %s", guild_id)
        await _report(wiring, interaction, exc)
        await _fail(interaction, "Routing failed \u2014 see the bot logs")
        return
    from kingdoms.discord.admin_panel_dynamic import build_pin_channel_menu

    locale = await _locale_of(wiring, guild_id)
    await interaction.edit_original_response(
        view=await build_pin_channel_menu(
            logs_service,
            guild_id,
            category,
            wiring.catalog,
            locale,
            wiring.admin_channel_service,
        )
    )


async def _handle_back(interaction: discord.Interaction) -> None:
    """Return from the channel menu to the pinned main menu."""
    wiring = await _require_wiring(interaction)
    if wiring is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await _degrade(interaction, "Admin panel unavailable")
        return
    logs_service = wiring.logs_service
    if logs_service is None:
        await _degrade(interaction, "Admin panel unavailable")
        return
    await interaction.response.defer()
    if not await _guard(interaction, wiring):
        return
    from kingdoms.discord.admin_panel_dynamic import build_pin_main_menu

    locale = await _locale_of(wiring, guild_id)
    await interaction.edit_original_response(
        view=await build_pin_main_menu(
            logs_service,
            guild_id,
            wiring.catalog,
            locale,
            wiring.admin_channel_service,
        )
    )


async def _handle_read_only(interaction: discord.Interaction) -> None:
    """Persist the read-only intent, re-apply it, refresh the pins."""
    wiring = await _require_wiring(interaction)
    if wiring is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await _degrade(interaction, "Admin panel unavailable")
        return
    values = await _chosen_values(interaction)
    if not values or values[0] == "none":
        await interaction.response.defer()
        return
    read_only = not values[0].endswith(":open")
    category = values[0].removesuffix(":open").removesuffix(":locked")
    await interaction.response.defer()
    if not await _guard(interaction, wiring):
        return
    from kingdoms.discord.pinned_views import refresh_registered_pins, set_pinned_read_only

    logs = wiring.logs_service
    db = getattr(logs, "_db", None) if logs is not None else None
    await set_pinned_read_only(guild_id, category, read_only, db=db)
    await refresh_registered_pins(guild_id)


async def _handle_roles(interaction: discord.Interaction) -> None:
    """Show the roles view ephemerally (core roles + mod-declared roles)."""
    wiring = await _require_wiring(interaction)
    if wiring is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if not guild_id:
        await _degrade(interaction, "Admin panel unavailable")
        return
    if not await _guard(interaction, wiring):
        return
    from kingdoms.discord.admin_roles import build_roles_view

    view = await build_roles_view(interaction, wiring)
    try:
        await interaction.response.send_message(view=view, ephemeral=True)
    except Exception:
        logger.warning("ADMIN PANEL: roles view answer failed", exc_info=True)



def register_admin_persistent_items(bot: discord.Client) -> None:
    """Register the pinned panel DynamicItems (called at every startup)."""
    from kingdoms.discord.admin_panel_dynamic import (
        PinBackButton,
        PinChannelMenu,
        PinLocaleSelect,
        PinReadOnlySelect,
        PinRolesButton,
        PinRouteSelect,
        PinVisibilitySelect,
        set_panel_client,
    )
    from kingdoms.discord.admin_panel_mods import PinModRouteSelect

    set_panel_client(bot)
    bot.add_dynamic_items(
        PinLocaleSelect,
        PinChannelMenu,
        PinVisibilitySelect,
        PinRouteSelect,
        PinBackButton,
        PinReadOnlySelect,
        PinRolesButton,
        PinModRouteSelect,
    )
