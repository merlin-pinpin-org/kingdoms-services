"""The home surface: /home + the pinned 🏛 menu, one ephemeral view per button.

The home is the guild's front door: a pinned menu in the 🏛-home
channel (provisioned like every managed channel), plus the /home command
showing the same menu. Every button answers with an **ephemeral** view —
the home is public, the answers are personal.

Namespace uniqueness (the §3b rule): the pinned home rides the
``home:`` namespace, served only by the DynamicItems declared here — no
live-closure view is registered for the pinned message, so a click has
exactly one dispatch path, restart-proof.

- ``home:pin:menu``   — the marker identifying the pinned message;
- ``home:open:<view>`` — one button per view (status, profile, games,
  users, admin, mod:<name>).

The standard views render the platform's own data (the /status layout,
the core profile bindings, the provider status); a mod view routes to
the mod's home view builder when the mod provides one.

The pinned message is **never rebuilt while it lives**: its id is
registered in the message registry (``home-menu`` key, per guild) and
the periodic check only verifies the registered message still exists
(re-pinning it if it was unpinned) - the menu is recreated solely when
the registered message is gone (deleted, channel wiped). Without a
registry (local runs), an in-memory store provides the same lifecycle
for the process's lifetime.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.home import HomeService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.commands_i18n import localized, reply

logger = logging.getLogger("kingdoms.home")

HOME_CHANNEL_NAME = "🏛-home"
HOME_CHANNEL_CATEGORY = "bot_home"
HOME_MARKER = "home:pin:"
HOME_MESSAGE_KEY = "view:home"
HOME_MESSAGE_LEGACY_KEY = "home-menu"
HOME_MESSAGE_PLATFORM = "discord"


class HomeButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=r"home:open:(?P<view>[a-z0-9:_-]+)"):
    """One persistent home button: opens an ephemeral view at click time."""

    def __init__(self, view_key: str, label: str, emoji: str) -> None:
        super().__init__(
            discord.ui.Button(
                custom_id=f"home:open:{view_key}",
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
            )
        )
        self.view_key = view_key

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> HomeButton:
        """Rebuild the button from the custom_id at click time."""
        del interaction
        button = item if isinstance(item, discord.ui.Button) else None
        emoji = ""
        label = "?"
        if button is not None:
            label = button.label or "?"
            emoji = button.emoji.name if isinstance(button.emoji, discord.PartialEmoji) else ""
        return cls(match.group("view"), label, emoji)
        return cls(match.group("view"), item.label or "?", emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the view at click time (ephemeral)."""
        await open_home_view(interaction, self.view_key)


def build_home_menu(home: HomeService) -> discord.ui.LayoutView:
    """Build the home menu layout: one button per home view."""
    rows: list[discord.ui.ActionRow[discord.ui.LayoutView]] = []
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for i, button in enumerate(home.buttons()):
        if i and i % 5 == 0:
            rows.append(row)
            row = discord.ui.ActionRow()
        row.add_item(
            discord.ui.Button(
                custom_id=f"home:open:{button.view}",
                label=button.label,
                emoji=discord.PartialEmoji.from_str(button.emoji) if button.emoji else None,
                style=discord.ButtonStyle.secondary,
            )
        )
    rows.append(row)
    from kingdoms.discord.pinned_marks import pinned_mark

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("## 🏛 Kingdoms\nBienvenue — chaque bouton ouvre une vue réservée à toi."),
            *rows,
            discord.ui.TextDisplay(pinned_mark(HOME_MESSAGE_KEY)),
        )
    )
    return view


async def open_home_menu(interaction: discord.Interaction) -> None:
    """Answer with the home menu itself (the back target of mod views)."""
    home = getattr(interaction.client, "home_service", None)
    if home is None:
        await interaction.response.send_message(await reply(interaction, "menu_unavailable"), ephemeral=True)
        return
    await interaction.response.send_message(view=build_home_menu(home), ephemeral=True)


async def open_home_view(interaction: discord.Interaction, view_key: str) -> None:
    """Answer a home click with the matching ephemeral view."""
    simple = {
        "home": open_home_menu,
        "status": _view_status,
        "profile": _view_profile,
        "games": _view_games,
        "mods": _view_mods,
        "users": _view_users,
        "admin": _view_admin,
    }
    handler = simple.get(view_key)
    if handler is not None:
        await handler(interaction)
    elif view_key.startswith("guild-admin:"):
        await _view_admin_for_guild(interaction, view_key[len("guild-admin:") :])
    elif view_key.startswith("guild-games:"):
        await _view_game_for_guild(interaction, view_key[len("guild-games:") :])
    elif view_key.startswith("mod:"):
        await _view_mod(interaction, view_key[4:])
    else:
        await interaction.response.send_message(await reply(interaction, "unknown_view"), ephemeral=True)


async def _view_status(interaction: discord.Interaction) -> None:
    """Render the /status layout, ephemeral."""
    await interaction.response.defer(ephemeral=True)
    from pathlib import Path

    from kingdoms.discord.announce import AnnounceConfig, build_announcement_layout
    from kingdoms.discord.bot.factory import KingdomsBot

    bot = interaction.client
    if not isinstance(bot, KingdomsBot):
        await interaction.followup.send("Status indisponible.", ephemeral=True)
        return
    status = bot.status_service
    latency_raw: float | None = bot.latency if bot.latency == bot.latency and bot.latency != float("inf") else None
    layout = build_announcement_layout(
        status,
        AnnounceConfig(config_dir=Path(bot.config.config_dir)),
        env=bot.config.deploy_env,
        latency_ms=round(latency_raw * 1000) if latency_raw is not None else None,
    )
    await interaction.followup.send(view=layout, ephemeral=True)


async def _view_profile(interaction: discord.Interaction) -> None:
    """Render the user's games, linked accounts (name + elo) and actions."""
    registration = getattr(interaction.client, "registration_service", None)
    if registration is None:
        await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _profile_followup(interaction)


async def _profile_followup(interaction: discord.Interaction) -> None:
    """Send the profile view as a followup (the response is already acked)."""
    registration = getattr(interaction.client, "registration_service", None)
    if registration is None:
        await interaction.followup.send(await reply(interaction, "not_configured"), ephemeral=True)
        return
    user_id = str(interaction.user.id)
    bindings = await registration.list_bindings(user_id)
    blocks: list[Any] = []
    if not bindings:
        blocks.append(discord.ui.TextDisplay("Aucun compte de jeu lié — ajoute-en un ci-dessous."))
    by_game: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        by_game.setdefault(str(binding.get("game_key", "?")), []).append(binding)
    players_by_id: dict[str, dict[str, Any]] = {}
    enrichers: dict[str, Any] = getattr(interaction.client, "mod_profile_enrichers", {}) or {}
    for enricher in enrichers.values():
        try:
            players_by_id.update(await enricher(interaction.client) or {})
        except Exception:
            logger.warning("HOME: profile enricher failed", exc_info=True)
    for game, entries in sorted(by_game.items()):
        lines = [f"## {game}"]
        for entry in entries:
            profile = entry.get("profile") or {}
            aliases = [str(a) for a in (profile.get("aliases") or []) if str(a)]
            name = (
                str(profile.get("display_name") or profile.get("name") or "")
                or (aliases[0] if aliases else "")
                or "?"
            )
            pid = entry.get("profile_id", "?")
            player = players_by_id.get(user_id)
            elo = f" — {player.get('rating')} elo" if player else ""
            lines.append(f"• {name} (`{pid}`){elo}")
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view = discord.ui.LayoutView(timeout=None)
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(ProfileAddAccountButton())
    row.add_item(ProfileRenameButton())
    if bindings:
        row.add_item(ProfileRemoveAccountButton())
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(await _profile_header(interaction, user_id)), *blocks))
    view.add_item(row)
    await interaction.followup.send(view=view, ephemeral=True)




PROFILE_PROVIDER = "ext-librematch (aoe2)"


async def _report_profile_error(
    interaction: discord.Interaction,
    action: str,
    game_key: str,
    exc: BaseException,
) -> None:
    """Log the precise failure and DM the admins (spam-guarded)."""
    import traceback

    logger.warning("PROFILE %s failed (game %s): %s", action, game_key, exc, exc_info=True)
    bot = interaction.client
    admins = tuple(getattr(getattr(bot, "status_service", None), "bot_admins", ()) or ())
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=8))
    content = (
        f"**Erreur profil \u2014 {action}**\n"
        f"Jeu : `{game_key}` \u00b7 Provider : `{PROFILE_PROVIDER}`\n"
        f"Utilisateur : <@{interaction.user.id}>\n"
        f"Erreur : `{type(exc).__name__}: {exc}`\n"
        f"```\n{detail[:1500]}\n```"
    )
    from kingdoms.discord.error_report import dm_admins

    with contextlib.suppress(Exception):
        if admins:
            await dm_admins(bot, admins, content)


class ProfileAddAccountButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:add",
):
    """Open the add-account modal (profile id)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Ajouter un compte", style=discord.ButtonStyle.success, custom_id="home:profile:add"
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ProfileAddAccountButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the add-account modal (game options, aoe2-only fallback)."""
        import asyncio

        try:
            options = await asyncio.wait_for(_game_select_options(), timeout=2.5)
        except Exception:
            options = None
        await interaction.response.send_modal(ProfileAddAccountModal(options))


async def _game_select_options() -> list[discord.SelectOption]:
    """Build the game picker's options: the known games, aoe2 first."""
    options = [discord.SelectOption(label="Age of Empires II", value="aoe2", default=True)]
    try:
        from kingdoms.discord.admin_panel_games import _games_wiring

        service = _games_wiring()
        if service is not None:
            for key in [g for g in await service.list_game_keys() if g != "aoe2"]:
                options.append(discord.SelectOption(label=key, value=key))
    except Exception:
        logger.warning("PROFILE ADD: game list read failed — aoe2 only", exc_info=True)
    return options[:25]



class ProfileAddAccountModal(discord.ui.Modal):
    """The add-account form: game select (aoe2 preselected) + profile id."""

    def __init__(self, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(title="Ajouter un compte", timeout=None)
        self.game: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="home:profile:add:game",
            options=options or [discord.SelectOption(label="Age of Empires II", value="aoe2", default=True)],
            placeholder="Jeu du compte...",
            min_values=1,
            max_values=1,
        )
        self.profile_id: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Ton id aoe2insights ou ton relic id",
            placeholder="Ex. 17190369 ou https://www.aoe2insights.com/user/12488557/",
            max_length=64,
            required=True,
        )
        self.add_item(discord.ui.Label(text="Jeu du compte", component=self.game))
        self.add_item(self.profile_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Bind the account, audit, confirm, then refresh the profile view."""
        registration = getattr(interaction.client, "registration_service", None)
        if registration is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        chosen = (self.game.values or ["aoe2"])[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await registration.bind_profile(
                str(interaction.user.id), str(chosen), str(self.profile_id.value).strip()
            )
        except Exception as exc:
            await _report_profile_error(interaction, "profile.add", chosen, exc)
            reason = str(exc).lower()
            if "déjà" in reason or "already" in reason:
                note = "Ce profil est déjà lié — retire-le d'abord ou choisis un autre profil."
            elif "no profile seam" in reason or "unknown game" in reason:
                note = f"Le jeu {chosen} n'est pas configuré pour l'inscription."
            elif "player_stats" in reason or "mal configur" in reason:
                note = (
                    f"Ajout échoué : {PROFILE_PROVIDER} ne répond pas correctement "
                    "— le détail part aux admins (vois les logs du bot)."
                )
            elif "valid" in reason or "inconnu" in reason:
                note = (
                    f"Ajout échoué : aucun profil trouvé pour "
                    f"`{str(self.profile_id.value).strip()}`. Colle l'URL de ta page "
                    "aoe2insights (ou l'id qu'elle contient) \u2014 le bon id sera "
                    "récupéré automatiquement."
                )
            else:
                note = (
                    f"Ajout échoué : le provider {PROFILE_PROVIDER} n'a pas répondu "
                    "— l'erreur précise part aux admins."
                )
            await interaction.followup.send(note, ephemeral=True)
            return
        await interaction.followup.send(await reply(interaction, "profile_linked"), ephemeral=True)
        await _profile_followup(interaction)


async def _profile_header(interaction: discord.Interaction, user_id: str) -> str:
    """Build the profile header: the user's chosen name when known."""
    identity = getattr(interaction.client, "identity_service", None)
    if identity is None:
        return "# 👤 Ton profil — pseudo : (non défini)"
    try:
        user = await identity.get_user(f"discord:{user_id}")
    except Exception:
        logger.warning("HOME: identity lookup failed", exc_info=True)
        return "# 👤 Ton profil — pseudo : (non défini)"
    if user is None or not user.display_name:
        return "# 👤 Ton profil — pseudo : (non défini)"
    return f"# 👤 Ton profil — pseudo : {user.display_name}"


class ProfileRenameButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:rename",
):
    """Open the rename modal (the user's chosen gamer name)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Changer de pseudo", style=discord.ButtonStyle.secondary, custom_id="home:profile:rename"
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ProfileRenameButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the rename modal, prefilled with the current nickname."""
        import asyncio

        current = ""
        identity = getattr(interaction.client, "identity_service", None)
        if identity is not None:
            try:
                user = await asyncio.wait_for(identity.get_user(f"discord:{interaction.user.id}"), timeout=2.5)
                current = user.display_name if user is not None and user.display_name else ""
            except Exception:
                current = ""
        await interaction.response.send_modal(ProfileRenameModal(current))


class ProfileRenameModal(discord.ui.Modal):
    """The rename form: the user's chosen name (1-16 chars)."""

    def __init__(self, current: str = "") -> None:
        super().__init__(title="Ton pseudo", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Pseudo (16 caractères max)",
            max_length=16,
            min_length=1,
            required=True,
            default=current or None,
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Persist the new name, confirm, then refresh the profile view."""
        identity = getattr(interaction.client, "identity_service", None)
        if identity is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        user_id = f"discord:{interaction.user.id}"
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            user = await identity.get_user(user_id)
            if user is None:
                user = await identity.get_or_create_user(
                    platform="discord",
                    platform_user_id=str(interaction.user.id),
                    display_name=str(interaction.user.display_name),
                )
            await identity.set_display_name(user.id, str(self.name.value))
        except ValueError:
            await interaction.followup.send("Le pseudo doit faire entre 1 et 16 caractères.", ephemeral=True)
            return
        except Exception:
            logger.warning("PROFILE RENAME failed", exc_info=True)
            await interaction.followup.send("Renommage impossible pour le moment.", ephemeral=True)
            return
        await interaction.followup.send(f"Pseudo mis à jour ✅ — {self.name.value}", ephemeral=True)
        await _profile_followup(interaction)


def _remove_options(bindings: list[dict[str, Any]]) -> list[discord.SelectOption]:
    """Build the remove modal's options: name + game + profile id."""
    options: list[discord.SelectOption] = []
    for b in bindings[:25]:
        profile = b.get("profile") or {}
        label = str(
            profile.get("display_name")
            or next(iter(profile.get("aliases") or []), None)
            or b.get("profile_id", "?")
        )[:100]
        options.append(
            discord.SelectOption(
                label=label,
                value=f"{b.get('game_key', '')}|{b.get('profile_id', '')}",
                description=f"{b.get('game_key', '?')} \u00b7 {b.get('profile_id', '?')}"[:100],
            )
        )
    return options


class ProfileRemoveAccountButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:remove",
):
    """Open the remove-account modal (multi-select of the linked accounts)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Retirer un compte", style=discord.ButtonStyle.danger, custom_id="home:profile:remove"
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ProfileRemoveAccountButton:
        """Rebuild the item from the wire (bindings resolve at click time)."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the multi-select remove modal (accounts read fast, aoe2 cap)."""
        import asyncio

        registration = getattr(interaction.client, "registration_service", None)
        if registration is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        try:
            bindings = await asyncio.wait_for(registration.list_bindings(str(interaction.user.id)), timeout=2.5)
        except Exception:
            bindings = []
        if not bindings:
            await interaction.response.send_message(await reply(interaction, "no_account"), ephemeral=True)
            return
        await interaction.response.send_modal(ProfileRemoveAccountModal(_remove_options(bindings)))


class ProfileRemoveAccountModal(discord.ui.Modal):
    """The remove-account form: one multi-select of the linked accounts."""

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(title="Retirer des comptes", timeout=None)
        self.accounts: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="home:profile:remove:select",
            options=options,
            placeholder="Comptes \u00e0 retirer...",
            min_values=1,
            max_values=len(options),
        )
        self.add_item(discord.ui.Label(text="Comptes \u00e0 retirer", component=self.accounts))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Unlink every chosen account, then refresh the profile view."""
        registration = getattr(interaction.client, "registration_service", None)
        if registration is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        chosen = list(self.accounts.values or [])
        await interaction.response.defer(ephemeral=True, thinking=True)
        removed: list[str] = []
        failures: list[str] = []
        for entry in chosen:
            game_key, _, profile_id = str(entry).partition("|")
            try:
                if await registration.unlink_profile(str(interaction.user.id), game_key, profile_id):
                    removed.append(f"{game_key} (`{profile_id}`)")
                else:
                    failures.append(f"{game_key} `{profile_id}` (introuvable)")
                    await _report_profile_error(
                        interaction, "profile.remove", game_key, RuntimeError(f"binding not found: {entry}")
                    )
            except Exception as exc:
                failures.append(f"{game_key} `{profile_id}` ({type(exc).__name__})")
                await _report_profile_error(interaction, "profile.remove", game_key, exc)
        if removed:
            listed = ", ".join(removed)
            await interaction.followup.send(f"Comptes retir\u00e9s : {listed}.", ephemeral=True)
        if failures:
            await interaction.followup.send(
                "Retrait \u00e9chou\u00e9 pour : " + ", ".join(failures) + " \u2014 le d\u00e9tail part aux admins.",
                ephemeral=True,
            )
        if removed:
            await _profile_followup(interaction)


async def _view_games(interaction: discord.Interaction) -> None:
    """Render the known games, their catalog sizes and provider status."""
    from kingdoms.discord.guild_context import require_guild_context

    guild_id = await require_guild_context(interaction, "guild-games:games")
    if guild_id is None:
        return
    await _view_games_for_guild(interaction, guild_id)


async def _tree_channel_link(guild_id: str, scope: str, key: str) -> str:
    """Resolve one tree channel into a Discord mention (empty when unknown)."""
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    channel_id = await stored_channel_id(guild_id, scope, key)
    return f"<#{channel_id}>" if channel_id else ""




async def _granted_games_with_counts(
    guild_id: str, fallback: tuple[str, ...]
) -> tuple[tuple[str, ...], dict[str, tuple[int, int, int]]]:
    """Read the guild's granted games with their catalog counts."""
    from kingdoms.discord.maps_forum import maps_forum_wiring_ready

    games = fallback
    counts: dict[str, tuple[int, int, int]] = {}
    if not maps_forum_wiring_ready():
        return games, counts
    try:
        from kingdoms.discord.admin_panel_games import _games_wiring
        from kingdoms.discord.wiring import granted_game_keys

        service = _games_wiring()
        if service is None:
            return games, counts
        games = await granted_game_keys(guild_id, tuple(await service.list_game_keys()))
        for key in games:
            civs = sum(1 for c in await service.list_factions(key) if c.archived_at is None)
            maps = sum(1 for m in await service.list_maps(key) if m.archived_at is None)
            pools = sum(1 for pl in await service.list_map_pools(key) if pl.archived_at is None)
            counts[key] = (civs, maps, pools)
    except Exception:
        logger.warning("GAMES VIEW failed to read the catalog", exc_info=True)
    return games, counts


async def _view_games_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Render the guild's games: one section per game, links, counts."""
    from kingdoms.discord.bot.factory import KingdomsBot

    bot = interaction.client
    await interaction.response.defer(ephemeral=True)
    games = bot.status_service.games() if isinstance(bot, KingdomsBot) else ()
    games, counts = await _granted_games_with_counts(guild_id, games)

    header = "# \U0001f3ae Jeux\nLes jeux du serveur, leurs donn\u00e9es et leurs salons."
    blocks: list[Any] = [discord.ui.TextDisplay(header)]
    if not games:
        blocks.append(discord.ui.TextDisplay("_Aucun jeu configur\u00e9._"))
    for game in games[:25]:
        civs, maps, pools = counts.get(game, (0, 0, 0))
        general = await _tree_channel_link(guild_id, f"games/{game}", "general")
        civs_link = await _tree_channel_link(guild_id, f"games/{game}", "civs")
        maps_link = await _tree_channel_link(guild_id, f"games/{game}", "maps")
        pools_link = await _tree_channel_link(guild_id, f"games/{game}", "map-pools")
        lines = [f"## \u2694\ufe0f {game}"]
        stats = f"{civs} civs \u00b7 {maps} maps \u00b7 {pools} map pools"
        lines.append(stats)
        if general:
            lines.append(f"\u2022 G\u00e9n\u00e9ral : {general}")
        if civs_link:
            lines.append(f"\u2022 Civilisations : {civs_link}")
        if maps_link:
            lines.append(f"\u2022 Maps : {maps_link}")
        if pools_link:
            lines.append(f"\u2022 Map pools : {pools_link}")
        blocks.append(discord.ui.Separator())
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    await interaction.followup.send(view=view, ephemeral=True)


async def _view_game_for_guild(interaction: discord.Interaction, game_key: str) -> None:
    """One game's dashboard: players, civs, maps, pools + forum links."""
    from kingdoms.discord.guild_context import require_guild_context

    guild_id = await require_guild_context(interaction, f"guild-games:{game_key}")
    if guild_id is None:
        return
    await interaction.response.defer(ephemeral=True)
    from kingdoms.discord.admin_panel_games import _games_wiring

    service = _games_wiring()
    bot = interaction.client
    players = 0
    roster = getattr(bot, "_registration_database", None)
    if roster is not None:
        try:
            players = len(await roster.list_bindings_for_game(game_key))
        except Exception:
            players = 0
    civs = maps = pools = 0
    if service is not None:
        try:
            civs = sum(1 for c in await service.list_factions(game_key) if c.archived_at is None)
            maps = sum(1 for m in await service.list_maps(game_key) if m.archived_at is None)
            pools = sum(1 for p in await service.list_map_pools(game_key) if p.archived_at is None)
        except Exception:
            logger.warning("GAME VIEW catalog read failed", exc_info=True)

    civs_link = await _tree_channel_link(guild_id, f"games/{game_key}", "civs")
    maps_link = await _tree_channel_link(guild_id, f"games/{game_key}", "maps")
    pools_link = await _tree_channel_link(guild_id, f"games/{game_key}", "map-pools")

    def bullet(label: str, count: int, lnk: str) -> str:
        text = f"\u2022 {label} : **{count}**"
        return f"{text} \u2014 {lnk}" if lnk else text

    lines = [
        f"# \u2694\ufe0f {game_key}",
        "",
        f"\u2022 Joueurs avec un compte li\u00e9 : **{players}**",
        bullet("Civs", civs, civs_link),
        bullet("Maps", maps, maps_link),
        bullet("Map pools", pools, pools_link),
    ]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    await interaction.followup.send(view=view, ephemeral=True)


async def _view_mods(interaction: discord.Interaction) -> None:
    """Render the guild's granted mods with their channels."""
    from kingdoms.discord.guild_context import require_guild_context

    guild_id = await require_guild_context(interaction, "guild-mods:mods")
    if guild_id is None:
        return
    await _view_mods_for_guild(interaction, guild_id)


async def _view_mods_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Render the guild's mods (DM re-dispatch friendly: ack first, then read)."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(view=await _mods_layout(interaction.client, guild_id), ephemeral=True)


async def _mods_layout(bot: Any, guild_id: str) -> discord.ui.LayoutView:
    """Build the guild's mods view: granted mods with their channels."""
    from kingdoms.discord.wiring import build_guild_access_service, guild_access_platform

    _, all_mods = guild_access_platform()
    granted: tuple[str, ...] = ()
    service = build_guild_access_service()
    if service is not None:
        try:
            granted = await service.enabled_mods(guild_id)
        except Exception:
            logger.warning("MODS VIEW access read failed", exc_info=True)
    if service is None:
        granted = tuple(all_mods)
    lines: list[str] = []
    for mod in (granted or all_mods)[:25]:
        channels: list[str] = []
        general = await _tree_channel_link(guild_id, f"mods/{mod}", "general")
        if general:
            channels.append(general)
        admin = await _tree_channel_link(guild_id, f"mods/{mod}", "admin")
        if admin:
            channels.append(f"admin : {admin}")
        detail = f" \u2014 {', '.join(channels)}" if channels else " \u2014 salons d\u00e9di\u00e9s \u00e0 venir"
        lines.append(f"\u2022 **{mod}**{detail}")
    if not lines:
        lines.append("_Aucun mod actif pour cette guilde._")
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                "# \U0001f9e9 Mods\n\n"
                + "\n".join(lines)
                + "\n\n\U0001f4d6 Documentation : https://github.com/merlin-pinpin-org/kingdoms/tree/main/docs/MODS"
            ),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    return view


async def _collect_roster_bindings(bot: Any, guild_id: str) -> list[dict[str, Any]]:
    """Collect the bindings across every known game (deduplicated)."""
    roster = getattr(bot, "_registration_database", None)
    if roster is None:
        return []
    bindings: list[dict[str, Any]] = []
    try:
        from kingdoms.discord.admin_panel_games import _games_wiring
        from kingdoms.discord.wiring import granted_game_keys

        service = _games_wiring()
        catalog = tuple(await service.list_game_keys()) if service is not None else ()
        keys = await granted_game_keys(guild_id, catalog) if guild_id else catalog
        for key in keys[:5]:
            bindings.extend(await roster.list_bindings_for_game(key))
    except Exception:
        logger.warning("USERS VIEW catalog listing failed", exc_info=True)
    if not bindings:
        bindings = await roster.list_bindings_for_game("aoe2")
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for b in bindings:
        bid = str(b.get("_id", ""))
        if bid not in seen:
            seen.add(bid)
            unique.append(b)
    return unique




async def _is_guild_member(guild: Any, user_id: int) -> bool:
    """Whether one user is a member of the guild (cache first, then fetch)."""
    if guild.get_member(user_id) is not None:
        return True
    try:
        return await guild.fetch_member(user_id) is not None
    except Exception:
        return False


async def _view_users(interaction: discord.Interaction) -> None:
    """Render the guild's roster: members with linked profiles (name + game)."""
    registration = getattr(interaction.client, "registration_service", None)
    if registration is None:
        await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
        return
    from kingdoms.discord.guild_context import require_guild_context

    guild_id = await require_guild_context(interaction, "guild-users:users")
    if guild_id is None:
        return
    await _view_users_for_guild(interaction, guild_id)


async def _view_users_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Render the guild's roster (DM re-dispatch friendly: ack first)."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    bot = interaction.client
    roster = getattr(bot, "_registration_database", None)
    if roster is None:
        await interaction.followup.send("Roster indisponible (store non configuré).", ephemeral=True)
        return
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        await interaction.followup.send("Guilde introuvable.", ephemeral=True)
        return
    unique = await _collect_roster_bindings(bot, guild_id)
    lines: list[str] = []
    for binding in unique:
        user_id = str(binding.get("user_id", ""))
        if not user_id.isdigit() or not await _is_guild_member(guild, int(user_id)):
            continue
        profile = binding.get("profile") or {}
        name = (
            profile.get("display_name")
            or next(iter(profile.get("aliases") or []), None)
            or "?"
        )
        game = binding.get("game_key", "?")
        lines.append(f"\u2022 <@{user_id}> \u2014 **{name}** \u00b7 `{game}` (`{binding.get('profile_id', '?')}`)")
        if len(lines) >= 25:
            break
    if not lines:
        await interaction.followup.send("Aucun membre de cette guilde n'a de compte lié.", ephemeral=True)
        return
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"# \U0001f465 Joueurs enregistrés ({len(lines)})\n\n" + "\n".join(lines)
            ),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    await interaction.followup.send(view=view, ephemeral=True)


async def _view_admin(interaction: discord.Interaction) -> None:
    """Open the admin panel ephemerally, guarded at click time."""
    from kingdoms.discord.guild_context import require_guild_context

    guild_id = await require_guild_context(interaction, "guild-admin:admin")
    if guild_id is None:
        return
    await _view_admin_for_guild(interaction, guild_id)


async def open_home_view_for_guild(interaction: discord.Interaction, view_key: str, guild_id: str) -> None:
    """Re-dispatch a home view key with an explicit guild scope (DM pick)."""
    if view_key.startswith("guild-admin:"):
        await _view_admin_for_guild(interaction, guild_id)
    elif view_key.startswith("guild-games:"):
        await _view_game_for_guild(interaction, view_key[len("guild-games:") :])
    elif view_key.startswith("guild-mods:"):
        await _view_mods_for_guild(interaction, guild_id)
    elif view_key.startswith("guild-users:"):
        await _view_users_for_guild(interaction, guild_id)
    else:
        await interaction.response.send_message("Vue inconnue.", ephemeral=True)


async def _view_admin_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Open the new admin menu ephemerally, guarded at click time."""
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    admins = tuple(getattr(getattr(bot, "status_service", None), "bot_admins", ()) or ())
    if not await require_admin(interaction, admins, getattr(bot, "roles_service", None)):
        return
    from kingdoms.discord.admin_tree import build_admin_menu

    logs = getattr(bot, "logs_service", None)
    locale = "en"
    if logs is not None:
        try:
            locale = str(await logs.get_locale(guild_id) or "en")
        except Exception:
            locale = "en"
    await interaction.response.send_message(view=await build_admin_menu(guild_id, locale), ephemeral=True)


async def _view_mod(interaction: discord.Interaction, mod: str) -> None:
    """Route to a mod's home view builder."""
    bot = interaction.client
    builder = getattr(bot, "mod_home_builders", {}).get(mod)
    if builder is None:
        await interaction.response.send_message(await reply(interaction, "no_home_view"), ephemeral=True)
        return
    await builder(interaction)


def _home_static_pin_spec(bot: discord.Client) -> Any:
    """Declare (and register) the home menu as a static pinned view."""
    from kingdoms.discord.static_pins import StaticPinnedView, register_static_pin

    home = getattr(bot, "home_service", None)
    if home is None:
        return None
    registry = getattr(bot, "message_registry", None)

    async def _resolve(guild_id: str) -> str | None:
        """Resolve the tree's ``root/general`` (stored id only)."""
        from kingdoms.discord.ui.panels_provisioning import stored_channel_id

        return await stored_channel_id(guild_id, "root", "general")

    spec = StaticPinnedView(
        key=HOME_MESSAGE_KEY,
        mark_suffix=HOME_MESSAGE_KEY,
        resolve_channel=_resolve,
        build_layout=_async_layout(home),
        registry=registry,
    )
    register_static_pin(spec)
    return spec


async def ensure_pinned_home_menu(bot: discord.Client, guild_id: str) -> bool:
    """Ensure the guild's home channel holds its pinned menu (registry cycle).

    The static-pin cycle owns everything: channel resolution (recreated
    on delete), pin creation/refresh/re-pin, and the view-id mark.
    """
    from kingdoms.discord.static_pins import ensure_static_pin

    spec = _home_static_pin_spec(bot)
    if spec is None:
        return False
    return await ensure_static_pin(bot, spec, guild_id)


async def _registered_menu_lives(bot: discord.Client, guild_id: str, channel: Any) -> bool:
    """Whether the registered menu message exists **and is current**.

    A live message is edited in place to the current layout revision —
    a boot with a changed surface must update the pin, not keep the old
    version. The message id stays stable. Re-pins best-effort.
    """
    message_id = await _resolve_menu_message_id(bot, guild_id)
    if message_id is None:
        return False
    try:
        message = await channel.fetch_message(int(message_id))
    except Exception:
        return False
    try:
        home = getattr(bot, "home_service", None)
        if home is not None:
            await message.edit(view=await _async_layout(home)(guild_id))
    except Exception:
        logger.warning(
            "PINNED HOME MENU in-place update failed (message %s) — keeping the old pin",
            message_id,
            exc_info=True,
        )
    try:
        await message.pin(reason="kingdoms: pinned home menu (guild front door)")
    except Exception:
        logger.warning("PINNED HOME MENU re-pin failed (guild %s) - best-effort", guild_id)
    return True


async def _resolve_menu_message_id(bot: discord.Client, guild_id: str) -> str | None:
    """Resolve the registered menu id (registry first, memory fallback)."""
    registry = getattr(bot, "message_registry", None)
    if registry is not None:
        try:
            registered = await registry.resolve(HOME_MESSAGE_PLATFORM, HOME_MESSAGE_KEY, guild_id)
            if registered is None:
                registered = await registry.resolve(
                    HOME_MESSAGE_PLATFORM, HOME_MESSAGE_LEGACY_KEY, guild_id
                )
            if registered is not None:
                return str(registered.message_id)
        except Exception:
            logger.warning("PINNED HOME MENU registry resolve failed - best-effort")
    store = getattr(bot, "_home_menu_message_ids", None)
    if isinstance(store, dict):
        value = store.get(guild_id)
        return str(value) if value is not None else None
    return None


async def _register_menu_message(bot: discord.Client, guild_id: str, channel_id: str, message_id: str) -> None:
    """Persist the new menu id: memory fallback + registry (durable)."""
    store = getattr(bot, "_home_menu_message_ids", None)
    if not isinstance(store, dict):
        store = {}
        bot._home_menu_message_ids = store  # type: ignore[attr-defined]
    store[guild_id] = message_id
    registry = getattr(bot, "message_registry", None)
    if registry is None:
        return
    try:
        await registry.register(
            platform=HOME_MESSAGE_PLATFORM,
            message_key=HOME_MESSAGE_KEY,
            entity_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
        )
    except Exception:
        logger.warning("PINNED HOME MENU registry register failed - best-effort")


async def build_home_menu_view(bot: Any, guild_id: str) -> discord.ui.LayoutView:
    """Build the pinned home menu layout for a guild (localized labels)."""
    home = getattr(bot, "home_service", None)
    if home is None:
        return discord.ui.LayoutView(timeout=None)
    return await build_home_menu_for_guild(home, bot, guild_id)


async def build_home_menu_for_guild(home: HomeService, bot: Any, guild_id: str) -> discord.ui.LayoutView:
    """Build the home menu with the guild's localized button labels."""
    from kingdoms.core.services.home import HomeButton
    from kingdoms.discord.commands_i18n import tr_guild

    label_keys = {
        "status": ("ui.homes.btn_status", "Status"),
        "profile": ("ui.homes.btn_profile", "Profile"),
        "games": ("ui.homes.btn_games", "Games"),
        "mods": ("ui.homes.btn_mods", "Mods"),
        "users": ("ui.homes.btn_users", "Users"),
        "admin": ("ui.homes.btn_admin", "Admin"),
    }

    async def localized(view: str, fallback: str) -> str:
        if view.startswith("mod:"):
            return await tr_guild(guild_id, bot, "ui.homes.btn_mods", fallback)
        key, default = label_keys.get(view, ("", fallback))
        if not key:
            return fallback
        return await tr_guild(guild_id, bot, key, default)

    buttons = [
        HomeButton(view=b.view, label=await localized(b.view, b.label), emoji=b.emoji)
        for b in home.buttons()
    ]
    rows: list[discord.ui.ActionRow[discord.ui.LayoutView]] = []
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for i, button in enumerate(buttons):
        if i and i % 5 == 0:
            rows.append(row)
            row = discord.ui.ActionRow()
        row.add_item(
            discord.ui.Button(
                custom_id=f"home:open:{button.view}",
                label=button.label,
                emoji=discord.PartialEmoji.from_str(button.emoji) if button.emoji else None,
                style=discord.ButtonStyle.secondary,
            )
        )
    rows.append(row)
    from kingdoms.discord.pinned_marks import pinned_mark

    view = discord.ui.LayoutView(timeout=None)
    header = "## \U0001f3d0 Kingdoms\nBienvenue \u2014 chaque bouton ouvre une vue r\u00e9serv\u00e9e \u00e0 toi."
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(header),
            *rows,
            discord.ui.TextDisplay(pinned_mark(HOME_MESSAGE_KEY)),
        )
    )
    return view


def register_home_command(
    tree: app_commands.CommandTree[discord.Client],
    home: HomeService,
    catalog: MessageCatalog | None = None,
) -> None:
    """Register the /home slash command and the persistent home buttons."""

    @tree.command(
        name=localized("commands.home_name", "home"),
        description=localized("commands.home_description", "The guild's home menu"),
    )
    async def home_command(interaction: discord.Interaction) -> None:
        """Answer /home with the home menu (ephemeral, localized)."""
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        await interaction.response.send_message(
            view=await build_home_menu_for_guild(home, interaction.client, guild_id),
            ephemeral=True,
        )


def _async_layout(home: HomeService) -> Any:
    """Return the awaited-layout builder expected by PinnedMenuService."""

    async def build(guild_id: str) -> discord.ui.LayoutView:
        return await build_home_menu_for_guild(home, _home_bot_ref(), guild_id)

    return build


def _home_bot_ref() -> Any:
    """Resolve the running client held by the home module (boot wiring)."""
    return _HOME_BOT_REF[0] if _HOME_BOT_REF else None


def hold_home_bot(bot: Any) -> None:
    """Hold the running client so the pinned menu renders localized labels."""
    _HOME_BOT_REF.clear()
    _HOME_BOT_REF.append(bot)


_HOME_BOT_REF: list[Any] = []
