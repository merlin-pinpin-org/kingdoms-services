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
    """Render the bot-admin DM panel: one FR menu, sectioned.

    Sections: demandes en attente (approve/deny), accords actifs, la
    langue des DM du bot admin (the DM locale select rides inside this
    single panel), refresh du contenu civs/maps et gestion directe des
    accords (sans demande). No provider-mapping section. The caller
    passes the interaction (clicks) or the client itself (the boot's
    DM delivery) — only the client reference is read.
    """
    client = interaction if isinstance(interaction, discord.Client) else getattr(interaction, "client", None)
    service = await _access_service()
    view = discord.ui.LayoutView(timeout=None)
    _locale = await _dm_locale(interaction)
    blocks: list[Any] = [
        discord.ui.TextDisplay("# \U0001f6e1\ufe0f " + _dm_tr(interaction, _locale, "title"))
    ]
    if service is None:
        blocks.append(discord.ui.TextDisplay(_dm_tr(interaction, _locale, "wiring_unavailable")))
        blocks.append(discord.ui.Separator())
        blocks.append(discord.ui.TextDisplay("## " + _dm_tr(interaction, _locale, "language_title")))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        if locale_select is not None:
            early_row = discord.ui.ActionRow[discord.ui.LayoutView]()
            early_row.add_item(locale_select)
            view.add_item(early_row)
        return view
    requests = await service.pending_requests()
    if requests:
        req_lines = []
        for request in requests[:20]:
            guild_id = request.get("guild_id")
            keys = ", ".join(request.get("keys") or [])
            req_lines.append(_dm_tr(interaction, _locale, "pending_line", guild_id=guild_id, keys=keys))
        blocks.append(discord.ui.Separator())
        blocks.append(
            discord.ui.TextDisplay(
                _dm_tr(interaction, _locale, "pending_title") + "\n" + "\n".join(req_lines)
            )
        )
    access_docs = await service.list_all()
    granted_lines = []
    for doc in access_docs[:20]:
        guild = client.get_guild(int(doc.get("guild_id") or 0)) if client is not None else None
        name = guild.name if guild is not None else _dm_tr(interaction, _locale, "unknown_server")
        games = ", ".join(doc.get("games") or []) or "\u2014"
        mods = ", ".join(doc.get("mods") or []) or "\u2014"
        granted_lines.append(
            _dm_tr(
                interaction, _locale, "granted_line",
                name=name, guild_id=doc.get("guild_id"), games=games, mods=mods,
            )
        )
    if granted_lines:
        blocks.append(discord.ui.Separator())
        blocks.append(
            discord.ui.TextDisplay(_dm_tr(interaction, _locale, "granted_title") + "\n" + "\n".join(granted_lines))
        )
    blocks.append(discord.ui.Separator())
    blocks.append(discord.ui.TextDisplay("## " + _dm_tr(interaction, _locale, "language_title")))
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    if requests:
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        first = requests[0]
        row.add_item(AccessApproveButton(str(first["guild_id"]), str(first["requested_at"]), len(requests)))
        row.add_item(AccessDenyButton(str(first["guild_id"]), str(first["requested_at"])))
        view.add_item(row)
    if locale_select is not None:
        locale_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        locale_row.add_item(locale_select)
        view.add_item(locale_row)
    actions_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    actions_row.add_item(ContentRefreshButton())
    actions_row.add_item(AccessGrantButton())
    view.add_item(actions_row)
    return view


async def _cleanup_request_dms(interaction: discord.Interaction, guild_id: str, requested_at: str) -> None:
    """One admin answered: delete the request DMs every admin received."""
    from kingdoms.discord.guild_access_request import _delete_pending_admin_dms

    await _delete_pending_admin_dms(interaction.client, guild_id, requested_at)


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
        await interaction.response.edit_message(view=await build_admin_dm_panel(interaction))
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
        await interaction.response.edit_message(view=await build_admin_dm_panel(interaction))


class ContentRefreshButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:refresh",
):
    """Refresh the localized faction content from the vendored dataset."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Rafraîchir le contenu civs/maps",
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

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="G\u00e9rer les acc\u00e8s",
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
    template=rf"{_NS}:access:guild:(?P<guild_id>\d+)",
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
        from kingdoms.discord.guild_access_request import _request_options
        from kingdoms.discord.wiring import guild_access_platform

        guild_id = (_selected(interaction) or [""])[0]
        if not guild_id or guild_id == "none":
            await interaction.response.defer()
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        games, mods = guild_access_platform()
        options = _request_options() or [discord.SelectOption(label="Aucun jeu/mod actif", value="none")]
        service = await _access_service()
        granted: set[str] = set()
        if service is not None:
            doc = await service.get(guild_id)
            if doc:
                granted = {f"game:{k}" for k in doc.get("games") or []} | {f"mod:{k}" for k in doc.get("mods") or []}
        for option in options:
            option.default = option.value in granted
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
        keys_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        keys_row.add_item(AccessKeysSelect(guild_id, options))
        view.add_item(keys_row)
        act_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        act_row.add_item(AccessApplyButton(guild_id, grant=True))
        act_row.add_item(AccessApplyButton(guild_id, grant=False))
        view.add_item(act_row)
        await interaction.followup.send(view=view, ephemeral=True)


class AccessKeysSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:access:keys:(?P<guild_id>\d+)",
):
    """Pick the games/mods to grant or revoke (multi-select)."""

    def __init__(self, guild_id: str, options: list[discord.SelectOption]) -> None:
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:access:keys:{guild_id}"[:100],
                options=options,
                placeholder="Jeux/mods à accorder ou retirer...",
                min_values=1,
                max_values=min(25, len(options)),
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessKeysSelect:
        """Rebuild from the wire."""
        options = _current_options(interaction)
        del item
        return cls(match.group("guild_id"), options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Remember the selection (the apply buttons read it)."""
        await interaction.response.defer()


class AccessApplyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:access:apply:(?P<action>grant|revoke):(?P<guild_id>\d+)",
):
    """Apply the selected keys to the guild (grant or revoke)."""

    def __init__(self, guild_id: str, grant: bool = True) -> None:
        self.guild_id = guild_id
        self.grant = grant
        super().__init__(
            discord.ui.Button(
                label="Accorder" if grant else "Retirer",
                style=discord.ButtonStyle.success if grant else discord.ButtonStyle.danger,
                custom_id=f"{_NS}:access:apply:{'grant' if grant else 'revoke'}:{guild_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessApplyButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"), grant=match.group("action") == "grant")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the selection through the service, report the outcome."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        dm_locale = await _dm_locale(interaction)
        keys = _selected(interaction)
        if not keys:
            await interaction.response.send_message(
                _dm_tr(interaction, dm_locale, "access_select_first"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service = await _access_service()
        if service is None:
            await interaction.followup.send("Wiring indisponible.", ephemeral=True)
            return
        from kingdoms.core.services.guild_access import GuildAccessError

        try:
            if self.grant:
                doc = await service.grant(self.guild_id, keys)
            else:
                doc = await service.revoke_keys(self.guild_id, keys)
        except GuildAccessError as error:
            await interaction.followup.send(
                _dm_tr(interaction, dm_locale, "access_denied", error=error), ephemeral=True
            )
            return
        except Exception:
            logger.exception("DM ACCESS: direct apply failed (guild %s)", self.guild_id)
            await interaction.followup.send(_dm_tr(interaction, dm_locale, "access_failed"), ephemeral=True)
            return
        games = ", ".join(doc.get("games") or []) or "—"
        mods = ", ".join(doc.get("mods") or []) or "—"
        await interaction.followup.send(
            _dm_tr(
                interaction, await _dm_locale(interaction), "access_granted" if self.grant else "access_revoked",
                guild_id=self.guild_id, games=games, mods=mods,
            ),
            ephemeral=True,
        )


def _selected(interaction: discord.Interaction) -> list[str]:
    """Read the interaction's select values (payload-shape proof)."""
    from kingdoms.discord.admin_panel_games import _selected_values

    return list(_selected_values(interaction) or [])


def _current_options(interaction: discord.Interaction) -> list[discord.SelectOption]:
    """Rebuild a keys select's options from the clicked message."""
    raw = getattr(interaction, "data", None) or {}
    component = getattr(interaction, "message", None)
    options: list[discord.SelectOption] = []
    if component is not None:
        for row in getattr(component, "components", []) or []:
            for child in getattr(row, "children", []) or []:
                if getattr(child, "options", None):
                    options = list(child.options)
    del raw
    return options or [discord.SelectOption(label="Aucun jeu/mod actif", value="none")]


def register_admin_dm_items(bot: discord.Client) -> None:
    """Register the DM panel's persistent dynamic items."""
    bot.add_dynamic_items(AccessApproveButton)
    bot.add_dynamic_items(AccessDenyButton)
    bot.add_dynamic_items(ContentRefreshButton)
    bot.add_dynamic_items(AccessGrantButton)
    bot.add_dynamic_items(AccessGuildSelect)
    bot.add_dynamic_items(AccessKeysSelect)
    bot.add_dynamic_items(AccessApplyButton)
