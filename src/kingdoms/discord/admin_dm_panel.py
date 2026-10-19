"""The bot-admin DM panel: cross-guild platform actions (core).

A bot admin is cross-guild: the platform-wide actions live in their DMs,
never in one guild's pinned panel, and are visible/clickable by nobody
else. This panel reprises the existing bot-admin surfaces and adds the
per-guild activation seam:

- **Content refresh** — re-run the vendored dataset's extraction into
  the catalog + per-locale content store (game updates: new civs/maps,
  changed unique units/tech trees).

The DM locale of the invoking admin is reused for rendering (user
settings, ``logs_service.get_user_locale``). The namespace
``admin:dm:`` is disjoint from the guild panels' namespaces, so a DM
click has exactly one dispatch path.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.admin_dm")

_NS = "admin:dm"


def _is_bot_admin(interaction: discord.Interaction) -> bool:
    """Whether the clicker is a BOT_ADMINS operator (the DM panel's guard)."""
    admins = getattr(getattr(interaction.client, "status_service", None), "bot_admins", ())
    return str(getattr(interaction.user, "id", "")) in tuple(admins)


async def _deny(interaction: discord.Interaction) -> None:
    """Answer a non-bot-admin click ephemerally and mark it handled."""
    try:
        await interaction.response.send_message(
            _dm_tr(interaction, await _dm_locale(interaction), "bot_admin_only"), ephemeral=True
        )
    except Exception:
        logger.warning("DM PANEL denial answer failed — best-effort", exc_info=True)


async def _dm_locale(source: Any) -> str:
    """Resolve the invoking admin's DM locale (user settings, en fallback)."""
    client = source if isinstance(source, discord.Client) else getattr(source, "client", None)
    user = getattr(source, "user", None)
    user_id = str(getattr(user, "id", "") or "") if user is not None else ""
    logs = getattr(client, "logs_service", None)
    if logs is None or not user_id:
        return "en"
    try:
        return str(await logs.get_user_locale(user_id) or "en")
    except Exception:
        return "en"


def _dm_tr(source: Any, locale: str, key: str, **kwargs: Any) -> str:
    """Render one admin_dm key through the client's catalog (fr fallback)."""
    client = source if isinstance(source, discord.Client) else getattr(source, "client", None)
    catalog = getattr(client, "messages", None)
    if catalog is None:
        return key
    rendered = catalog.render(f"admin_dm.{key}", locale, **kwargs)
    if rendered == f"admin_dm.{key}":
        rendered = catalog.render(f"admin_dm.{key}", "fr", **kwargs)
    return str(rendered)


async def _access_service() -> Any | None:
    from kingdoms.discord.wiring import build_guild_access_service

    return build_guild_access_service()


async def build_admin_dm_panel(
    interaction: discord.Interaction | discord.Client,
    locale_select: discord.ui.Item[Any] | None = None,
) -> discord.ui.LayoutView:
    """Render the bot-admin DM menu: frozen, five openers (the home/admin charte).

    The menu never re-renders on navigation — every button opens an
    ephemeral sub-view. It carries one status line (pending requests,
    servers) so the data reads at a glance, and the callers
    that mutate the data re-render it in place (same message id).
    """
    dm_locale = await _dm_locale(interaction)
    view = discord.ui.LayoutView(timeout=None)
    service = await _access_service()

    status_parts: list[str] = []
    servers: list[dict[str, Any]] = []
    if service is not None:
        try:
            servers = list((await service.list_all()) or [])[:20]
        except Exception:
            logger.warning("DM PANEL: servers read failed", exc_info=True)
        status_parts.append(_dm_tr(interaction, dm_locale, "menu_status", requests=0, servers=len(servers)))
    else:
        status_parts.append(_dm_tr(interaction, dm_locale, "wiring_unavailable"))

    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("# \U0001f6e1\ufe0f " + _dm_tr(interaction, dm_locale, "title")),
            discord.ui.TextDisplay("\n".join(status_parts)),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(DmOpenButton("language", _dm_tr(interaction, dm_locale, "menu_btn_language")))
    row.add_item(DmOpenButton("servers", _dm_tr(interaction, dm_locale, "menu_btn_servers")))
    row.add_item(DmOpenButton("sync", _dm_tr(interaction, dm_locale, "menu_btn_sync")))
    view.add_item(row)
    return view


async def _render_dm_menu_into(message: Any, client: Any) -> None:
    """Edit one DM menu message to the current menu (best-effort)."""
    try:
        await message.edit(view=await build_admin_dm_panel(client))
    except Exception:
        logger.warning("DM PANEL: menu refresh failed \u2014 best-effort", exc_info=True)


async def _refresh_admin_dm_menus(client: Any, exclude_channel: int | None = None) -> None:
    """Re-render every bot-admin's pinned DM menu (best-effort).

    The boot delivery pins one menu per admin; each is registered in
    the module's store, so a data change re-renders them all.
    """
    for channel_id, message in list(_ADMIN_MENU_MESSAGES.items()):
        if exclude_channel is not None and channel_id == exclude_channel:
            continue
        try:
            await message.edit(view=await build_admin_dm_panel(client))
        except Exception:
            _ADMIN_MENU_MESSAGES.pop(channel_id, None)
            logger.info("DM PANEL: menu gone (channel %s) \u2014 dropping from the store", channel_id)


_ADMIN_MENU_MESSAGES: dict[int, Any] = {}


def track_admin_menu(message: Any) -> None:
    """Track one freshly delivered DM menu message (per admin)."""
    channel = getattr(message, "channel", None)
    if message is not None and channel is not None:
        _ADMIN_MENU_MESSAGES[channel.id] = message


class DmOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:open:(?P<section>[a-z]+)",
):
    """One DM menu opener: opens its section's ephemeral sub-view."""

    SECTIONS = ("language", "servers", "sync")

    def __init__(self, section: str, label: str, disabled: bool = False) -> None:
        self.section = section
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:open:{section}",
                disabled=disabled,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DmOpenButton:
        """Rebuild from the wire (labels via the admin's DM locale)."""
        section = match.group("section")
        if section not in cls.SECTIONS:
            section = "language"
        dm_locale = await _dm_locale(interaction)
        labels = {
            "language": "menu_btn_language",
            "servers": "menu_btn_servers",
            "sync": "menu_btn_sync",
        }
        return cls(section, _dm_tr(interaction, dm_locale, labels[section]))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the section's ephemeral sub-view (guarded)."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        if self.section == "language":
            await _open_language(interaction)
        elif self.section == "servers":
            await _open_servers(interaction)
        elif self.section == "sync":
            await _open_sync(interaction)


async def _open_language(interaction: discord.Interaction) -> None:
    """Open the DM language sub-view: the locale picker panel."""
    from kingdoms.discord.admin import build_dm_setup_view

    logs = getattr(interaction.client, "logs_service", None)
    catalog = getattr(interaction.client, "messages", None)
    view = await build_dm_setup_view(
        logs, str(interaction.user.id), catalog=catalog
    )
    await interaction.response.send_message(view=view, ephemeral=True)


async def _open_servers(interaction: discord.Interaction) -> None:
    """Open the servers sub-view: the known guilds, read-only."""
    dm_locale = await _dm_locale(interaction)
    service = await _access_service()
    if service is None:
        await interaction.response.send_message(
            _dm_tr(interaction, dm_locale, "wiring_unavailable"), ephemeral=True
        )
        return
    docs = list((await service.list_all()) or [])[:20]
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [
        discord.ui.TextDisplay("# " + _dm_tr(interaction, dm_locale, "granted_title")),
    ]
    client = interaction.client
    for doc in docs:
        guild = client.get_guild(int(doc.get("guild_id") or 0))
        name = guild.name if guild is not None else _dm_tr(interaction, dm_locale, "unknown_server")
        games = ", ".join(doc.get("games") or []) or "\u2014"
        mods = ", ".join(doc.get("mods") or []) or "\u2014"
        blocks.append(
            discord.ui.TextDisplay(
                _dm_tr(
                    interaction,
                    dm_locale,
                    "granted_line",
                    name=name,
                    guild_id=doc.get("guild_id"),
                    games=games,
                    mods=mods,
                )
            )
        )
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    await interaction.response.send_message(view=view, ephemeral=True)


async def _open_sync(interaction: discord.Interaction) -> None:
    """Open the content sync sub-view: the refresh action."""
    dm_locale = await _dm_locale(interaction)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("# " + _dm_tr(interaction, dm_locale, "sync_title")),
            discord.ui.TextDisplay(_dm_tr(interaction, dm_locale, "sync_subtitle")),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(ContentRefreshButton(label=_dm_tr(interaction, dm_locale, "menu_btn_sync")))
    view.add_item(row)
    await interaction.response.send_message(view=view, ephemeral=True)


class ContentRefreshButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:refresh",
):
    """Refresh the localized faction content from the vendored dataset."""

    def __init__(self, label: str = "Rafraîchir") -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:refresh",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ContentRefreshButton:
        """Rebuild from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, run the dataset refresh (catalog + content), confirm."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        dm_locale = await _dm_locale(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            from kingdoms.core.games.aoe2.content_refresh import refresh_aoe2_content

            counts = await refresh_aoe2_content()
        except Exception:
            logger.exception("DM PANEL: content refresh failed")
            await interaction.followup.send(_dm_tr(interaction, dm_locale, "refresh_failed"), ephemeral=True)
            return
        raw_new_factions = counts.get("new_factions") or []
        new_factions: list[str] = raw_new_factions if isinstance(raw_new_factions, list) else []
        new_line = (
            _dm_tr(interaction, dm_locale, "new_civs_line", names=", ".join(new_factions))
            if new_factions else ""
        )
        await interaction.followup.send(
            _dm_tr(
                interaction, dm_locale, "refresh_done",
                factions=counts.get("total_factions", 0), new=counts.get("factions", 0),
                docs=counts.get("content_docs", 0), new_line=new_line,
            ),
            ephemeral=True,
        )


def register_admin_dm_items(bot: discord.Client) -> None:
    """Register the DM panel's persistent dynamic items."""
    bot.add_dynamic_items(DmOpenButton)
    bot.add_dynamic_items(ContentRefreshButton)
