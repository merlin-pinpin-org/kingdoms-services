"""Persistent admin panel components: restart-proof via custom_id (#122).

The pinned admin menu (``admin_panel_pin``) is a **permanent** message:
it must answer clicks after every restart, but the closures wired into
the original view die with the process. The components here follow the
§3b state reconstruction contract: every state the callback needs is
either the interaction itself (guild, user) or a database read through
the services — never a captured session.

Mechanics:

- the custom_ids are the **same** as the live panel's
  (``admin:select:locale``, ``admin:select:channel``, ...), so the
  DynamicItems also serve a live panel whose view instance expired
  (dispatch tries dynamic items first);
- one :class:`AdminPanelWiring` — the services plus the parsed
  ``BOT_ADMINS`` — is registered at startup by the factory; without it
  the items answer with the standard degradation note;
- access is validated at click time (the developer mandate): the same
  ``require_admin`` guard as everywhere else;
- the panel is rebuilt fresh from the services (``build_main_menu`` /
  ``build_channel_menu``), so a post-restart click re-renders the
  guild's current settings — not a stale snapshot.

Reference: kingdoms-services#122, #115; the restart bug report
(the pinned panel stops answering after a deploy).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import discord

from kingdoms.core.services.admin_channel import ADMIN_CHANNEL_CATEGORY, AdminChannelService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import BOT_LOGS_CATEGORY, LogService
from kingdoms.discord.admin import (
    VISIBILITY_PUBLIC,
    build_channel_menu,
    build_main_menu,
)
from kingdoms.discord.guards import require_admin

logger = logging.getLogger("kingdoms.admin.persistent")

__all__ = [
    "AdminDynamicButton",
    "AdminDynamicRoute",
    "AdminDynamicSelect",
    "AdminPanelWiring",
    "register_admin_panel_wiring",
    "register_admin_persistent_items",
]


@dataclass(frozen=True, slots=True)
class AdminPanelWiring:
    """The services a reconstructed admin panel needs at click time."""

    logs_service: LogService | None
    bot_admins: tuple[str, ...]
    roles_service: Any = None
    catalog: MessageCatalog | None = None
    admin_channel_service: AdminChannelService | None = None
    error_reporter: Any = None


_WIRING_RESOLVER: Callable[[], AdminPanelWiring] | None = None


def register_admin_panel_wiring(wiring: AdminPanelWiring) -> None:
    """Register the panel wiring (compatibility shim, superseded by the bot resolver)."""
    global _WIRING_RESOLVER
    _WIRING_RESOLVER = None


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


class AdminDynamicSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"admin:select:(?P<select>[a-z0-9-]+)",
):
    """A restart-proof admin select: one class for every panel select.

    The payload rides the custom_id (``admin:select:locale``); the
    interaction carries the guild and the user. State lives in the
    database, never in memory.
    """

    def __init__(self, select: str) -> None:
        custom_id = f"admin:select:{select}"
        super().__init__(discord.ui.Select(custom_id=custom_id[:100], options=[]))
        self.select = select

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AdminDynamicSelect:
        """Rebuild the select from the wire — the only post-restart path."""
        return cls(match["select"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Dispatch the reconstructed select to the panel flows."""
        wiring = _wiring()
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        if wiring is None or not guild_id or wiring.logs_service is None:
            await interaction.response.send_message("Admin panel unavailable", ephemeral=True)
            return
        if not await require_admin(interaction, wiring.bot_admins, wiring.roles_service):
            return
        values = _chosen_values(interaction)
        if not values:
            return
        handlers = {
            "locale": _handle_locale,
            "user-locale": _handle_user_locale,
            "channel": _handle_channel,
            "visibility": _handle_visibility,
        }
        handler = handlers.get(self.select)
        if handler is None:
            await interaction.response.send_message("Unknown panel component", ephemeral=True)
            return
        await handler(interaction, wiring, guild_id, values)


class AdminDynamicRoute(
    discord.ui.DynamicItem[discord.ui.ChannelSelect[Any]],
    template=r"admin:channels:(?P<channel>[a-z0-9_]+)",
):
    """A restart-proof admin channel routing select."""

    def __init__(self, channel: str) -> None:
        custom_id = f"admin:channels:{channel}"
        super().__init__(discord.ui.ChannelSelect(custom_id=custom_id[:100]))
        self.channel = channel

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AdminDynamicRoute:
        """Rebuild the routing select from the wire — the only post-restart path."""
        return cls(match["channel"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the routed channel, then re-render the secondary menu."""
        wiring = _wiring()
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        if wiring is None or not guild_id:
            await interaction.response.send_message("Admin panel unavailable", ephemeral=True)
            return
        if not await require_admin(interaction, wiring.bot_admins, wiring.roles_service):
            return
        logs_service = await _require_logs(wiring, interaction)
        if logs_service is None:
            return
        values = _chosen_values(interaction)
        if not values:
            return
        by = str(interaction.user.id)
        try:
            if self.channel == "logs":
                await logs_service.set_channel(guild_id, values[0], by=by)
                category = BOT_LOGS_CATEGORY
            else:
                if wiring.admin_channel_service is None:
                    raise RuntimeError("admin channel management is unavailable")
                await wiring.admin_channel_service.set_channel(guild_id, values[0])
                category = ADMIN_CHANNEL_CATEGORY
        except Exception as exc:
            logger.exception("ADMIN PANEL (persistent): channel routing failed for guild %s", guild_id)
            await _report(wiring, interaction, exc)
            await interaction.response.send_message("Routing failed", ephemeral=True)
            return
        await interaction.response.edit_message(
            view=await build_channel_menu(
                logs_service,
                guild_id,
                category,
                by,
                wiring.bot_admins,
                wiring.roles_service,
                wiring.catalog,
                await _locale_of(wiring, guild_id),
                wiring.admin_channel_service,
                wiring.error_reporter,
            )
        )


class AdminDynamicButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"admin:button:(?P<button>[a-z0-9-]+)",
):
    """A restart-proof admin button (the back button)."""

    def __init__(self, button: str) -> None:
        custom_id = f"admin:button:{button}"
        super().__init__(
            discord.ui.Button(
                label=button,
                style=discord.ButtonStyle.secondary,
                custom_id=custom_id[:100],
            )
        )
        self.button = button

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AdminDynamicButton:
        """Rebuild the button from the wire — the only post-restart path."""
        return cls(match["button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Dispatch the reconstructed button (back → main menu)."""
        wiring = _wiring()
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        if wiring is None or not guild_id:
            await interaction.response.send_message("Admin panel unavailable", ephemeral=True)
            return
        if not await require_admin(interaction, wiring.bot_admins, wiring.roles_service):
            return
        logs_service = await _require_logs(wiring, interaction)
        if logs_service is None:
            return
        if self.button == "back":
            await interaction.response.edit_message(
                view=await build_main_menu(
                    logs_service,
                    guild_id,
                    str(interaction.user.id),
                    wiring.bot_admins,
                    wiring.roles_service,
                    wiring.catalog,
                    wiring.admin_channel_service,
                    wiring.error_reporter,
                )
            )
            return
        await interaction.response.send_message("Unknown panel component", ephemeral=True)


async def _handle_locale(
    interaction: discord.Interaction,
    wiring: AdminPanelWiring,
    guild_id: str,
    values: list[str],
) -> None:
    """Apply the guild locale, then re-render the main menu."""
    logs_service = await _require_logs(wiring, interaction)
    if logs_service is None:
        return
    by = str(interaction.user.id)
    try:
        await logs_service.set_locale(guild_id, values[0], by=by)
    except Exception as exc:
        logger.exception("ADMIN PANEL (persistent): locale change failed for guild %s", guild_id)
        await _report(wiring, interaction, exc)
        await interaction.response.send_message("Language change failed", ephemeral=True)
        return
    await interaction.response.edit_message(
        view=await build_main_menu(
            logs_service,
            guild_id,
            by,
            wiring.bot_admins,
            wiring.roles_service,
            wiring.catalog,
            wiring.admin_channel_service,
            wiring.error_reporter,
        )
    )


async def _handle_user_locale(
    interaction: discord.Interaction,
    wiring: AdminPanelWiring,
    guild_id: str,
    values: list[str],
) -> None:
    """Apply the user's DM locale, then re-render the DM setup view."""
    logs_service = await _require_logs(wiring, interaction)
    if logs_service is None:
        return
    try:
        await logs_service.set_user_locale(str(interaction.user.id), values[0])
    except Exception as exc:
        logger.exception("ADMIN PANEL (persistent): user locale change failed for user %s", interaction.user.id)
        await _report(wiring, interaction, exc)
        await interaction.response.send_message("DM language change failed", ephemeral=True)
        return
    from kingdoms.discord.admin import build_dm_setup_view

    await interaction.response.edit_message(
        view=await build_dm_setup_view(
            logs_service,
            str(interaction.user.id),
            wiring.bot_admins,
            wiring.catalog,
            wiring.error_reporter,
        )
    )


async def _handle_channel(
    interaction: discord.Interaction,
    wiring: AdminPanelWiring,
    guild_id: str,
    values: list[str],
) -> None:
    """Open the secondary menu of the selected managed channel."""
    logs_service = await _require_logs(wiring, interaction)
    if logs_service is None:
        return
    by = str(interaction.user.id)
    await interaction.response.edit_message(
        view=await build_channel_menu(
            logs_service,
            guild_id,
            values[0],
            by,
            wiring.bot_admins,
            wiring.roles_service,
            wiring.catalog,
            await _locale_of(wiring, guild_id),
            wiring.admin_channel_service,
            wiring.error_reporter,
        )
    )


async def _handle_visibility(
    interaction: discord.Interaction,
    wiring: AdminPanelWiring,
    guild_id: str,
    values: list[str],
) -> None:
    """Apply the visibility choice, then re-render the secondary menu."""
    logs_service = await _require_logs(wiring, interaction)
    if logs_service is None:
        return
    by = str(interaction.user.id)
    try:
        await logs_service.set_visibility(guild_id, values[0] == VISIBILITY_PUBLIC, by=by)
    except Exception as exc:
        logger.exception("ADMIN PANEL (persistent): visibility change failed for guild %s", guild_id)
        await _report(wiring, interaction, exc)
        await interaction.response.send_message("Visibility change failed", ephemeral=True)
        return
    await interaction.response.edit_message(
        view=await build_channel_menu(
            logs_service,
            guild_id,
            BOT_LOGS_CATEGORY,
            by,
            wiring.bot_admins,
            wiring.roles_service,
            wiring.catalog,
            await _locale_of(wiring, guild_id),
            wiring.admin_channel_service,
            wiring.error_reporter,
        )
    )


async def _require_logs(
    wiring: AdminPanelWiring,
    interaction: discord.Interaction,
) -> LogService | None:
    """Resolve the LogService or answer the degradation note (awaited)."""
    if wiring.logs_service is not None:
        return wiring.logs_service
    try:
        await interaction.response.send_message("Admin panel unavailable", ephemeral=True)
    except Exception:
        logger.warning("ADMIN PANEL (persistent): degradation answer failed", exc_info=True)
    return None


def _chosen_values(interaction: discord.Interaction) -> list[str]:
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
        logger.warning("ADMIN PANEL (persistent): error reporting failed — best-effort", exc_info=True)


def register_admin_persistent_items(bot: discord.Client) -> None:
    """Register the admin panel DynamicItems (called at every startup)."""
    bot.add_dynamic_items(AdminDynamicSelect, AdminDynamicRoute, AdminDynamicButton)
