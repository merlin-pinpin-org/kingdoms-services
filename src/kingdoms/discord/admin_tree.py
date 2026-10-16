"""The new admin menu: language, tree channels, roles (the sessions' design).

One ephemeral menu, three sections, every item a DynamicItem (the §3b
reconstruction contract — a click is restart-proof):

- ``admin:tree:lang`` — the guild's language (select);
- ``admin:tree:channels`` — the picker of every tree channel the bot
  manages; the sub-menu shows the
  channel's status and offers a reroute (a native channel select,
  since Discord's ChannelSelect cannot be pre-filtered) and a
  read-only lock toggle;
- ``admin:tree:roles`` — the roles view (core + function mappings),
  served by the existing roles surface.

The access (games/mods) management lives in the scope admin menus
(``games/admin``, ``mods/admin``) — never here.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.admin.tree")

_NS = "admin:tree"

_PIN_ROLES: dict[str, str] = {
    "general": "menu d'accueil \u00e9pingl\u00e9 (ce salon n'accueille que ce menu)",
    "admin": "menu d'administration \u00e9pingl\u00e9 (ce salon n'accueille que ce menu)",
}

# The three channel kinds of the tree (2026-10-16):
# - public discussions (read+write: everyone),
# - admin decisions/discussions (read+write: bot-admins or a mod's own role),
# - read-only menus (read: everyone, write: none — the pinned views live there).
async def _tree_tr(interaction: Any, key: str, **kwargs: Any) -> str:
    """Render one admin_tree key in the interaction's locale."""
    from kingdoms.discord.commands_i18n import reply

    return await reply(interaction, key, **kwargs)


_ACCESS_FALLBACKS = {
    "public": "Lecture : @everyone \u00b7 \u00c9criture : @everyone",
    "readonly": "Lecture : @everyone \u00b7 \u00c9criture : personne (menus \u00e9pingl\u00e9s uniquement)",
    "admin": "Lecture : bot-admins \u00b7 \u00c9criture : bot-admins",
}
_ACCESS_KEYS = {
    "public": "admin_tree.channel_access_everyone",
    "readonly": "admin_tree.channel_access_readonly",
    "admin": "admin_tree.channel_access_admin",
}


async def _audience(interaction: Any, access: str) -> str:
    """Render one channel's read/write access as a locale-aware line."""
    if interaction is None:
        return _ACCESS_FALLBACKS.get(access, _ACCESS_FALLBACKS["admin"])
    from kingdoms.discord.commands_i18n import tr

    return await tr(interaction, _ACCESS_KEYS.get(access, "admin_tree.channel_access_admin"))
def _pin_role(channel_key: str) -> str:
    """Describe the pinned view a channel hosts (empty when it hosts none)."""
    return _PIN_ROLES.get(channel_key, "")




def _tree_channels() -> list[tuple[str, str, str]]:
    """List every registered scope's channels: (scope, key, access)."""
    from kingdoms.discord.ui.panels import registered_scopes

    out: list[tuple[str, str, str]] = []
    for spec in registered_scopes():
        for decl in spec.channels:
            out.append((spec.scope, decl.key, decl.access))
    return out


def _channel_label(scope: str, key: str) -> str:
    """One channel's picker label: scope path and key."""
    return f"{scope}/{key}"[:100]


LOCALE_LABELS = {"en": "🇬🇧 English", "fr": "🇫🇷 Français"}


def _wiring(interaction: discord.Interaction) -> Any:
    from kingdoms.discord.admin_persistent import _wiring as panel_wiring

    return panel_wiring()


async def _guard(interaction: discord.Interaction) -> bool:
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    admins = tuple(getattr(getattr(bot, "status_service", None), "bot_admins", ()) or ())
    roles = getattr(bot, "roles_service", None)
    return await require_admin(interaction, admins, roles)


def _channel_value(scope: str, key: str) -> str:
    return f"{scope}|{key}"


def _parse_channel_value(value: str) -> tuple[str, str]:
    scope, _, key = value.partition("|")
    return scope, key


async def _channel_status(guild_id: str, scope: str, key: str) -> str:
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    try:
        channel_id = await stored_channel_id(guild_id, scope, key)
    except Exception:
        return "non provisionné"
    return f"<#{channel_id}>" if channel_id else "non provisionné"


async def build_admin_menu(
    guild_id: str, locale: str, interaction: Any = None
) -> discord.ui.LayoutView:
    """Build the new admin menu (ephemeral answer; stateless items)."""
    view = discord.ui.LayoutView(timeout=None)
    rows: list[Any] = []
    title_line = "# 🛠️ " + await _tree_tr(interaction, "admin_tree.title")
    subtitle_line = await _tree_tr(interaction, "admin_tree.subtitle")
    rows.append(discord.ui.TextDisplay(title_line + "\n" + subtitle_line))
    rows.append(discord.ui.Separator())
    rows.append(discord.ui.TextDisplay("## " + await _tree_tr(interaction, "admin_tree.language")))
    rows.append(_select_row(TreeLocaleSelect(locale)))
    rows.append(discord.ui.Separator())
    rows.append(discord.ui.TextDisplay("## " + await _tree_tr(interaction, "admin_tree.channels")))
    rows.append(_select_row(await TreeChannelSelect.create(guild_id)))
    rows.append(discord.ui.Separator())
    rows.append(discord.ui.TextDisplay("## " + await _tree_tr(interaction, "admin_tree.roles")))
    rows.append(_button_row(TreeRolesButton()))
    view.add_item(discord.ui.Container(*rows, accent_colour=discord.Colour(0x5865F2)))
    return view


def _select_row(item: Any) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(item)
    return row


def _button_row(item: Any) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(item)
    return row


class TreeLocaleSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:lang",
):
    """The guild language select of the new admin menu."""

    def __init__(self, locale: str = "en") -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:lang",
                options=[
                    discord.SelectOption(label=label, value=code, default=code == locale)
                    for code, label in LOCALE_LABELS.items()
                ],
                placeholder=LOCALE_LABELS.get(locale, locale),
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> TreeLocaleSelect:
        del item, match
        return cls(await _guild_locale(interaction))

    async def callback(self, interaction: discord.Interaction) -> None:
        from kingdoms.discord.admin_persistent import _handle_locale

        await _handle_locale(interaction)


async def _guild_locale(interaction: discord.Interaction) -> str:
    logs = getattr(interaction.client, "logs_service", None)
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if logs is None or not guild_id:
        return "en"
    try:
        return str(await logs.get_locale(guild_id) or "en")
    except Exception:
        return "en"


class TreeChannelSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:channels",
):
    """The tree-channel picker: one option per bot-managed channel."""

    def __init__(
        self,
        options: list[discord.SelectOption],
        placeholder: str = "Salon \u00e0 g\u00e9rer...",
        empty_label: str = "Aucun salon",
    ) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:channels",
                options=options or [discord.SelectOption(label=empty_label, value="none")],
                placeholder=placeholder,
            )
        )

    @classmethod
    async def create(cls, guild_id: str, interaction: Any = None) -> TreeChannelSelect:
        placeholder = await _tree_tr(interaction, "admin_tree.channel_to_manage")
        empty_label = await _tree_tr(interaction, "admin_tree.no_channel")
        options = []
        for scope, key, access in _tree_channels():
            status = await _channel_status(guild_id, scope, key)
            description = f"{await _audience(interaction, access)} · {status}"[:100]
            options.append(
                discord.SelectOption(
                    label=_channel_label(scope, key),
                    value=_channel_value(scope, key),
                    description=description,
                )
            )
        return cls(options[:25], placeholder=placeholder, empty_label=empty_label)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> TreeChannelSelect:
        del item, match
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        return await cls.create(guild_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _guard(interaction):
            return
        values = self.item.values or []
        chosen = str(values[0]) if values else ""
        if not chosen or chosen == "none":
            await interaction.response.defer(ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        scope, key = _parse_channel_value(chosen)
        await interaction.followup.send(
            view=await build_channel_menu(str(interaction.guild_id), scope, key),
            ephemeral=True,
        )


class TreeChannelRouteSelect(
    discord.ui.DynamicItem[discord.ui.ChannelSelect[Any]],
    template=rf"{_NS}:route:(?P<scope>[a-z0-9_/]+):(?P<key>[a-z0-9_-]+)",
):
    """Reroute one tree channel to an existing guild channel (admin-guarded)."""

    def __init__(self, scope: str, key: str, placeholder: str = "Nouveau salon pour ce point de gestion...") -> None:
        self.scope = scope
        self.key = key
        super().__init__(
            discord.ui.ChannelSelect(
                custom_id=f"{_NS}:route:{scope}:{key}"[:100],
                channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
                placeholder=placeholder,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> TreeChannelRouteSelect:
        return cls(match["scope"], match["key"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _guard(interaction):
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        values = self.item.values or []
        if not guild_id or not values:
            await interaction.response.defer(ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel_id = str(values[0].id)
        try:
            from kingdoms.core.models.db import get_async_database

            await get_async_database()["channels"].replace_one(
                {"_id": f"{guild_id}:tree:{self.scope}:{self.key}"},
                {
                    "_id": f"{guild_id}:tree:{self.scope}:{self.key}",
                    "guild_id": guild_id,
                    "platform": "discord",
                    "category": f"tree:{self.scope}:{self.key}",
                    "channel_id": channel_id,
                    "name": self.key,
                },
                upsert=True,
            )
        except Exception:
            logger.warning("ADMIN TREE: reroute persist failed", exc_info=True)
            await interaction.followup.send(await _tree_tr(interaction, "admin_tree.route_failed"), ephemeral=True)
            return
        await interaction.followup.send(
            f"Salon rerouté sur <#{channel_id}> — les menus épinglés suivront à la prochaine vérification.",
            ephemeral=True,
        )


class TreeRolesButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:roles",
):
    """Open the roles view (core roles + function mappings)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(label="Rôles", emoji="🧩", style=discord.ButtonStyle.primary, custom_id=f"{_NS}:roles")
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> TreeRolesButton:
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        from kingdoms.discord.admin_persistent import _handle_roles

        await _handle_roles(interaction)


def _channel_access(scope: str, key: str) -> str:
    """Resolve one tree channel's access band (public/readonly/admin)."""
    for tree_scope, tree_key, access in _tree_channels():
        if tree_scope == scope and tree_key == key:
            return access
    return "public"


async def build_channel_menu(guild_id: str, scope: str, key: str, interaction: Any = None) -> discord.ui.LayoutView:
    """One tree channel's sub-menu: status, audience, pinned view, reroute."""
    status = await _channel_status(guild_id, scope, key)
    access = _channel_access(scope, key)
    pin_role = _pin_role(key)
    lines = [f"Salon actuel : {status}", await _audience(interaction, access)]
    if pin_role:
        lines.append(f"Rôle : {pin_role}")
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [
        discord.ui.TextDisplay(f"# 📋 Salon géré — {scope}/{key}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(chr(10).join(lines)),
        discord.ui.Separator(),
    ]
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    route_placeholder = await _tree_tr(interaction, "admin_tree.route_placeholder")
    view.add_item(_select_row(TreeChannelRouteSelect(scope, key, placeholder=route_placeholder)))
    view.add_item(_button_row(TreeBackButton()))
    return view


class TreeBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:back",
):
    """Back to the admin menu from a channel sub-menu."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(label="Retour", style=discord.ButtonStyle.secondary, custom_id=f"{_NS}:back")
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> TreeBackButton:
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        locale = "en"
        logs = getattr(interaction.client, "logs_service", None)
        if logs is not None and guild_id:
            try:
                locale = str(await logs.get_locale(guild_id) or "en")
            except Exception:
                locale = "en"
        await interaction.followup.send(view=await build_admin_menu(guild_id, locale), ephemeral=True)


def register_admin_tree_items(bot: discord.Client) -> None:
    """Register the new admin menu's DynamicItems (every startup)."""
    bot.add_dynamic_items(TreeLocaleSelect)
    bot.add_dynamic_items(TreeChannelSelect)
    bot.add_dynamic_items(TreeChannelRouteSelect)
    bot.add_dynamic_items(TreeRolesButton)
    bot.add_dynamic_items(TreeBackButton)


__all__ = ["build_admin_menu", "register_admin_tree_items"]
