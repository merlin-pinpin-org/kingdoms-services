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
"""
from __future__ import annotations

import logging
import re
from typing import Any, cast

import discord
from discord import app_commands

from kingdoms.core.services.home import HomeService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.pinned_menu import PinnedMenuChannel, PinnedMenuService
from kingdoms.discord.commands_i18n import localized

logger = logging.getLogger("kingdoms.home")

HOME_CHANNEL_NAME = "🏛-kingdoms-home"
HOME_CHANNEL_CATEGORY = "bot_home"
HOME_MARKER = "home:pin:"
HOME_MENU_MARKER_ID = "home:pin:menu"


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
        row.add_item(discord.ui.Button(
            custom_id=f"home:open:{button.view}",
            label=button.label,
            emoji=discord.PartialEmoji.from_str(button.emoji) if button.emoji else None,
            style=discord.ButtonStyle.secondary,
        ))
    rows.append(row)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            "## 🏛 Kingdoms\nBienvenue — chaque bouton ouvre une vue réservée à toi."
        ),
        *rows,
        discord.ui.TextDisplay(f"-#{HOME_MENU_MARKER_ID}"),
    ))
    return view


async def open_home_view(interaction: discord.Interaction, view_key: str) -> None:
    """Answer a home click with the matching ephemeral view."""
    if view_key == "status":
        await _view_status(interaction)
    elif view_key == "profile":
        await _view_profile(interaction)
    elif view_key == "games":
        await _view_games(interaction)
    elif view_key == "users":
        await _view_users(interaction)
    elif view_key == "admin":
        await _view_admin(interaction)
    elif view_key.startswith("mod:"):
        await _view_mod(interaction, view_key[4:])
    else:
        await interaction.response.send_message("Unknown view.", ephemeral=True)


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
    """Render the user's games, linked accounts and their info."""
    registration = getattr(interaction.client, "registration_service", None)
    if registration is None:
        await interaction.response.send_message("Registration is not configured.", ephemeral=True)
        return
    user_id = str(interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    bindings = await registration.list_bindings(user_id)
    if not bindings:
        await interaction.followup.send(
            "Aucun compte de jeu lié — utilise /register pour lier ton profil AoE2.",
            ephemeral=True,
        )
        return
    embed = discord.Embed(title="👤 Ton profil", color=0x5865F2)
    by_game: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        by_game.setdefault(str(binding.get("game_key", "?")), []).append(binding)
    for game, entries in sorted(by_game.items()):
        lines = []
        for entry in entries:
            profile = entry.get("profile") or {}
            name = profile.get("display_name") or profile.get("name") or "?"
            pid = entry.get("profile_id", "?")
            lines.append(f"• {name} (`{pid}`)")
        embed.add_field(name=game, value="\n".join(lines), inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


async def _view_games(interaction: discord.Interaction) -> None:
    """Render the configured games and their provider status."""
    from kingdoms.discord.bot.factory import KingdomsBot

    bot = interaction.client
    games = bot.status_service.games() if isinstance(bot, KingdomsBot) else ()
    embed = discord.Embed(title="🎮 Jeux", color=0x5865F2)
    if not games:
        embed.description = "Aucun jeu configuré."
    else:
        lines = []
        for game in games:
            provider = await _provider_status(bot, game)
            lines.append(f"**{game}** — {provider}")
        embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _provider_status(bot: Any, game: str) -> str:
    """Probe one game's provider through its live adapter (best-effort)."""
    import asyncio

    adapter = getattr(bot, "_home_providers", {}).get(game)
    if adapter is None:
        return "❔ aucun provider configuré"
    try:
        maps = await asyncio.wait_for(adapter.list_maps(), timeout=5)
        return f"✅ en ligne ({len(maps)} maps)" if maps else "⚠️ réponse vide"
    except TimeoutError:
        return "⚠️ timeout"
    except Exception:
        return "⚠️ indisponible"


async def _view_users(interaction: discord.Interaction) -> None:
    """Render the guild's roster with linked profiles."""
    bot = interaction.client
    registration = getattr(bot, "registration_service", None)
    if registration is None:
        await interaction.response.send_message("Registration is not configured.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    roster = getattr(bot, "_registration_database", None)
    if roster is None:
        await interaction.followup.send("Roster indisponible (store non configuré).", ephemeral=True)
        return
    bindings = await roster.list_bindings_for_game("aoe2")
    if not bindings:
        await interaction.followup.send("Aucun compte lié pour l'instant.", ephemeral=True)
        return
    lines = []
    for binding in sorted(bindings, key=lambda b: str(b.get("user_id", "")))[:25]:
        user_id = binding.get("user_id", "")
        profile = binding.get("profile") or {}
        name = profile.get("display_name") or "?"
        lines.append(f"<@{user_id}> — {name} (`{binding.get('profile_id', '?')}`)")
    embed = discord.Embed(
        title=f"👥 Joueurs enregistrés ({len(bindings)})",
        description="\n".join(lines),
        color=0x5865F2,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


async def _view_admin(interaction: discord.Interaction) -> None:
    """Point to the admin panel, guarded at click time."""
    from kingdoms.discord.bot.factory import KingdomsBot
    from kingdoms.discord.guards import require_admin

    bot = interaction.client
    if not isinstance(bot, KingdomsBot):
        return
    admins = tuple(bot.status_service.bot_admins)
    allowed = await require_admin(interaction, admins, bot.roles_service)
    if not allowed:
        return
    await interaction.response.send_message(
        "Le panneau admin vit dans le channel 🛡-bot-admins — utilise /admin ici.",
        ephemeral=True,
    )


async def _view_mod(interaction: discord.Interaction, mod: str) -> None:
    """Route to a mod's home view builder."""
    bot = interaction.client
    builder = getattr(bot, "mod_home_builders", {}).get(mod)
    if builder is None:
        await interaction.response.send_message("Ce mod n'a pas de vue home.", ephemeral=True)
        return
    await builder(interaction)


async def ensure_pinned_home_menu(bot: discord.Client, guild_id: str) -> bool:
    """Ensure the guild's home channel holds exactly one current pinned menu."""
    home_channel = getattr(bot, "home_channel_service", None)
    home = getattr(bot, "home_service", None)
    if home_channel is None or home is None:
        return False
    channel_id = await home_channel.resolve_channel(guild_id)
    if channel_id is None:
        return False
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() and guild else None
    if not isinstance(channel, discord.TextChannel):
        return False

    class _ChannelDelivery:
        async def deliver(self, channel: Any, layout: Any) -> str:
            message = await channel.send(view=layout)
            return str(message.id)

    service = PinnedMenuService(_ChannelDelivery())
    return await service.ensure(
        guild_id,
        cast("PinnedMenuChannel", channel),
        marker=HOME_MARKER,
        build_layout=lambda guild: _build_layout(home),
        pin_reason="kingdoms: pinned home menu (guild front door)",
    )


async def _build_layout(home: HomeService) -> discord.ui.LayoutView:
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
