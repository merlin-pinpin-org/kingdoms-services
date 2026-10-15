"""The new admin menu: language, tree channels, roles (the sessions' design).

One ephemeral menu, three sections, every item a DynamicItem (the §3b
reconstruction contract — a click is restart-proof):

- ``admin:tree:lang`` — the guild's language (select);
- ``admin:tree:channels`` — the picker of every tree channel the bot
  manages (autopin/autoheal by design); the sub-menu shows the
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

_TREE_CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("root", "general", "🏠 Racine — général"),
    ("root", "discussions", "💬 Racine — discussions"),
    ("bot", "logs", "📜 Bot — logs"),
    ("bot", "admin", "🛠️ Bot — admin"),
    ("bot", "decisions", "⚖️ Bot — décisions"),
    ("games", "general", "🎮 Games — général"),
    ("games", "admin", "🛠️ Games — admin"),
    ("games/aoe2", "general", "⚔️ AoE2 — général"),
    ("games/aoe2", "live", "📡 AoE2 — live"),
    ("games/aoe2", "civs", "🏛️ AoE2 — civs (forum)"),
    ("games/aoe2", "maps", "🌍 AoE2 — maps (forum)"),
    ("games/aoe2", "map-pools", "🧱 AoE2 — map pools (forum)"),
    ("games/aoe2", "admin", "🛠️ AoE2 — admin"),
    ("mods", "general", "🧩 Mods — général"),
    ("mods", "admin", "🛠️ Mods — admin"),
)

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


async def build_admin_menu(guild_id: str, locale: str) -> discord.ui.LayoutView:
    """Build the new admin menu (ephemeral answer; stateless items)."""
    view = discord.ui.LayoutView(timeout=None)
    rows: list[Any] = []
    rows.append(discord.ui.TextDisplay("# 🛠️ Admin\nLangue, salons et rôles de la guilde."))
    rows.append(discord.ui.Separator())
    rows.append(discord.ui.TextDisplay("## 🌐 Langue"))
    rows.append(_select_row(TreeLocaleSelect(locale)))
    rows.append(discord.ui.Separator())
    rows.append(discord.ui.TextDisplay("## 📋 Salons du bot (autopin · autoheal)"))
    rows.append(_select_row(await TreeChannelSelect.create(guild_id)))
    rows.append(discord.ui.Separator())
    rows.append(discord.ui.TextDisplay("## 🧩 Rôles"))
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

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:channels",
                options=options or [discord.SelectOption(label="Aucun salon", value="none")],
                placeholder="Salon à gérer...",
            )
        )

    @classmethod
    async def create(cls, guild_id: str) -> TreeChannelSelect:
        options = []
        for scope, key, label in _TREE_CHANNELS:
            status = await _channel_status(guild_id, scope, key)
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=_channel_value(scope, key),
                    description=status[:100],
                )
            )
        return cls(options[:25])

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

    def __init__(self, scope: str, key: str) -> None:
        self.scope = scope
        self.key = key
        super().__init__(
            discord.ui.ChannelSelect(
                custom_id=f"{_NS}:route:{scope}:{key}"[:100],
                channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
                placeholder="Nouveau salon pour ce point de gestion...",
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
            await interaction.followup.send("Reroutage impossible (voir les logs).", ephemeral=True)
            return
        await interaction.followup.send(
            f"Salon rerouté sur <#{channel_id}> — les messages épinglés suivent au prochain passage de l'autoheal.",
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


async def build_channel_menu(guild_id: str, scope: str, key: str) -> discord.ui.LayoutView:
    """One tree channel's sub-menu: status, reroute, back."""
    status = await _channel_status(guild_id, scope, key)
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [
        discord.ui.TextDisplay(f"# 📋 Salon géré — {scope}/{key}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"Salon actuel : {status}\nCe point de gestion est autopin + autoheal."),
        discord.ui.Separator(),
    ]
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    view.add_item(_select_row(TreeChannelRouteSelect(scope, key)))
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
