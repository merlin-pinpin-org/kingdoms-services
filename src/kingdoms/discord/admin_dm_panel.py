"""The bot-admin DM panel: cross-guild platform actions (core).

A bot admin is cross-guild: the platform-wide actions live in their DMs,
never in one guild's pinned panel, and are visible/clickable by nobody
else. This panel reprises the existing bot-admin surfaces and adds the
per-guild activation seam:

- **Access requests** — every registered guild requests access to
  games/mods here (default: nothing active); the bot admin approves or
  denies, and can revoke a granted key at any time.
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
    granted servers) so the data reads at a glance, and the callers
    that mutate the data re-render it in place (same message id).
    """
    dm_locale = await _dm_locale(interaction)
    view = discord.ui.LayoutView(timeout=None)
    service = await _access_service()

    status_parts: list[str] = []
    requests: list[dict[str, Any]] = []
    if service is not None:
        try:
            requests = list((await service.pending_requests()) or [])[:20]
        except Exception:
            logger.warning("DM PANEL: pending read failed", exc_info=True)
        try:
            servers = len((await service.list_all()) or [])
        except Exception:
            servers = 0
        status_parts.append(_dm_tr(interaction, dm_locale, "menu_status", requests=len(requests), servers=servers))
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
    row.add_item(DmOpenButton("requests", _dm_tr(interaction, dm_locale, "menu_btn_requests"), disabled=not requests))
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


async def _cleanup_request_dms(interaction: discord.Interaction, guild_id: str, requested_at: str) -> None:
    """One admin answered: delete the request DMs every admin received."""
    from kingdoms.discord.guild_access_request import _delete_pending_admin_dms

    await _delete_pending_admin_dms(interaction.client, guild_id, requested_at)


class DmOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:open:(?P<section>[a-z]+)",
):
    """One DM menu opener: opens its section's ephemeral sub-view."""

    SECTIONS = ("language", "requests", "servers", "sync")

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
            "requests": "menu_btn_requests",
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
        elif self.section == "requests":
            await _open_requests(interaction)
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


async def _open_requests(interaction: discord.Interaction) -> None:
    """Open the pending-requests sub-view: one row per request."""
    dm_locale = await _dm_locale(interaction)
    service = await _access_service()
    if service is None:
        await interaction.response.send_message(
            _dm_tr(interaction, dm_locale, "wiring_unavailable"), ephemeral=True
        )
        return
    requests = list((await service.pending_requests()) or [])[:20]
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [
        discord.ui.TextDisplay("# " + _dm_tr(interaction, dm_locale, "pending_title")),
    ]
    for request in requests:
        keys = ", ".join(request.get("keys") or [])
        blocks.append(
            discord.ui.TextDisplay(
                _dm_tr(
                    interaction,
                    dm_locale,
                    "pending_line",
                    guild_id=request.get("guild_id"),
                    keys=keys,
                )
            )
        )
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    for request in requests[:5]:
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(
            AccessApproveButton(str(request["guild_id"]), str(request["requested_at"]), 1)
        )
        row.add_item(AccessDenyButton(str(request["guild_id"]), str(request["requested_at"])))
        view.add_item(row)
    await interaction.response.send_message(view=view, ephemeral=True)


async def _open_servers(interaction: discord.Interaction) -> None:
    """Open the granted-servers sub-view: the grant/revoke flow."""
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
    act_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    act_row.add_item(AccessGrantButton(label=_dm_tr(interaction, dm_locale, "access_btn_manage")))
    view.add_item(act_row)
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


class AccessApproveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:approve:(?P<guild_id>\d+):(?P<requested_at>\d+)",
):
    """Approve the guild's pending access request (bot admins only)."""

    def __init__(self, guild_id: str, requested_at: str, pending: int = 1) -> None:
        self.guild_id = guild_id
        self.requested_at = requested_at
        super().__init__(
            discord.ui.Button(
                label="Approuver" if pending <= 1 else f"Approuver ({pending})",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:approve:{guild_id}:{requested_at}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessApproveButton:
        """Rebuild from the wire (guild + request timestamps)."""
        del interaction, item
        return cls(match.group("guild_id"), match.group("requested_at"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, approve, re-render the panel."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        service = await _access_service()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        try:
            await service.approve(self.guild_id, self.requested_at)
        except Exception:
            logger.exception("DM PANEL: approve failed (guild %s)", self.guild_id)
            await interaction.response.send_message("Approbation échouée (déjà traitée ?).", ephemeral=True)
            return
        await _cleanup_request_dms(interaction, self.guild_id, self.requested_at)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _refresh_admin_dm_menus(interaction.client, exclude_channel=interaction.channel_id)
        await interaction.followup.send(
            _dm_tr(interaction, await _dm_locale(interaction), "approved", guild_id=self.guild_id), ephemeral=True
        )


class AccessDenyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:deny:(?P<guild_id>\d+):(?P<requested_at>\d+)",
):
    """Deny the guild's pending access request (bot admins only)."""

    def __init__(self, guild_id: str, requested_at: str) -> None:
        self.guild_id = guild_id
        self.requested_at = requested_at
        super().__init__(
            discord.ui.Button(
                label="Deny",
                style=discord.ButtonStyle.danger,
                custom_id=f"{_NS}:deny:{guild_id}:{requested_at}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessDenyButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"), match.group("requested_at"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, deny, re-render the panel."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        service = await _access_service()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        try:
            await service.deny(self.guild_id, self.requested_at)
        except Exception:
            logger.exception("DM PANEL: deny failed (guild %s)", self.guild_id)
            await interaction.response.send_message("Refus échoué (déjà traitée ?).", ephemeral=True)
            return
        await _cleanup_request_dms(interaction, self.guild_id, self.requested_at)
        await interaction.response.defer(ephemeral=True)
        await _refresh_admin_dm_menus(interaction.client, exclude_channel=interaction.channel_id)


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


class AccessGrantButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:access:grant",
):
    """Open the direct grant/revoke flow (no request needed)."""

    def __init__(self, label: str = "Gérer les accès") -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:access:grant",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessGrantButton:
        """Rebuild from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, then open the guild picker (never silent)."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        dm_locale = await _dm_locale(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            service = await _access_service()
            if service is None:
                await interaction.followup.send("Wiring indisponible.", ephemeral=True)
                return
            guilds = await service.list_all()
        except Exception:
            logger.exception("DM ACCESS: guild list failed")
            await interaction.followup.send(_dm_tr(interaction, dm_locale, "access_list_failed"), ephemeral=True)
            return
        options = []
        for doc in guilds[:25]:
            guild = interaction.client.get_guild(int(doc.get("guild_id") or 0))
            name = guild.name if guild is not None else _dm_tr(interaction, dm_locale, "unknown_server")
            games = ", ".join(doc.get("games") or []) or "—"
            mods = ", ".join(doc.get("mods") or []) or "—"
            options.append(
                discord.SelectOption(
                    label=f"{name} ({doc.get('guild_id')})",
                    value=str(doc.get("guild_id")),
                    description=f"jeux : {games} · mods : {mods}"[:100],
                )
            )
        if not options:
            await interaction.followup.send(_dm_tr(interaction, dm_locale, "access_no_server"), ephemeral=True)
            return
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# Gérer les accès"),
                discord.ui.TextDisplay(_dm_tr(interaction, dm_locale, "access_subtitle")),
                accent_colour=discord.Colour(0x5865F2),
            )
        )
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(AccessGuildSelect(options))
        view.add_item(row)
        await interaction.followup.send(view=view, ephemeral=True)


class AccessGuildSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:access:guild:(?P<guild_id>[a-z0-9]+)",
):
    """Pick the guild whose access is managed directly."""

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:access:guild:picker"[:100],
                options=options,
                placeholder="Serveur \u00e0 g\u00e9rer...",
                max_values=1,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessGuildSelect:
        """Rebuild from the wire (options rebuilt at click time)."""
        del interaction, item
        service = await _access_service()
        guilds = await service.list_all() if service is not None else []
        options = [
            discord.SelectOption(
                label=f"Serveur {doc.get('guild_id')}",
                value=str(doc.get("guild_id")),
            )
            for doc in guilds[:25]
        ] or [discord.SelectOption(label="Aucun serveur", value="none")]
        return cls(options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the key picker for the chosen guild."""
        from kingdoms.discord.wiring import guild_access_platform

        guild_id = (_selected(interaction) or [""])[0]
        if not guild_id or guild_id == "none":
            await interaction.response.defer()
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        games, mods = guild_access_platform()
        view = discord.ui.LayoutView(timeout=None)
        service_note = _dm_tr(
            interaction, await _dm_locale(interaction), "access_server_note",
            guild_id=guild_id, games=", ".join(games) or "—", mods=", ".join(mods) or "—",
        )
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# " + _dm_tr(interaction, await _dm_locale(interaction), "access_server_title", guild_id=guild_id)
                ),
                discord.ui.TextDisplay(service_note),
                accent_colour=discord.Colour(0x5865F2),
            )
        )
        act_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        dm_locale = await _dm_locale(interaction)
        act_row.add_item(AccessAddButton(guild_id, label=_dm_tr(interaction, dm_locale, "access_apply")))
        act_row.add_item(AccessRemoveButton(guild_id, label=_dm_tr(interaction, dm_locale, "access_revoke")))
        view.add_item(act_row)
        await interaction.followup.send(view=view, ephemeral=True)


class AccessAddButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:access:add:(?P<guild_id>[a-z0-9]+)",
):
    """Open the grant modal: a multi-select of the not-yet-granted games/mods."""

    def __init__(self, guild_id: str, label: str = "Accorder") -> None:
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:access:add:{guild_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessAddButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, read the guild's grants, open the grant modal."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        dm_locale = await _dm_locale(interaction)
        from kingdoms.discord.guild_access_request import _request_options

        options = [opt for opt in _request_options("all") if opt.value != "none"]
        service = await _access_service()
        granted: set[str] = set()
        if service is not None:
            doc = await service.get(self.guild_id)
            if doc:
                granted = {f"game:{k}" for k in doc.get("games") or []} | {
                    f"mod:{k}" for k in doc.get("mods") or []
                }
        addable = [opt for opt in options if opt.value not in granted][:25]
        if not addable:
            await interaction.response.send_message(
                _dm_tr(interaction, dm_locale, "access_nothing_to_add"), ephemeral=True
            )
            return
        await interaction.response.send_modal(AccessGrantModal(self.guild_id, addable))


class AccessGrantModal(discord.ui.Modal):
    """The grant form: one multi-select of the addable games/mods."""

    def __init__(self, guild_id: str, options: list[discord.SelectOption]) -> None:
        super().__init__(title="Accorder", timeout=None)
        self.guild_id = guild_id
        self._select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"{_NS}:access:add-select:{guild_id}"[:100],
            options=options,
            placeholder="Jeux/mods à accorder...",
            min_values=1,
            max_values=min(25, len(options)),
        )
        self.add_item(discord.ui.Label(text="Jeux/mods à accorder", component=self._select))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Grant the chosen keys, then report the outcome."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        keys = list(self._select.values or [])
        if not keys:
            await interaction.response.defer(ephemeral=True)
            return
        dm_locale = await _dm_locale(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = await _access_service()
        if service is None:
            await interaction.followup.send(
                _dm_tr(interaction, dm_locale, "wiring_unavailable"), ephemeral=True
            )
            return
        from kingdoms.core.services.guild_access import GuildAccessError

        try:
            doc = await service.grant(self.guild_id, keys)
        except GuildAccessError as error:
            await interaction.followup.send(
                _dm_tr(interaction, dm_locale, "access_denied", error=error), ephemeral=True
            )
            return
        except Exception:
            logger.exception("DM ACCESS: grant modal failed (guild %s)", self.guild_id)
            await interaction.followup.send(
                _dm_tr(interaction, dm_locale, "access_failed"), ephemeral=True
            )
            return
        games = ", ".join(doc.get("games") or []) or "—"
        mods = ", ".join(doc.get("mods") or []) or "—"
        await _refresh_admin_dm_menus(interaction.client)
        await interaction.followup.send(
            _dm_tr(interaction, dm_locale, "access_granted", guild_id=self.guild_id, games=games, mods=mods),
            ephemeral=True,
        )


class AccessRemoveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:access:remove:(?P<guild_id>[a-z0-9]+)",
):
    """Open the revoke modal: a multi-select of the granted games/mods."""

    def __init__(self, guild_id: str, label: str = "Retirer") -> None:
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger,
                custom_id=f"{_NS}:access:remove:{guild_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessRemoveButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, read the guild's grants, open the revoke modal."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        dm_locale = await _dm_locale(interaction)
        service = await _access_service()
        if service is None:
            await interaction.response.send_message(
                _dm_tr(interaction, dm_locale, "wiring_unavailable"), ephemeral=True
            )
            return
        doc = await service.get(self.guild_id) or {}
        options = [discord.SelectOption(label=f"jeu : {k}", value=f"game:{k}") for k in doc.get("games") or []]
        options += [discord.SelectOption(label=f"mod : {k}", value=f"mod:{k}") for k in doc.get("mods") or []]
        if not options:
            await interaction.response.send_message(
                _dm_tr(interaction, dm_locale, "access_nothing_to_remove"), ephemeral=True
            )
            return
        await interaction.response.send_modal(AccessRevokeModal(self.guild_id, options[:25]))


class AccessRevokeModal(discord.ui.Modal):
    """The revoke form: one multi-select of the granted games/mods."""

    def __init__(self, guild_id: str, options: list[discord.SelectOption]) -> None:
        super().__init__(title="Retirer", timeout=None)
        self.guild_id = guild_id
        self._select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"{_NS}:access:remove-select:{guild_id}"[:100],
            options=options,
            placeholder="Jeux/mods à retirer...",
            min_values=1,
            max_values=min(25, len(options)),
        )
        self.add_item(discord.ui.Label(text="Jeux/mods à retirer", component=self._select))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Revoke the chosen keys, then report the outcome."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        keys = list(self._select.values or [])
        if not keys:
            await interaction.response.defer(ephemeral=True)
            return
        dm_locale = await _dm_locale(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = await _access_service()
        if service is None:
            await interaction.followup.send(
                _dm_tr(interaction, dm_locale, "wiring_unavailable"), ephemeral=True
            )
            return
        from kingdoms.core.services.guild_access import GuildAccessError

        try:
            doc = await service.revoke_keys(self.guild_id, keys)
        except GuildAccessError as error:
            await interaction.followup.send(
                _dm_tr(interaction, dm_locale, "access_denied", error=error), ephemeral=True
            )
            return
        except Exception:
            logger.exception("DM ACCESS: revoke modal failed (guild %s)", self.guild_id)
            await interaction.followup.send(
                _dm_tr(interaction, dm_locale, "access_failed"), ephemeral=True
            )
            return
        games = ", ".join(doc.get("games") or []) or "—"
        mods = ", ".join(doc.get("mods") or []) or "—"
        await _refresh_admin_dm_menus(interaction.client)
        await interaction.followup.send(
            _dm_tr(interaction, dm_locale, "access_revoked", guild_id=self.guild_id, games=games, mods=mods),
            ephemeral=True,
        )


def _selected(interaction: discord.Interaction) -> list[str]:
    """Read the interaction's select values (payload-shape proof)."""
    from kingdoms.discord.admin_panel_games import _selected_values

    return list(_selected_values(interaction) or [])


def register_admin_dm_items(bot: discord.Client) -> None:
    """Register the DM panel's persistent dynamic items."""
    bot.add_dynamic_items(DmOpenButton)
    bot.add_dynamic_items(AccessApproveButton)
    bot.add_dynamic_items(AccessDenyButton)
    bot.add_dynamic_items(ContentRefreshButton)
    bot.add_dynamic_items(AccessGrantButton)
    bot.add_dynamic_items(AccessGuildSelect)
    bot.add_dynamic_items(AccessAddButton)
    bot.add_dynamic_items(AccessRemoveButton)
