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
import os
import re
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.home import HomeService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.commands_i18n import localized, reply, tr
from kingdoms.discord.ephemeral import EphemeralOrigin

logger = logging.getLogger("kingdoms.home")

HOME_CHANNEL_NAME = "🏛-home"
HOME_CHANNEL_CATEGORY = "bot_home"
HOME_MARKER = "home:pin:"
HOME_MESSAGE_KEY = "view:home"
HOME_MESSAGE_LEGACY_KEY = "home-menu"
HOME_MESSAGE_PLATFORM = "discord"


# The legacy home:open ids mapped onto the declared view ids — the
# pins deployed before the views registry keep working through it.
_LEGACY_VIEW_KEYS = {
    "profile": "panel:profile",
    "status": "panel:status",
    "games": "menu:games:home",
    "mods": "menu:mods:home",
    "admin": "menu:admin",
    "live": "panel:game:aoe2:live",
    "users": "panel:game:aoe2:players",
}


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
        """Open the declared view at click time (legacy ids mapped).

        The declared menus carry ``view:open:<view id>`` buttons; the
        legacy ``home:open:<key>`` ids ride the pins deployed before
        the views registry. They map onto the same declared views so
        an un-refreshed pin still opens the right thing.
        """
        from kingdoms.discord.views_routing import open_declared_view

        await open_declared_view(interaction, _LEGACY_VIEW_KEYS.get(self.view_key, self.view_key))


async def _view_status(interaction: discord.Interaction) -> None:
    """Render the /status layout, ephemeral."""
    await interaction.response.defer(ephemeral=True)
    from pathlib import Path

    from kingdoms.discord.announce import AnnounceConfig, build_announcement_layout
    from kingdoms.discord.bot.factory import KingdomsBot

    bot = interaction.client
    if not isinstance(bot, KingdomsBot):
        await interaction.followup.send(await reply(interaction, "profile.status_unavailable"), ephemeral=True)
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
    await interaction.response.defer(ephemeral=True, thinking=False)
    await _profile_followup(interaction)


async def _resolve_profile_names(
    interaction: discord.Interaction, bindings: list[dict[str, Any]]
) -> dict[str, str]:
    """Resolve each binding's display name: stored first, provider next.

    The binding's stored profile payload (profile_name, aliases) wins —
    a missing one (the pre-merge seed wiped some) resolves through the
    librematch provider's stats (its alias entries), cached for the
    render; the profile id is the last resort, never a bare '?'.
    """
    names: dict[str, str] = {}
    missing: list[str] = []
    for binding in bindings:
        profile = binding.get("profile") or {}
        pid = str(binding.get("profile_id", ""))
        stored = str(profile.get("profile_name") or "")
        aliases = [str(a) for a in (profile.get("aliases") or []) if str(a)]
        if stored:
            names[pid] = stored
        elif aliases:
            names[pid] = aliases[0]
        elif pid:
            missing.append(pid)
    if missing:
        # resolve the missing names through the profile seam's provider
        game = str(bindings[0].get("game_key", "aoe2")) if bindings else "aoe2"
        await _resolve_missing_names_from_provider(game, missing, names)
    return names


async def _resolve_missing_names_from_provider(
    game: str, missing: list[str], names: dict[str, str]
) -> None:
    """Fill the missing profile names through the librematch provider."""
    try:
        from kingdoms.core.rpc.game_client import GameProviderClient

        provider_uri = os.environ.get("EXT_LIBREMATCH_URI", "")
        if not provider_uri:
            return
        client = GameProviderClient(provider_uri, "ext-librematch", game)
        for pid in missing:
            stats = await client.get_player_stats(pid)
            if stats is None:
                continue
            entries = [e for b in stats.blocks for e in b.entries if e.key == "alias"]
            if not entries:
                entries = [e for b in stats.blocks for e in b.entries][:1]
            if entries:
                names[pid] = str(entries[0].value)
    except Exception:
        logger.warning("HOME: provider name resolve failed — best-effort", exc_info=True)


async def _profile_game_blocks(
    interaction: discord.Interaction, bindings: list[dict[str, Any]], user_id: str
) -> list[discord.ui.TextDisplay[discord.ui.LayoutView]]:
    """Render the linked accounts grouped by game (one block per game)."""
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
    blocks: list[discord.ui.TextDisplay[discord.ui.LayoutView]] = []
    resolved = await _resolve_profile_names(interaction, bindings)
    for game, entries in sorted(by_game.items()):
        lines = [f"## {_game_label(game)}"]
        for entry in entries:
            pid = str(entry.get("profile_id", "?"))
            name = resolved.get(pid) or pid
            player = players_by_id.get(user_id)
            elo = f" — {player.get('rating')} elo" if player else ""
            url = _game_account_url(game, str(pid))
            account = f"[{name}]({url}) (`{pid}`)" if url else f"{name} (`{pid}`)"
            lines.append(f"• {account}{elo}")
        blocks.append(discord.ui.TextDisplay(chr(10).join(lines)))
    return blocks


async def _profile_followup(
    interaction: discord.Interaction, banner: str = "", origin: Any = None
) -> None:
    """Render the profile view, updating the original ephemeral message.

    The ephemeral contract: the profile view NEVER sends a copy of
    itself — the caller hands the original message (captured at the
    button click) and it is edited in place, banner and all; only the
    very first render sends a message.
    """
    registration = getattr(interaction.client, "registration_service", None)
    if registration is None:
        await interaction.followup.send(await reply(interaction, "not_configured"), ephemeral=True)
        return
    user_id = str(interaction.user.id)
    bindings = await registration.list_bindings(user_id)
    blocks: list[Any] = []
    if not bindings:
        blocks.append(discord.ui.TextDisplay(await reply(interaction, "profile.no_account")))
    blocks.extend(await _profile_game_blocks(interaction, bindings, user_id))
    view = discord.ui.LayoutView(timeout=None)
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(
        ProfileAddAccountButton(await tr(interaction, "profile.add_button", "Ajouter un compte"))
    )
    row.add_item(
        ProfileRenameButton(await tr(interaction, "profile.rename_button", "Changer de pseudo"))
    )
    if bindings:
        row.add_item(
            ProfileRemoveAccountButton(await tr(interaction, "profile.remove_button", "Retirer un compte"))
        )
    header = await _profile_header(interaction, user_id)
    if banner:
        header = f"{header}\n\n{banner}"
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(header), *blocks))
    view.add_item(row)
    from kingdoms.core.ids import footer

    view.add_item(discord.ui.TextDisplay(footer("panel:profile")))
    # The modal submit edits the message the modal CAME FROM directly —
    # interaction.response.edit_message is documented for modal
    # interactions (no defer, no thinking indicator, no followup
    # residue: the clicked view updates in place, ONE surface).
    if origin is not None:
        logger.info(
            "PROFILE FOLLOWUP: origin captured (message_id=%s) — editing in place",
            getattr(origin, "message_id", "?"),
        )
        try:
            await interaction.response.edit_message(view=view)
            return
        except Exception:
            logger.info(
                "PROFILE FOLLOWUP: edit_message failed — plain-text fallback",
                exc_info=True,
            )
            note = banner or await reply(interaction, "profile.updated")
            await interaction.followup.send(note, ephemeral=True)
            return
    logger.info("PROFILE FOLLOWUP: no origin — sending a fresh ephemeral view")
    await interaction.followup.send(view=view, ephemeral=True)


async def _delete_deferred_followup(interaction: discord.Interaction) -> None:
    """Delete the deferred thinking message a modal submit leaves behind.

    A defer(thinking=False) creates an ephemeral followup message; when
    the flow ends by editing the ORIGIN in place, that residue must go
    — the user sees exactly one view, not a view + a stale thinking.
    """
    try:
        # the deferred response IS the latest followup message —
        # fetch the interaction's own ephemeral response and delete it
        original = await interaction.original_response()
        if original is not None and original.flags.ephemeral:
            await interaction.delete_original_response()
    except Exception:
        logger.debug("deferred followup cleanup skipped — best-effort", exc_info=True)




PROFILE_PROVIDER = "ext-librematch (aoe2)"

GAME_LABELS = {"aoe2": "Age of Empires II"}


def _live_client_for(bot: Any, game_key: str) -> Any:
    """Resolve the dashboard client of one game (None when unwired).

    The URI convention is ``GAME_<KEY>_URI``; aoe2 is served by the
    game service's GameData client. The live view is ephemeral-only
    (on demand): one client per render, nothing kept warm.
    """
    import os

    uri = os.environ.get(f"GAME_{game_key.upper()}_URI", "")
    if not uri:
        return None
    if game_key == "aoe2":
        from kingdoms.game_aoe2.client import GameDataClient

        return GameDataClient(uri)
    return None


def _game_label(game_key: str) -> str:
    """Render one game key as its display name."""
    return GAME_LABELS.get(game_key, game_key)


def _profile_sites(game_key: str) -> list[tuple[str, str, str]]:
    """List the game's declared profile sites: (key, label, no-url)."""
    from kingdoms.core.services.profile_sites import profile_sites_registry

    return [(s.key, s.label, "") for s in profile_sites_registry().sites_for(game_key)]


def _game_account_url(game_key: str, relic_id: str, site_key: str = "") -> str:
    """Build the profile's public page URL on the chosen (or default) site.

    The sites are declared in ``config/games/<game>/profile_sites.yaml``
    — never hard-coded; the user's preferred site rides their settings.
    """
    from kingdoms.core.services.profile_sites import format_profile_url

    return format_profile_url(game_key, relic_id, site_key)


async def _user_preferred_site(interaction: discord.Interaction, game_key: str) -> str:
    """Read the user's preferred profile site for a game ('' = default)."""
    logs_service = getattr(interaction.client, "logs_service", None)
    if logs_service is None:
        return ""
    try:
        settings = await logs_service._safe(
            logs_service._db.get_user_settings(str(interaction.user.id))
        ) or {}
    except Exception:
        return ""
    return str(settings.get(f"profile_site:{game_key}", ""))


async def _set_user_preferred_site(
    interaction: discord.Interaction, game_key: str, site_key: str
) -> None:
    """Persist the user's preferred profile site for a game."""
    logs_service = getattr(interaction.client, "logs_service", None)
    if logs_service is None:
        return
    import time as _time

    settings = await logs_service._safe(
        logs_service._db.get_user_settings(str(interaction.user.id))
    ) or {}
    settings[f"profile_site:{game_key}"] = site_key
    settings["updated_at"] = int(_time.time())
    await logs_service._db.set_user_settings(str(interaction.user.id), settings)


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


async def _dm_profile_notice(interaction: discord.Interaction, key: str, **kwargs: Any) -> None:
    """DM the user the account-change notice in their own stored language.

    The ephemeral view answers the click (the guild's context); the DM
    is the user's personal record and speaks THEIR language \u2014 the
    stored DM choice, then the observed client locale, then English.
    Best-effort: a closed DM is skipped quietly.
    """
    client = interaction.client
    catalog = getattr(client, "messages", None)
    if catalog is None:
        return
    logs = getattr(client, "logs_service", None)
    locale = "en"
    user_id = str(interaction.user.id)
    if logs is not None:
        try:
            locale = str(await logs.get_user_locale(user_id) or "en")
        except Exception:
            locale = "en"
    rendered = str(catalog.render(f"profile_dm.{key}", locale, **kwargs))
    if rendered == f"profile_dm.{key}":
        rendered = str(catalog.render(f"profile_dm.{key}", "en", **kwargs))
    if rendered == f"profile_dm.{key}":
        return
    try:
        await interaction.user.send(rendered)
    except Exception:
        logger.info("PROFILE DM skipped (user %s \u2014 DMs closed?)", user_id)


class ProfileAddAccountButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:add",
):
    """Open the add-account modal (profile id)."""

    def __init__(self, label: str = "Ajouter un compte") -> None:
        super().__init__(
            discord.ui.Button(
                label=label, emoji="\u2795", style=discord.ButtonStyle.success, custom_id="home:profile:add"
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
        await interaction.response.send_modal(
            ProfileAddAccountModal(options, origin=EphemeralOrigin.capture(interaction))
        )


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

    def __init__(
        self, options: list[discord.SelectOption] | None = None, origin: Any = None
    ) -> None:
        super().__init__(title="Ajouter un compte", timeout=None)
        self.origin = origin
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
        # no defer: the submit's edit_message is the response
        try:
            await registration.bind_profile(
                str(interaction.user.id), str(chosen), str(self.profile_id.value).strip()
            )
        except Exception as exc:
            await _report_profile_error(interaction, "profile.add", chosen, exc)
            reason = str(exc).lower()
            if "déjà" in reason or "already" in reason:
                note = await reply(interaction, "profile.add_taken")
            elif "no profile seam" in reason or "unknown game" in reason:
                note = await reply(interaction, "profile.add_game_unconfigured", game=chosen)
            elif "player_stats" in reason or "mal configur" in reason:
                note = await reply(interaction, "profile.add_provider_error", provider=PROFILE_PROVIDER)
            elif "valid" in reason or "inconnu" in reason:
                note = (
                    f"Add failed: no profile found for "
                    f"`{str(self.profile_id.value).strip()}`. Paste your aoe2insights "
                    "page URL (or the id it contains) \u2014 the right id resolves "
                    "automatically."
                )
            else:
                note = (
                    f"Add failed: the provider {PROFILE_PROVIDER} did not answer "
                    "\u2014 the exact error went to the admins."
                )
            await interaction.followup.send(note, ephemeral=True)
            return
        bound_url = _game_account_url(str(chosen), str(self.profile_id.value).strip())
        await _dm_profile_notice(
            interaction,
            "account_added",
            account=f"[{chosen} {self.profile_id.value}]({bound_url})" if bound_url else str(chosen),
        )
        await _profile_followup(
            interaction,
            banner=await reply(interaction, "profile.add_success"),
            origin=self.origin,
        )


async def _profile_header(interaction: discord.Interaction, user_id: str) -> str:
    """Build the profile header: the user's chosen name when known."""
    identity = getattr(interaction.client, "identity_service", None)
    if identity is None:
        return "# 👤 Your profile\nName: *(not set)*"
    try:
        user = await identity.get_user(f"discord:{user_id}")
    except Exception:
        logger.warning("HOME: identity lookup failed", exc_info=True)
        return "# 👤 Your profile\nName: *(not set)*"
    if user is None or not user.display_name:
        return "# 👤 Your profile\nName: *(not set)*"
    return f"# 👤 Your profile\nName: **{user.display_name}**"


class ProfileRenameButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:profile:rename",
):
    """Open the rename modal (the user's chosen gamer name)."""

    def __init__(self, label: str = "Changer de pseudo") -> None:
        super().__init__(
            discord.ui.Button(
                label=label, emoji="\u270f", style=discord.ButtonStyle.secondary, custom_id="home:profile:rename"
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
        await interaction.response.send_modal(
            ProfileRenameModal(current, origin=EphemeralOrigin.capture(interaction))
        )


class ProfileSiteSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"home:profile:site:(?P<game>[a-z0-9]+)",
):
    """Pick the preferred profile site for a game (persisted per user).

    The sites come from ``config/games/<game>/profile_sites.yaml`` —
    the select is rebuilt from the registry at every profile render,
    so a config change shows up without a deploy.
    """

    def __init__(self, game_key: str, options: list[discord.SelectOption]) -> None:
        super().__init__(
            discord.ui.Select(
                placeholder="Preferred profile site",
                options=options,
                custom_id=f"home:profile:site:{game_key}",
            )
        )
        self.game_key = game_key

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ProfileSiteSelect:
        """Rebuild the picker from the wire (registry options)."""
        game_key = str(match.group("game"))
        return cls(game_key, _site_options(interaction, game_key))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Persist the choice and re-render the profile in place."""
        data = getattr(interaction, "data", None) or {}
        values = [str(v) for v in data.get("values", [])]
        if not values:
            return
        await _set_user_preferred_site(interaction, self.game_key, values[0])
        await _profile_followup(
            interaction,
            banner=f"Preferred profile site: **{values[0]}**.",
        )


def _site_options(interaction: discord.Interaction, game_key: str) -> list[discord.SelectOption]:
    """Build the site options from the registry (current choice marked)."""
    from kingdoms.core.services.profile_sites import profile_sites_registry

    registry = profile_sites_registry()
    current = ""
    default = registry.default_site(game_key)
    options = [
        discord.SelectOption(
            label=site.label,
            value=site.key,
            default=(site.key == current) or (not current and default is not None and site.key == default.key),
        )
        for site in registry.sites_for(game_key)
    ]
    return options


class ProfileRenameModal(discord.ui.Modal):
    """The rename form: the user's chosen name (1-16 chars)."""

    def __init__(self, current: str = "", origin: Any = None) -> None:
        super().__init__(title="Ton pseudo", timeout=None)
        self.origin = origin
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="Name (16 characters max)",
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
        # no defer: the submit's edit_message is the response
        try:
            user = await identity.get_user(user_id)
            if user is None:
                user = await identity.get_or_create_user(
                    platform="discord",
                    platform_user_id=str(interaction.user.id),
                    display_name=str(interaction.user.display_name or interaction.user.name),
                )
            await identity.set_display_name(user.id, str(self.name.value))
        except ValueError:
            await interaction.followup.send(await reply(interaction, "profile.rename_bad_length"), ephemeral=True)
            return
        except Exception:
            logger.warning("PROFILE RENAME failed", exc_info=True)
            await interaction.followup.send(await reply(interaction, "profile.rename_failed"), ephemeral=True)
            return
        await _dm_profile_notice(interaction, "pseudo_updated", name=self.name.value)
        await _profile_followup(
            interaction,
            banner=await reply(interaction, "profile.rename_success", name=self.name.value),
            origin=self.origin,
        )


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

    def __init__(self, label: str = "Retirer un compte") -> None:
        super().__init__(
            discord.ui.Button(
                label=label, emoji="\U0001F5D1", style=discord.ButtonStyle.danger, custom_id="home:profile:remove"
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
        await interaction.response.send_modal(
            ProfileRemoveAccountModal(
                _remove_options(bindings), origin=EphemeralOrigin.capture(interaction)
            )
        )


class ProfileRemoveAccountModal(discord.ui.Modal):
    """The remove-account form: one multi-select of the linked accounts."""

    def __init__(self, options: list[discord.SelectOption], origin: Any = None) -> None:
        super().__init__(title="Retirer des comptes", timeout=None)
        self.origin = origin
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
        # no defer: the submit's edit_message is the response
        removed: list[str] = []
        failures: list[str] = []
        for entry in chosen:
            game_key, _, profile_id = str(entry).partition("|")
            try:
                if await registration.unlink_profile(str(interaction.user.id), game_key, profile_id):
                    url = _game_account_url(game_key, str(profile_id))
                    removed.append(f"[{game_key} {profile_id}]({url})" if url else f"{game_key} (`{profile_id}`)")
                else:
                    failures.append(f"{game_key} `{profile_id}`")
                    await _report_profile_error(
                        interaction, "profile.remove", game_key, RuntimeError(f"binding not found: {entry}")
                    )
            except Exception as exc:
                failures.append(f"{game_key} `{profile_id}` ({type(exc).__name__})")
                await _report_profile_error(interaction, "profile.remove", game_key, exc)
        # ONE surface (owner direction, the view SDK): the result rides
        # the refreshed profile view's banner — no standalone followup
        # messages, the deferred thinking residue is cleaned by the
        # followup's origin edit
        banner_bits: list[str] = []
        if removed:
            listed = ", ".join(removed)
            banner_bits.append(await reply(interaction, "profile.accounts_removed", listed=listed))
        if failures:
            banner_bits.append(
                "Removal failed for: " + ", ".join(failures) + " — the details went to the admins."
            )
        if removed:
            await _dm_profile_notice(interaction, "accounts_removed", listed=", ".join(removed))
        banner = "\n".join(banner_bits)
        if not removed and not failures:
            banner = await reply(interaction, "profile.no_account")
        await _profile_followup(interaction, banner=banner, origin=self.origin)


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
    await interaction.response.defer(ephemeral=True, thinking=False)
    games = bot.status_service.games() if isinstance(bot, KingdomsBot) else ()
    games, counts = await _granted_games_with_counts(guild_id, games)

    header = await reply(interaction, "profile.games_title")
    blocks: list[Any] = [
        discord.ui.TextDisplay(header + "\n" + await reply(interaction, "profile.games_subtitle")),
        discord.ui.Separator(),
    ]
    if not games:
        blocks.append(discord.ui.TextDisplay(await reply(interaction, "profile.games_none", admin_role="bot-admins")))
    if games:
        game_names = "\n".join(f"\u2022 **{_game_label(game)}**" for game in games[:25])
        blocks.append(
            discord.ui.TextDisplay(await reply(interaction, "profile.games_available") + "\n" + game_names)
        )
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
            lines.append(f"\u2022 {await reply(interaction, 'profile.general_label')} : {general}")
        if civs_link:
            lines.append(f"\u2022 {await reply(interaction, 'profile.civs_label')} : {civs_link}")
        if maps_link:
            lines.append(f"\u2022 {await reply(interaction, 'profile.maps_label')} : {maps_link}")
        if pools_link:
            lines.append(f"\u2022 {await reply(interaction, 'profile.pools_label')} : {pools_link}")
        blocks.append(discord.ui.Separator())
        blocks.append(discord.ui.TextDisplay("\n".join(lines)))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    await interaction.followup.send(view=view, ephemeral=True)


async def _view_live(interaction: discord.Interaction, game_key: str = "aoe2") -> None:
    """Render one game's live dashboard ephemerally (/live, the menu panel)."""
    await interaction.response.defer(ephemeral=True, thinking=False)
    view = await _build_live_view(interaction, game_key)
    await interaction.followup.send(view=view, ephemeral=True)


async def _build_live_view(
    interaction: discord.Interaction, game_key: str = "aoe2"
) -> Any:
    """Build the ephemeral live dashboard view (no defer, no send).

    Shared by /live, the menu button and the dashboard's refresh
    button (which already deferred): the caller owns the transport.
    """
    from kingdoms.discord.live import collect_profile_stats, render_dashboard

    snapshot: dict[str, Any] = {"players": [], "generated_at": 0, "degraded": True}
    client = _live_client_for(interaction.client, game_key)
    if client is not None:
        try:
            snapshot = await client.watch(game_key)
        except Exception:
            logger.warning("HOME: live snapshot failed (game %s)", game_key, exc_info=True)
    guild = interaction.client.get_guild(int(interaction.guild_id)) if interaction.guild_id else None
    if guild is not None:
        from kingdoms.discord.live import scope_snapshot_to_guild

        snapshot = await scope_snapshot_to_guild(snapshot, guild)
    active_ids = [
        str(p.get("profile_id", ""))
        for p in snapshot.get("players", [])
        if p.get("state") != "offline"
    ]
    stats = await collect_profile_stats(active_ids, game_key)
    from kingdoms.discord.content_posts import guild_locale

    locale = await guild_locale(str(interaction.guild_id), interaction.client)
    body = render_dashboard(snapshot, locale=locale, stats=stats, bot=interaction.client)
    from kingdoms.discord.live import build_dashboard_view

    # THE dashboard rendering (unified, owner direction): the same view
    # the pinned channel shows — Container + the id under it, one code path.
    view = build_dashboard_view(snapshot, body, interaction.client, locale=locale, game_key=game_key)
    return view


async def _view_mods_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Render the guild's mods (DM re-dispatch friendly: ack first, then read)."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(view=await _mods_layout(interaction.client, guild_id, interaction), ephemeral=True)


def _mod_visible_to(
    bot: Any, mod: str, user_id: str | None
) -> bool:
    """Whether a mod is visible to one user.

    Public mods always are; private mods only to their owners and the
    bot admins (click-time guards apply on top for actions).
    """
    registry = getattr(bot, "registry", None)
    definition = registry.enabled().get(mod) if registry is not None else None
    if definition is None or not definition.is_private():
        return True
    if not user_id:
        return False
    admins = set(getattr(getattr(bot, "status_service", None), "bot_admins", ()) or ())
    return str(user_id) in admins or definition.owned_by(str(user_id))


async def _mods_layout(
    bot: Any, guild_id: str, interaction: Any = None
) -> discord.ui.LayoutView:
    """Build the server's mods view: available mods, bullets, no doc line."""
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
    viewer = (
        str(getattr(interaction.user, "id", "") or "")
        if interaction is not None
        else None
    )
    visible = tuple(mod for mod in (granted or all_mods) if _mod_visible_to(bot, mod, viewer))
    lines: list[str] = []
    for mod in visible[:25]:
        channels: list[str] = []
        general = await _tree_channel_link(guild_id, f"mods/{mod}", "general")
        if general:
            channels.append(general)
        admin = await _tree_channel_link(guild_id, f"mods/{mod}", "admin")
        if admin:
            channels.append(f"admin : {admin}")
        suffix = f" \u2014 {', '.join(channels)}" if channels else ""
        lines.append(f"\u2022 **{mod}**{suffix}")
    header = await reply(interaction, "profile.mods_title") if interaction is not None else "# \U0001f9e9 Mods"
    subtitle = (
        await reply(interaction, "profile.mods_subtitle") if interaction is not None else ""
    )
    body = header + ("\n" + subtitle if subtitle else "")
    if lines:
        available = (
            await reply(interaction, "profile.mods_available") if interaction is not None else "Mods disponibles :"
        )
        body += "\n\n" + available + "\n" + "\n".join(lines)
    else:
        none_note = (
            await reply(interaction, "profile.mods_none") if interaction is not None else "Aucun mod actif."
        )
        body += "\n\n" + none_note
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(body),
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
    """Return whether one user is a member of the guild (the cached roster).

    An unresolvable roster (empty fetch) accepts everyone — fail open:
    an API hiccup must never blank the roster panel.
    """
    from kingdoms.discord.member_cache import guild_members

    members = await guild_members(guild)
    if not members:
        return True
    return user_id in members


async def _view_users_for_guild(interaction: discord.Interaction, guild_id: str) -> None:
    """Render the guild's roster (DM re-dispatch friendly: ack first)."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=False)
    bot = interaction.client
    roster = getattr(bot, "_registration_database", None)
    if roster is None:
        await interaction.followup.send(await reply(interaction, "profile.roster_unavailable"), ephemeral=True)
        return
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        await interaction.followup.send(await reply(interaction, "profile.guild_not_found"), ephemeral=True)
        return
    unique = await _collect_roster_bindings(bot, guild_id)
    from collections import defaultdict


    by_member: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in unique:
        user_id = str(binding.get("user_id", ""))
        if not user_id.isdigit() or not await _is_guild_member(guild, int(user_id)):
            continue
        by_member[user_id].append(binding)
    member_lines: list[str] = []
    # the pseudo rides the member cache (the chunked roster — the
    # guild's internal cache misses members it never saw)
    from kingdoms.discord.member_cache import guild_members

    cached_members = await guild_members(guild) if guild is not None else {}
    for user_id, bindings in list(by_member.items())[:25]:
        display = cached_members.get(int(user_id), "") if cached_members else ""
        header_name = display or f"<@{user_id}>"
        member_lines.append(f"## {header_name} \u2014 @{display or user_id}")
        for binding in bindings:
            profile = binding.get("profile") or {}
            aliases = [str(a) for a in (profile.get("aliases") or []) if str(a)]
            name = (
                str(profile.get("display_name") or profile.get("name") or "")
                or (aliases[0] if aliases else "")
                or "?"
            )
            pid = str(binding.get("profile_id", "?"))
            game = str(binding.get("game_key", "?"))
            url = _game_account_url(game, pid)
            account = f"[{name}]({url})" if url else name
            member_lines.append(f"\u2022 {account} (`{pid}`)")
    if not member_lines:
        await interaction.followup.send(await reply(interaction, "profile.no_members_bound"), ephemeral=True)
        return
    title = await reply(interaction, "profile.users_title", count=len(by_member))
    server_line = await reply(interaction, "profile.users_server", server=guild.name)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"{title}\n\n{server_line}\n\n" + "\n".join(member_lines)),
            accent_colour=discord.Colour(0x5865F2),
        )
    )
    await interaction.followup.send(view=view, ephemeral=True)


class LiveGamePicker(discord.ui.Modal):
    """The /live game picker: one select, the installed games.

    A modal (not a bare select response) per the owner's direction —
    the picker presents the games, the chosen game's dashboard renders
    ephemerally on submit.
    """

    def __init__(self, games: list[tuple[str, str]]) -> None:
        super().__init__(title="Live dashboard — pick a game")
        self._games = dict(games)
        self.game_select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Game",
            options=[
                discord.SelectOption(label=label, value=game_key)
                for game_key, label in games
            ],
        )
        self.add_item(self.game_select)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Render the chosen game's dashboard ephemerally."""
        game_key = self.game_select.values[0] if self.game_select.values else "aoe2"
        await _view_live(interaction, game_key)


def register_live_command(
    tree: app_commands.CommandTree[discord.Client],
    catalog: Any = None,
    games: tuple[str, ...] = ("aoe2",),
) -> None:
    """Register the /live command: pick a game, see its live dashboard.

    Generic (owner direction 2026-10-23): a modal + select lists the
    installed games; the chosen game's dashboard renders ephemerally.
    """

    @tree.command(
        name=localized("commands.live_name", "live"),
        description=localized(
            "commands.live_description",
            "Live dashboard: pick a game, see its linked players",
        ),
    )
    async def live_command(interaction: discord.Interaction) -> None:
        """Open the game picker modal."""
        options = [(game_key, _game_label(game_key)) for game_key in games]
        if len(options) == 1:
            await _view_live(interaction, options[0][0])
            return
        await interaction.response.send_modal(LiveGamePicker(options))

    del catalog


def _view_users_for_game(game_key: str) -> Any:
    """Build the players panel opener for one game (generic, per game).

    One line per registered member — pseudo, Discord mention, then
    every profile of that game as an aoe2insights link — sorted
    alphabetically by pseudo. The whole roster shows: the lines are
    paginated across as many ephemeral messages as the 4000-char V2
    text budget needs (no [:25] truncation).
    """

    async def _open(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=False)
        bot = interaction.client
        roster = getattr(bot, "_registration_database", None)
        if roster is None:
            await interaction.followup.send(await reply(interaction, "not_configured"), ephemeral=True)
            return
        try:
            bindings = await roster.list_bindings_for_game(game_key)
        except Exception:
            logger.warning("PLAYERS PANEL roster read failed (%s)", game_key, exc_info=True)
            bindings = []

        from collections import defaultdict

        by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for binding in bindings:
            user_id = str(binding.get("user_id", "") or "")
            if user_id:
                by_user[user_id].append(binding)

        guild = bot.get_guild(int(interaction.guild_id)) if interaction.guild_id else None
        # guild-scoped: only the enrolled players who are MEMBERS of this
        # guild render (owner direction — the platform's roster, not the
        # game's global one)
        if guild is not None:
            member_ids = [
                user_id
                for user_id in by_user
                if await _is_guild_member(guild, int(user_id))
            ]
            by_user = {user_id: by_user[user_id] for user_id in member_ids}
        profile_ids = [str(b.get("profile_id", "")) for bs in by_user.values() for b in bs]
        from kingdoms.discord.live import collect_profile_stats

        names = await collect_profile_stats(profile_ids, game_key)
        lines = await _roster_lines(game_key, by_user, guild, names)
        if not lines:
            lines = [await reply(interaction, "profile.no_members_bound")]
        for view in _roster_views(game_key, lines):
            await interaction.followup.send(view=view, ephemeral=True)

    return _open


async def _roster_lines(
    game_key: str,
    by_user: dict[str, list[dict[str, Any]]],
    guild: Any,
    names: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """One line per member (pseudo, mention, profile links), alphabetical by pseudo.

    A profile link's label is the profile's NAME — the binding's stored
    display name, else the provider-resolved alias (librematch identity),
    never the raw profile id.
    """
    names = names or {}
    from kingdoms.discord.member_cache import guild_members

    cached_members = await guild_members(guild) if guild is not None else {}
    entries: list[tuple[str, str, str]] = []
    for user_id, user_bindings in by_user.items():
        # the pseudo rides the member cache (the guild's own cache can be
        # incomplete); the RAW user id shows when even the cache misses —
        # the mention follows, always the same readable shape
        pseudo = cached_members.get(int(user_id), "") if cached_members else ""
        mention = f"<@{user_id}>"
        profiles: list[str] = []
        for binding in user_bindings:
            # the link label: the stored name, else the provider-resolved
            # alias (passed through ``names``), never the raw profile id
            pid = str(binding.get("profile_id", "?"))
            profile = binding.get("profile") or {}
            stored = str(profile.get("profile_name") or "")
            alias = str((names.get(pid) or {}).get("display_name") or "")
            name = stored or alias or pid
            url = _game_account_url(game_key, pid)
            profiles.append(f"[{name}]({url})" if url else name)
        entries.append((pseudo or f"user {user_id}", mention, ", ".join(profiles)))
    entries.sort(key=lambda e: _casefold_key(e[0]))
    return [f"\u2022 **{pseudo}** \u2014 {mention}: {profiles}" for pseudo, mention, profiles in entries]


def _roster_views(game_key: str, lines: list[str]) -> list[Any]:
    """Build the roster's V2 views: one per page of the character budget."""
    from kingdoms.core.ids import footer

    header = f"# \U0001F465 {_game_label(game_key)}\n\n"
    view_id = f"panel:game:{game_key}:players"
    budget = 4000 - len(header) - len(footer(view_id)) - 64
    pages = _paginate_lines(lines, budget)
    views: list[Any] = []
    for page_index, page in enumerate(pages):
        title = header
        if len(pages) > 1:
            title += f"*({page_index + 1}/{len(pages)})*\n\n"
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(title + page),
                accent_colour=discord.Colour(0x5865F2),
            )
        )
        view.add_item(discord.ui.TextDisplay(footer(view_id)))
        views.append(view)
    return views


def _casefold_key(text: str) -> str:
    """Sort key that ignores case and diacritics (pseudo alphabetical order)."""
    import unicodedata

    stripped = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).casefold()


def _paginate_lines(lines: list[str], budget: int) -> list[str]:
    """Split lines into pages that each fit the character budget."""
    pages: list[str] = []
    current: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if current and used + cost > budget:
            pages.append("\n".join(current))
            current = []
            used = 0
        current.append(line)
        used += cost
    if current:
        pages.append("\n".join(current))
    return pages or [""]


async def open_home_view_for_guild(interaction: discord.Interaction, view_key: str, guild_id: str) -> None:
    """Re-dispatch a declared view with an explicit guild scope (DM pick).

    The DM guild picker re-dispatches a view on a chosen guild; the
    declared router owns the resolution (the legacy view keys map
    through the same table as the home buttons).
    """
    from kingdoms.discord.views_routing import open_declared_view

    await open_declared_view(interaction, _LEGACY_VIEW_KEYS.get(view_key, view_key))


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
    """Route to a mod's home view builder (private mods: owners/admins)."""
    bot = interaction.client
    viewer = str(getattr(interaction.user, "id", "") or "")
    if not _mod_visible_to(bot, mod, viewer):
        await interaction.response.send_message(await reply(interaction, "no_home_view"), ephemeral=True)
        return
    builder = getattr(bot, "mod_home_builders", {}).get(mod)
    if builder is None:
        await interaction.response.send_message(await reply(interaction, "no_home_view"), ephemeral=True)
        return
    await builder(interaction)


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
        """Answer /home with the declared home menu (ephemeral, localized)."""
        from kingdoms.discord.views_routing import open_declared_view

        await open_declared_view(interaction, "menu:home")


