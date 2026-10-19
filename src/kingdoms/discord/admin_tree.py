"""The admin menu: language, games, mods, and the provisioning ensure.

One ephemeral menu, four openers, every item a DynamicItem (the §3b
reconstruction contract — a click is restart-proof):

- ``admin:tree:lang`` — the guild's language (modal select);
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


async def _guild_locale(interaction: discord.Interaction) -> str:
    logs = getattr(interaction.client, "logs_service", None)
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    if logs is None or not guild_id:
        return "en"
    try:
        return str(await logs.get_locale(guild_id) or "en")
    except Exception:
        return "en"


async def _ensure_scope(bot: Any, guild_id: str, scope: str) -> str:
    """Re-provision one scope's declared channels, best-effort.

    Returns a human outcome: the scope's channels, listed like the
    home notices (one bullet per channel, its link and access band).
    """
    from kingdoms.core.services.i18n import DEFAULT_LOCALE
    from kingdoms.discord.ui.panels import registered_scopes
    from kingdoms.discord.ui.panels_provisioning import persist_resolutions, provision_scope

    guild = getattr(bot, "get_guild", lambda _gid: None)(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return "Guilde introuvable — impossible de re-provisionner."
    spec = next((s for s in registered_scopes() if s.scope == scope), None)
    if spec is None:
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
    try:
        resolved = await provision_scope(guild, spec, locale, _translate)
        await persist_resolutions(guild_id, scope, resolved)
    except Exception:
        logger.warning("ADMIN TREE: ensure failed (scope %s, guild %s)", scope, guild_id, exc_info=True)
        return "La création des salons a échoué — voir les logs du bot."
    from kingdoms.discord.pinned_views import refresh_registered_pins

    await refresh_registered_pins(guild_id)
    from kingdoms.discord.scope_pins import _ACCESS_LABELS

    lines: list[str] = [f"\U0001f527 **{scope}** — salons vérifiés/créés :"]
    for decl in spec.channels:
        channel_id = resolved.get(decl.key, "")
        where = f"<#{channel_id}>" if channel_id else "\u274c absent"
        access = _ACCESS_LABELS.get(decl.access, decl.access)
        lines.append(f"\u2022 {where} — {access}")
    return "\n".join(lines)


class TreeOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:open:(?P<section>[a-z]+)",
):
    """One admin menu button: opens its section's ephemeral view."""

    SECTIONS = ("lang", "games", "mods")

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
            "lang": "\U0001f310",
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
            "lang": "\U0001f310",
            "games": "\U0001f3ae",
            "mods": "\U0001f9e9",
        }.get(section, "\u2795")
        return cls(section, label, emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the section's view (guarded at click time)."""
        if not await _guard(interaction):
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        if self.section == "lang":
            # the modal IS the ack — deferring first raises InteractionResponded
            locale = await _guild_locale(interaction)
            modal = TreeLocaleModal(locale, await _tree_tr(interaction, "admin_tree.language"))
            try:
                await interaction.response.send_modal(modal)
                return
            except Exception:
                logger.warning("ADMIN TREE: language modal failed", exc_info=True)
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
    template=rf"{_NS}:ensure:(?P<scope>[a-z0-9_/]+)",
):
    """Re-provision one scope's channels (and mod/game roles), on demand."""

    def __init__(self, scope: str, label: str = "Create missing channels", emoji: str = "\U0001f527") -> None:
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
        ("lang", await _label("admin_tree.language_btn", "Langue"), "\U0001f310"),
        ("games", await _label("admin_tree.games_btn", "Jeux"), "\U0001f3ae"),
        ("mods", await _label("admin_tree.mods_btn", "Mods"), "\U0001f9e9"),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("# \U0001f6e0\ufe0f Admin"),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for section, label, emoji in buttons:
        row.add_item(TreeOpenButton(section, label, emoji))
    view.add_item(row)
    ensure_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    ensure_row.add_item(TreeEnsureButton("bot", await _label("admin_tree.ensure_btn", "Create missing channels")))
    view.add_item(ensure_row)
    view.add_item(discord.ui.TextDisplay(footer("view:bot:admin")))
    return view


def ensure_button(scope: str, label: str = "Create missing channels") -> TreeEnsureButton:
    """Build an ensure button for one scope (the scope admin menus)."""
    return TreeEnsureButton(scope, label)


class TreeLocaleModal(discord.ui.Modal):
    """The guild-language modal: a labelled select, applied on submit.

    The modal's title cannot render asynchronously, so the opener
    passes the localized title in (the click's locale); the submit
    persists the chosen value, refreshes the pinned views, and
    answers ephemerally.
    """

    def __init__(self, locale: str, title: str) -> None:
        super().__init__(title=title[:45], timeout=None)
        self.locale = locale
        self._select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"{_NS}:lang",
            options=[
                discord.SelectOption(label=label, value=code, default=code == locale)
                for code, label in LOCALE_LABELS.items()
            ],
            placeholder=LOCALE_LABELS.get(locale, locale),
        )
        self.add_item(discord.ui.Label(text=LOCALE_LABELS.get(locale, locale), component=self._select))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Apply the chosen locale: guard, persist, refresh, answer."""
        if not await _guard(interaction):
            return
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        value = ""
        for item in self.walk_children():
            select = getattr(item, "item", None) or item
            if isinstance(select, discord.ui.Select):
                value = str(select.values[0]) if select.values else ""
                break
        if not guild_id or not value:
            await interaction.response.defer(ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        logs = getattr(interaction.client, "logs_service", None)
        if logs is None:
            await interaction.followup.send(
                await _tree_tr(interaction, "admin_tree.admin_failed"), ephemeral=True
            )
            return
        try:
            await logs.set_locale(guild_id, value, by=str(interaction.user.id))
        except Exception:
            logger.warning("ADMIN TREE: locale set failed (guild %s)", guild_id, exc_info=True)
            await interaction.followup.send(
                await _tree_tr(interaction, "admin_tree.route_failed"), ephemeral=True
            )
            return
        from kingdoms.discord.pinned_views import refresh_registered_pins

        await refresh_registered_pins(guild_id)
        from kingdoms.discord.commands_i18n import reply

        await interaction.followup.send(
            await reply(interaction, "admin_replies.language_changed"), ephemeral=True
        )


def register_admin_tree_items(bot: discord.Client) -> None:
    """Register the admin menu's DynamicItems (every startup)."""
    bot.add_dynamic_items(TreeOpenButton)
    bot.add_dynamic_items(TreeEnsureButton)


__all__ = ["build_admin_menu", "ensure_button", "register_admin_tree_items"]
