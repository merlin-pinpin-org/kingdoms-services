"""The admin menu: language, games, mods, and the provisioning ensure.

One ephemeral menu, three openers, every item a DynamicItem (the §3b
reconstruction contract — a click is restart-proof):

- ``admin:tree:games`` / ``admin:tree:mods`` — the scope admin menus
  (the shared admin dispatches);
- ``admin:tree:ensure:<scope>`` — re-provision one scope's channels
  (and the mod/game roles) and refresh the pinned menus.

The roles/channel-management views are gone: the tree is declared and
provisioned (panels_scopes + panels_provisioning), the admin surfaces
carry an ensure button instead, and the homes explain the layout in a
contextual notice.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.admin.tree")

_NS = "admin:tree"

LOCALE_LABELS = {"en": "\U0001f1ec\U0001f1e7 English", "fr": "\U0001f1eb\U0001f1f7 Français"}


async def _tree_tr(interaction: Any, key: str, **kwargs: Any) -> str:
    """Render one admin_tree key in the interaction's locale."""
    from kingdoms.discord.commands_i18n import reply

    return await reply(interaction, key, **kwargs)


def _wiring(interaction: discord.Interaction) -> Any:
    from kingdoms.discord.admin_persistent import _wiring as panel_wiring

    return panel_wiring()


async def _guard(interaction: discord.Interaction) -> bool:
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    admins = tuple(getattr(getattr(bot, "status_service", None), "bot_admins", ()) or ())
    roles = getattr(bot, "roles_service", None)
    return await require_admin(interaction, admins, roles)


async def _ensure_scope_pins(bot: Any, guild_id: str, scope: str) -> int:
    """Ensure every pinned view registered for the scope (and its sub-scopes).

    A deleted pin is recreated: the pin is only a mirror of its view.
    Best-effort per pin; returns how many pins were (re-)created.
    """
    from kingdoms.discord.static_pins import ensure_static_pin, registered_static_pins

    ensured = 0
    for pin in registered_static_pins():
        if not pin.scope or not (pin.scope == scope or pin.scope.startswith(scope + "/")):
            continue
        try:
            if await ensure_static_pin(bot, pin, guild_id):
                ensured += 1
        except Exception:
            logger.warning("pin ensure failed (%s) \u2014 best-effort", pin.key, exc_info=True)
    return ensured


async def _ensure_scope(bot: Any, guild_id: str, scope: str) -> str:
    """Re-provision the scopes' declared channels, best-effort.

    ``scope`` carries a comma-separated list (the button's contract):
    every scope provisions in order, then the pins and the declared
    forum syncs of the whole list run. Returns a human outcome.
    """
    from kingdoms.core.services.i18n import DEFAULT_LOCALE
    from kingdoms.discord.ui.panels import registered_scopes
    from kingdoms.discord.ui.panels_provisioning import persist_resolutions, provision_scope

    guild = getattr(bot, "get_guild", lambda _gid: None)(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return "Guilde introuvable — impossible de re-provisionner."
    scopes = [part for part in scope.split(",") if part]
    specs = [s for s in registered_scopes() if s.scope in scopes]
    if not specs:
        return f"Périmètre inconnu : `{scope}`."
    catalog = getattr(bot, "messages", None)

    def _translate(key: str, locale: str) -> str:
        if catalog is None:
            return key
        rendered: str = catalog.render(key, locale)
        return rendered if rendered != key else catalog.render(key, "en")

    logs = getattr(bot, "logs_service", None)
    locale = DEFAULT_LOCALE
    if logs is not None:
        try:
            locale = str(await logs.get_locale(guild_id) or DEFAULT_LOCALE)
        except Exception:
            locale = DEFAULT_LOCALE
    lines: list[str] = []
    pins_ok = 0
    for spec in specs:
        try:
            resolved = await provision_scope(guild, spec, locale, _translate)
            await persist_resolutions(guild_id, spec.scope, resolved)
        except Exception:
            logger.warning("ADMIN TREE: ensure failed (scope %s, guild %s)", spec.scope, guild_id, exc_info=True)
            return "La création des salons a échoué — voir les logs du bot."
        lines.append(f"\U0001F527 **{spec.scope}** \u2014 salons v\u00e9rifi\u00e9s/cr\u00e9\u00e9s :")
        for decl in spec.channels:
            channel_id = resolved.get(decl.key, "")
            lock = "\U0001F512" if decl.access == "admin" else ""
            where = f"<#{channel_id}>{lock}" if channel_id else f"**{decl.key}** (absent)"
            lines.append(f"\u2022 {where}")
        pins_ok += await _ensure_scope_pins(bot, guild_id, spec.scope)
    from kingdoms.discord.scope_surfaces import run_scope_syncs

    synced_posts = await run_scope_syncs(guild_id, bot, ",".join(scopes))
    lines.append(f"\n\U0001f4cc Vues \u00e9pingl\u00e9es v\u00e9rifi\u00e9es/recr\u00e9\u00e9es : {pins_ok}")
    lines.append(f"\U0001f4dc Posts de forum v\u00e9rifi\u00e9s/recr\u00e9\u00e9s : {synced_posts}")
    return "\n".join(lines)


class TreeOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:open:(?P<section>[a-z]+)",
):
    """One admin menu button: opens its section's ephemeral view."""

    SECTIONS = ("games", "mods")

    def __init__(self, section: str, label: str, emoji: str) -> None:
        self.section = section
        super().__init__(
            discord.ui.Button(
                custom_id=f"{_NS}:open:{section}",
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> TreeOpenButton:
        """Rebuild the opener from the wire (label from the locale)."""
        section = match.group("section")
        label = await _tree_tr(interaction, f"admin_tree.{section}_btn")
        emoji = {
            "games": "\U0001f3ae",
            "mods": "\U0001f9e9",
        }.get(section, "\u2795")
        return cls(section, label, emoji)

    @classmethod
    async def create(cls, bot: Any, guild_id: str, section: str) -> TreeOpenButton:
        """Build an opener for a pinned menu (locale from the guild's logs)."""
        from kingdoms.discord.commands_i18n import tr_guild

        label = await tr_guild(guild_id, bot, f"admin_tree.{section}_btn")
        emoji = {
            "games": "\U0001f3ae",
            "mods": "\U0001f9e9",
        }.get(section, "\u2795")
        return cls(section, label, emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the section's view (guarded at click time)."""
        if not await _guard(interaction):
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.section in ("games", "mods"):
            from kingdoms.discord.scope_pins import _admin_builder

            builder = _admin_builder(interaction.client, self.section)
            try:
                scope_view = await builder(guild_id)
            except Exception:
                logger.warning("ADMIN TREE: scope admin view failed (%s)", self.section, exc_info=True)
                await interaction.followup.send(
                    await _tree_tr(interaction, "admin_tree.route_failed"), ephemeral=True
                )
                return
            await interaction.followup.send(view=scope_view, ephemeral=True)


class TreeEnsureButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:ensure:(?P<scope>[a-z0-9_/,]+)",
):
    """Re-provision scopes' channels (and mod/game roles), on demand.

    The custom id carries a comma-separated scope list: the sync of an
    admin menu heals its own scope plus anything it owns (the bot menu
    also carries ``root`` — the guild's root channels belong to no
    sub-scope).
    """

    def __init__(self, scope: str, label: str = "Sync now", emoji: str = "\U0001F527") -> None:
        self.scope = scope
        super().__init__(
            discord.ui.Button(
                custom_id=f"{_NS}:ensure:{scope}"[:100],
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> TreeEnsureButton:
        """Rebuild the ensure button from the wire."""
        del interaction, item
        return cls(match["scope"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the provisioning ensure and answer with the outcome."""
        if not await _guard(interaction):
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        await interaction.response.defer(ephemeral=True, thinking=True)
        answer = await _ensure_scope(interaction.client, guild_id, self.scope)
        await interaction.followup.send(answer, ephemeral=True)


async def build_admin_menu(
    guild_id: str, locale: str, interaction: Any = None
) -> discord.ui.LayoutView:
    """Build the light admin menu: lang/games/mods openers + ensure."""
    from kingdoms.core.ids import footer
    from kingdoms.discord.commands_i18n import tr_guild

    bot = getattr(interaction, "client", None) if interaction is not None else None

    async def _label(key: str, fallback: str) -> str:
        if bot is not None and guild_id:
            rendered = await tr_guild(guild_id, bot, f"replies.{key}", fallback)
            return rendered
        return fallback

    buttons = (
        ("games", await _label("admin_tree.games_btn", "Jeux"), "\U0001f3ae"),
        ("mods", await _label("admin_tree.mods_btn", "Mods"), "\U0001f9e9"),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                await tr_guild(guild_id, bot, "ui.views.menu.admin.title", "Admin")
            ),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for section, label, emoji in buttons:
        row.add_item(TreeOpenButton(section, label, emoji))
    view.add_item(row)
    ensure_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    ensure_row.add_item(TreeEnsureButton("bot", await _label("admin_tree.ensure_btn", "Sync now")))
    view.add_item(ensure_row)
    view.add_item(discord.ui.TextDisplay(footer("view:bot:admin")))
    return view


def ensure_button(scope: str, label: str = "Sync now") -> TreeEnsureButton:
    """Build an ensure button for one scope (the scope admin menus)."""
    return TreeEnsureButton(scope, label)


def register_admin_tree_items(bot: discord.Client) -> None:
    """Register the admin menu's DynamicItems (every startup)."""
    bot.add_dynamic_items(TreeOpenButton)
    bot.add_dynamic_items(TreeEnsureButton)


__all__ = ["build_admin_menu", "ensure_button", "register_admin_tree_items"]
