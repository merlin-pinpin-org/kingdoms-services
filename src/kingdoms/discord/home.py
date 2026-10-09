"""The home surface: /home + the pinned 🏛 menu, one ephemeral view per button.

The home is the guild's front door: a pinned menu in the 🏛-kingdoms-home
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
from typing import Any, cast

import discord
from discord import app_commands

from kingdoms.core.services.home import HomeService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.pinned_menu import PinnedMenuChannel, PinnedMenuService
from kingdoms.discord.commands_i18n import localized, reply

logger = logging.getLogger("kingdoms.home")

HOME_CHANNEL_NAME = "🏛-kingdoms-home"
HOME_CHANNEL_CATEGORY = "bot_home"
HOME_MARKER = "home:pin:"
HOME_MESSAGE_KEY = "home-menu"
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
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("## 🏛 Kingdoms\nBienvenue — chaque bouton ouvre une vue réservée à toi."),
            *rows,
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
    if view_key == "home":
        await open_home_menu(interaction)
    elif view_key == "status":
        await _view_status(interaction)
    elif view_key == "profile":
        await _view_profile(interaction)
    elif view_key == "games":
        await _view_games(interaction)
    elif view_key == "users":
        await _view_users(interaction)
    elif view_key == "admin":
        await _view_admin(interaction)
    elif view_key.startswith("guild-admin:"):
        await _view_admin_for_guild(interaction, view_key[len("guild-admin:") :])
    elif view_key.startswith("guild-games:"):
        await _view_games_for_guild(interaction, view_key[len("guild-games:") :])
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
    user_id = str(interaction.user.id)
    await interaction.response.defer(ephemeral=True)
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
            name = profile.get("display_name") or profile.get("name") or "?"
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
        row.add_item(ProfileRemoveAccountButton([str(b.get("profile_id", "")) for b in bindings]))
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(await _profile_header(interaction, user_id)), *blocks))
    view.add_item(row)
    await interaction.followup.send(view=view, ephemeral=True)


class ProfileAddAccountButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:add",
):
    """Open the add-account modal (profile id)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Add an account", style=discord.ButtonStyle.success, custom_id="home:profile:add"
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
        """Open the add-account modal."""
        await interaction.response.send_modal(ProfileAddAccountModal())


class ProfileAddAccountModal(discord.ui.Modal):
    """The add-account form: game key + profile id."""

    def __init__(self) -> None:
        super().__init__(title="Add an account", timeout=None)
        self.game: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Game (aoe2)", default="aoe2", max_length=16, required=True
        )
        self.profile_id: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Profile id", max_length=64, required=True
        )
        self.add_item(self.game)
        self.add_item(self.profile_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Bind the account, audit, confirm."""
        registration = getattr(interaction.client, "registration_service", None)
        if registration is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        try:
            await registration.bind_profile(
                str(interaction.user.id), str(self.game.value).strip(), str(self.profile_id.value).strip()
            )
        except Exception:
            logger.warning("PROFILE ADD failed", exc_info=True)
            await interaction.response.send_message(
                "Ajout echoue (profile inconnu du provider, ou deja lie).", ephemeral=True
            )
            return
        await interaction.response.send_message(await reply(interaction, "profile_linked"), ephemeral=True)


async def _profile_header(interaction: discord.Interaction, user_id: str) -> str:
    """Build the profile header: the user's chosen name when known."""
    identity = getattr(interaction.client, "identity_service", None)
    if identity is None:
        return "# 👤 Ton profil"
    try:
        user = await identity.get_user(f"discord:{user_id}")
    except Exception:
        logger.warning("HOME: identity lookup failed", exc_info=True)
        return "# 👤 Ton profil"
    if user is None or not user.display_name:
        return "# 👤 Ton profil"
    return f"# 👤 {user.display_name}"


class ProfileRenameButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:rename",
):
    """Open the rename modal (the user's chosen gamer name)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Change nickname", style=discord.ButtonStyle.secondary, custom_id="home:profile:rename"
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
        """Open the rename modal."""
        await interaction.response.send_modal(ProfileRenameModal())


class ProfileRenameModal(discord.ui.Modal):
    """The rename form: the user's chosen name (1-16 chars)."""

    def __init__(self) -> None:
        super().__init__(title="Your nickname", timeout=None)
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Nickname (16 chars max)", max_length=16, min_length=1, required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Persist the new name, confirm."""
        identity = getattr(interaction.client, "identity_service", None)
        if identity is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        user_id = f"discord:{interaction.user.id}"
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
            await interaction.response.send_message("Le pseudo doit faire entre 1 et 16 caractères.", ephemeral=True)
            return
        except Exception:
            logger.warning("PROFILE RENAME failed", exc_info=True)
            await interaction.response.send_message("Renommage impossible pour le moment.", ephemeral=True)
            return
        await interaction.response.send_message("Pseudo mis à jour ✅", ephemeral=True)


class ProfileRemoveAccountButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:remove",
):
    """Open the remove-account select (ephemeral state rides the wire)."""

    def __init__(self, profile_ids: list[str] | None = None) -> None:
        self.profile_ids = profile_ids or []
        super().__init__(
            discord.ui.Button(
                label="Remove an account", style=discord.ButtonStyle.danger, custom_id="home:profile:remove"
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
        """Rebuild the item; the profile list is rebuilt from the bindings."""
        registration = getattr(interaction.client, "registration_service", None)
        ids: list[str] = []
        if registration is not None:
            try:
                bindings = await registration.list_bindings(str(interaction.user.id))
                ids = [str(b.get("profile_id", "")) for b in bindings]
            except Exception:
                ids = []
        return cls(ids)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Confirm with a select of the user's accounts."""
        registration = getattr(interaction.client, "registration_service", None)
        if registration is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        bindings = await registration.list_bindings(str(interaction.user.id))
        options = [
            discord.SelectOption(
                label=str((b.get("profile") or {}).get("display_name") or b.get("profile_id", "?")),
                value=str(b.get("profile_id", "")),
            )
            for b in bindings[:25]
        ]
        if not options:
            await interaction.response.send_message(await reply(interaction, "no_account"), ephemeral=True)
            return
        await interaction.response.send_message(
            "Quel compte retirer ?",
            view=ProfileRemoveSelectView(options),
            ephemeral=True,
        )


class ProfileRemoveSelectView(discord.ui.View):
    """The remove-account select (ephemeral, short-lived)."""

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=None)
        self.add_item(ProfileRemoveSelect(options))

    async def on_error(self, interaction: discord.Interaction[discord.Client], error: Exception, item: Any) -> None:
        """Log and answer quietly (the surface is ephemeral)."""
        logger.warning("PROFILE REMOVE failed", exc_info=error)
        with contextlib.suppress(Exception):
            await interaction.response.send_message(await reply(interaction, "remove_failed"), ephemeral=True)


class ProfileRemoveSelect(discord.ui.Select[Any]):
    """The account picker of the remove flow."""

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(custom_id="home:profile:remove:select", options=options, placeholder="Account to remove...")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Unlink the chosen account, confirm."""
        registration = getattr(interaction.client, "registration_service", None)
        if registration is None:
            await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
            return
        data = interaction.data
        raw = getattr(data, "values", None) if data is not None else None
        if raw is None and isinstance(data, dict):
            raw = data.get("values")
        chosen = str(next(iter(raw))) if isinstance(raw, (list, tuple)) and raw else ""
        if not chosen:
            await interaction.response.defer()
            return
        try:
            await registration.unlink_profile(str(interaction.user.id), "aoe2", chosen)
        except Exception:
            logger.warning("PROFILE REMOVE failed", exc_info=True)
            await interaction.response.send_message(await reply(interaction, "remove_failed"), ephemeral=True)
            return
        await interaction.response.edit_message(content="Compte retire.", view=None)


async def _view_games(interaction: discord.Interaction) -> None:
    """Render the known games, their catalog sizes and provider status."""
    from kingdoms.discord.guild_context import require_guild_context

    guild_id = await require_guild_context(interaction, "guild-games:games")
    if guild_id is None:
        return
    await _view_games_for_guild(interaction, guild_id)


async def _view_games_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Render the guild's known games, catalog sizes and provider status."""
    from kingdoms.discord.bot.factory import KingdomsBot
    from kingdoms.discord.maps_forum import maps_forum_wiring_ready

    bot = interaction.client
    embed = discord.Embed(title="🎮 Jeux", color=0x5865F2)
    lines: list[str] = []
    if maps_forum_wiring_ready():
        try:
            from kingdoms.discord.admin_panel_games import _games_wiring

            service = _games_wiring()
            if service is not None:
                for key in (await service.list_game_keys())[:25]:
                    maps = await service.list_maps(key)
                    pools = await service.list_map_pools(key)
                    provider = await _provider_status(bot, key)
                    active = sum(1 for m in maps if m.archived_at is None)
                    lines.append(f"**{key}** — {active} maps actives, {len(pools)} pools — {provider}")
        except Exception:
            logger.warning("GAMES VIEW failed to read the catalog", exc_info=True)
    if not lines:
        games = bot.status_service.games() if isinstance(bot, KingdomsBot) else ()
        for game in games:
            provider = await _provider_status(bot, game)
            lines.append(f"**{game}** — {provider}")
    embed.description = "\n".join(lines) if lines else "Aucun jeu configure."
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _provider_status(bot: Any, game: str) -> str:
    """Probe one game's provider through its live adapter (best-effort)."""
    import asyncio

    adapter = getattr(bot, "_home_providers", {}).get(game)
    if adapter is None:
        return "❔ aucun provider configuré"
    try:
        maps = await asyncio.wait_for(adapter.list_maps(), timeout=5)
        del maps  # the probe checks reachability, not the lobby-scoped map list
        return "✅ provider en ligne"
    except TimeoutError:
        return "⚠️ timeout"
    except Exception:
        return "⚠️ indisponible"


async def _collect_roster_bindings(bot: Any) -> list[dict[str, Any]]:
    """Collect the bindings across every known game (deduplicated)."""
    roster = getattr(bot, "_registration_database", None)
    if roster is None:
        return []
    bindings: list[dict[str, Any]] = []
    try:
        from kingdoms.discord.admin_panel_games import _games_wiring

        service = _games_wiring()
        if service is not None:
            for key in (await service.list_game_keys())[:5]:
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


async def _view_users(interaction: discord.Interaction) -> None:
    """Render the guild's roster with linked profiles."""
    bot = interaction.client
    registration = getattr(bot, "registration_service", None)
    if registration is None:
        await interaction.response.send_message(await reply(interaction, "not_configured"), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    roster = getattr(bot, "_registration_database", None)
    if roster is None:
        await interaction.followup.send("Roster indisponible (store non configuré).", ephemeral=True)
        return
    unique = await _collect_roster_bindings(bot)
    if not unique:
        await interaction.followup.send("Aucun compte lié pour l'instant.", ephemeral=True)
        return
    lines = []
    for binding in sorted(unique, key=lambda b: str(b.get("user_id", "")))[:25]:
        user_id = binding.get("user_id", "")
        profile = binding.get("profile") or {}
        name = profile.get("display_name") or "?"
        lines.append(f"<@{user_id}> — {name} (`{binding.get('profile_id', '?')}`)")
    embed = discord.Embed(
        title=f"👥 Joueurs enregistrés ({len(unique)})",
        description="\n".join(lines),
        color=0x5865F2,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


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
        await _view_games_for_guild(interaction, guild_id)
    else:
        await interaction.response.send_message("Vue inconnue.", ephemeral=True)


async def _view_admin_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Open the admin panel for one guild (ephemeral, guarded at click time)."""
    from kingdoms.discord.bot.factory import KingdomsBot
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    if not isinstance(bot, KingdomsBot):
        return
    admins = tuple(bot.status_service.bot_admins)
    allowed = await require_admin(interaction, admins, bot.roles_service)
    if not allowed:
        return
    from kingdoms.discord.admin_panel_dynamic import build_pin_main_menu

    logs_service = getattr(bot, "logs_service", None)
    admin_channel_service = getattr(bot, "admin_channel_service", None)
    if logs_service is None:
        await interaction.response.send_message(
            "Le panneau admin n'est pas configuré sur ce process.",
            ephemeral=True,
        )
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    try:
        locale = await logs_service.get_locale(guild_id)
    except Exception:
        locale = "en"
    view = await build_pin_main_menu(
        logs_service,
        guild_id,
        getattr(bot, "messages", None),
        locale,
        admin_channel_service,
    )
    await interaction.response.send_message(view=view, ephemeral=True)


async def _view_mod(interaction: discord.Interaction, mod: str) -> None:
    """Route to a mod's home view builder."""
    bot = interaction.client
    builder = getattr(bot, "mod_home_builders", {}).get(mod)
    if builder is None:
        await interaction.response.send_message(await reply(interaction, "no_home_view"), ephemeral=True)
        return
    await builder(interaction)


async def ensure_pinned_home_menu(bot: discord.Client, guild_id: str) -> bool:
    """Ensure the guild's home channel holds its pinned menu, never rebuilt while it lives.

    The message id is resolved through the message registry (Mongo-backed,
    restart-proof) or the bot's in-memory fallback store: as long as the
    registered message exists it is merely re-pinned; only a gone message
    (deleted or channel wiped) triggers a rebuild, whose id replaces the
    registration.
    """
    home_channel = getattr(bot, "home_channel_service", None)
    home = getattr(bot, "home_service", None)
    if home_channel is None or home is None:
        return False
    channel_id = await home_channel.resolve_channel(guild_id)
    if channel_id is None:
        return False
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() and guild else None
    if channel is None or not hasattr(channel, "fetch_message") or not hasattr(channel, "send"):
        return False

    if await _registered_menu_lives(bot, guild_id, channel):
        return False

    class _ChannelDelivery:
        last_message_id: str | None = None

        async def deliver(self, channel: Any, layout: Any) -> str:
            """Send the layout and remember the delivered message id."""
            message = await channel.send(view=layout)
            self.last_message_id = str(message.id)
            return self.last_message_id

        async def update(self, channel: Any, message_id: str, layout: Any) -> bool:
            """Edit an existing menu message to the new layout in place."""
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(view=layout)
                return True
            except Exception:
                logger.warning(
                    "PINNED HOME MENU in-place update failed (message %s) — will re-post",
                    message_id,
                    exc_info=True,
                )
                return False

    delivery = _ChannelDelivery()
    service = PinnedMenuService(delivery, delivery)
    created = await service.ensure(
        guild_id,
        cast("PinnedMenuChannel", channel),
        marker=HOME_MARKER,
        build_layout=_async_layout(home),
        pin_reason="kingdoms: pinned home menu (guild front door)",
    )
    if not created:
        return False
    if delivery.last_message_id is not None:
        await _register_menu_message(bot, guild_id, str(channel_id), delivery.last_message_id)
    return True


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


def build_home_menu_view(bot: Any, guild_id: str) -> discord.ui.LayoutView:
    """Build the pinned home menu layout for a guild (refresher entry)."""
    del guild_id
    home = getattr(bot, "home_service", None)
    if home is None:
        return discord.ui.LayoutView(timeout=None)

    return build_home_menu(home)


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
        """Answer /home with the home menu (ephemeral)."""
        await interaction.response.send_message(view=build_home_menu(home), ephemeral=True)


def _async_layout(home: HomeService) -> Any:
    """Return the awaited-layout builder expected by PinnedMenuService."""

    async def build(guild_id: str) -> discord.ui.LayoutView:
        del guild_id
        return build_home_menu(home)

    return build
