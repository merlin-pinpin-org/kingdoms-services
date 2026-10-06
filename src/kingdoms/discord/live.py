"""The /live dashboard command and its dedicated channel (#147).

The live test dashboard: a dedicated ``live-dashboard`` channel hosts one
persistent message showing every linked player's current state
(offline / in lobby / in game, match ref, since when). The message is
edited in place on every update — no spam. ``/live`` renders the same
dashboard ephemerally; ``/game-link`` and ``/game-unlink`` manage the
minimal profile links needed to test the providers (test-scoped, kept
simple per #147).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Protocol

import discord
from discord import app_commands

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.commands_i18n import localized

logger = logging.getLogger("kingdoms.live")

LIVE_CHANNEL_NAME = "live-dashboard"
LIVE_MESSAGE_KEY = "live-dashboard"

_STATE_ICONS = {"offline": "⚫", "in_lobby": "🟡", "in_game": "🟢"}


def render_dashboard(snapshot: dict[str, Any], locale: str = "en") -> str:
    """Render the dashboard snapshot as a plain-text message body."""
    lines: list[str] = []
    if snapshot.get("degraded"):
        lines.append("⚠️ Providers unreachable — states may be stale, all shown offline.")
    players = snapshot.get("players", [])
    if not players:
        lines.append("No linked players yet — link a profile with /game-link.")
    for p in players:
        icon = _STATE_ICONS.get(p["state"], "⚫")
        since = f" (since <t:{p['since'] // 1000}:R>)" if p.get("since") else ""
        ref = f" — match `{p['match_ref']}`" if p.get("match_ref") else ""
        lines.append(f"{icon} <@{p['user_id']}> — {p['state']}{ref}{since}")
    return "\n".join(lines)


def register_live_commands(
    tree: app_commands.CommandTree[discord.Client],
    catalog: MessageCatalog | None = None,
) -> None:
    """Register the /live, /game-link and /game-unlink commands.

    The commands read svc-core's Live service through ``LiveClient``
    when ``CORE_URI`` is set; without it (local runs, providers down),
    the dashboard degrades to a note — the command still answers.
    """
    from kingdoms.core.rpc.live import LiveClient

    core_uri = os.environ.get("CORE_URI", "")
    client = LiveClient(core_uri) if core_uri else None

    def _render_locale(catalog: MessageCatalog | None, locale: str, key: str, fallback: str) -> str:
        if catalog is None:
            return fallback
        return catalog.render(key, locale)

    @tree.command(
        name=localized("commands.live_name", "live"),
        description=localized("commands.live_description", "Live test dashboard: linked players, lobbies and games"),
    )
    async def live_command(interaction: discord.Interaction) -> None:
        """Answer /live with the current dashboard snapshot."""
        await interaction.response.defer(ephemeral=True)
        if client is None:
            await interaction.followup.send("Live dashboard unavailable: CORE_URI not configured.", ephemeral=True)
            return
        try:
            snapshot = await client.watch("aoe2")
        except Exception:
            logger.warning("live snapshot fetch failed", exc_info=True)
            snapshot = {"players": [], "generated_at": 0, "degraded": True}
        body = render_dashboard(snapshot, _locale(interaction))
        await interaction.followup.send(body, ephemeral=True)

    @tree.command(
        name=localized("commands.game_link_name", "game-link"),
        description=localized(
            "commands.game_link_description", "Link your AoE2 profile id to your account (test scope)"
        ),
    )
    @app_commands.describe(profile_id="Your AoE2 profile id")
    async def game_link_command(interaction: discord.Interaction, profile_id: str) -> None:
        """Link the invoker's AoE2 profile (minimal test-scoped link)."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        if interaction.user.id is None or profile_id.strip() == "":
            await interaction.response.send_message("A profile id is required.", ephemeral=True)
            return
        profile = profile_id.strip()
        binding = {
            "_id": f"binding:aoe2:{interaction.user.id}:{profile}",
            "user_id": str(interaction.user.id),
            "game_key": "aoe2",
            "profile_id": profile,
            "bound_at": int(_now_ms()),
        }
        db = get_async_database()[PROFILE_BINDINGS_COLLECTION]
        await db.replace_one({"_id": binding["_id"]}, binding, upsert=True)
        await interaction.response.send_message(
            f"Linked profile `{profile}` to <@{interaction.user.id}> — the dashboard will show it. "
            "Link more profiles by running /game-link again.",
            ephemeral=True,
        )

    @tree.command(
        name=localized("commands.game_unlink_name", "game-unlink"),
        description=localized("commands.game_unlink_description", "Unlink one of your AoE2 profiles"),
    )
    @app_commands.describe(profile_id="The AoE2 profile id to unlink")
    async def game_unlink_command(interaction: discord.Interaction, profile_id: str) -> None:
        """Remove one of the invoker's AoE2 profile links."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        profile = profile_id.strip()
        db = get_async_database()[PROFILE_BINDINGS_COLLECTION]
        removed = await db.delete_one({"_id": f"binding:aoe2:{interaction.user.id}:{profile}"})
        if removed and removed.deleted_count > 0:
            await interaction.response.send_message(f"Profile `{profile}` unlinked.", ephemeral=True)
        else:
            await interaction.response.send_message(f"No link found for profile `{profile}`.", ephemeral=True)


def _locale(interaction: discord.Interaction) -> str:
    """Best-effort locale: the user's Discord locale, defaulting to en."""
    return str(interaction.locale) if interaction.locale else "en"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    return int(time.time() * 1000)


class LiveClientLike(Protocol):
    """The svc-core live client seam (LiveClient)."""

    async def watch(self, game_key: str) -> dict[str, Any]:
        """Fetch the current dashboard snapshot for a game."""
        ...


class RegisteredMessageLike(Protocol):
    """A resolved registered message (RegisteredMessageModel)."""

    @property
    def channel_id(self) -> str:
        """The channel the message lives in."""
        ...

    @property
    def message_id(self) -> str:
        """The platform message id."""
        ...


class MessageRegistryServiceLike(Protocol):
    """The message registry seam (MessageRegistryService)."""

    async def resolve(self, platform: str, message_key: str, entity_id: str) -> RegisteredMessageLike | None:
        """Resolve a registered message; None when never registered."""
        ...

    async def register(
        self,
        *,
        platform: str,
        message_key: str,
        entity_id: str,
        channel_id: str,
        message_id: str,
        guild_id: str | None = None,
    ) -> None:
        """Persist (or replace) a registered message."""
        ...


async def ensure_live_dashboard_channel(guild: discord.Guild) -> discord.TextChannel:
    """Resolve or create the guild's live-dashboard channel (idempotent)."""
    for channel in guild.text_channels:
        if channel.name == LIVE_CHANNEL_NAME:
            return channel
    return await guild.create_text_channel(LIVE_CHANNEL_NAME, reason="Kingdoms live test dashboard (#147)")


async def ensure_live_dashboard(
    bot: discord.Client,
    guild_id: str,
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
) -> bool:
    """Ensure the guild's dashboard channel holds one up-to-date dashboard message.

    The message is addressed by its logical key (``live-dashboard``)
    through the message registry and edited in place on every refresh —
    never re-sent, so the channel never spams. When the registered
    message is gone (deleted, channel wiped), a new one is created and
    registered. Best-effort: a failure never blocks the bot.
    Returns True when a message was created, False when an existing
    one was refreshed (or the step degraded quietly).
    """
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return False
    channel = await ensure_live_dashboard_channel(guild)
    try:
        snapshot = await client.watch(GAME_KEY)
    except Exception:
        logger.warning("live dashboard fetch failed (guild %s) — best-effort", guild_id, exc_info=True)
        snapshot = {"players": [], "generated_at": _now_ms(), "degraded": True}
    body = render_dashboard(snapshot)
    registered = await registry.resolve(PLATFORM, LIVE_MESSAGE_KEY, guild_id)
    if await _edit_registered(channel, registered, body):
        return False
    message = await channel.send(body)
    await registry.register(
        platform=PLATFORM,
        message_key=LIVE_MESSAGE_KEY,
        entity_id=guild_id,
        channel_id=str(channel.id),
        message_id=str(message.id),
        guild_id=guild_id,
    )
    return True


async def _edit_registered(
    channel: discord.TextChannel,
    registered: RegisteredMessageLike | None,
    body: str,
) -> bool:
    """Edit the registered message in place; True when the edit landed."""
    if registered is None or not str(registered.channel_id).isdigit() or not str(registered.message_id).isdigit():
        return False
    try:
        message = channel.get_partial_message(int(registered.message_id))
        await message.edit(content=body)
        return True
    except Exception:
        logger.warning(
            "live dashboard edit failed (message %s) — recreating",
            getattr(registered, "message_id", "?"),
            exc_info=True,
        )
        return False


async def start_live_dashboard_refresh(
    bot: discord.Client,
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
) -> asyncio.Task[None]:
    """Refresh every guild's dashboard message on an interval, forever."""

    async def _loop() -> None:
        while True:
            for guild in list(bot.guilds):
                try:
                    await ensure_live_dashboard(bot, str(guild.id), client, registry)
                except Exception:
                    logger.warning("live dashboard refresh failed (guild %s) — best-effort", guild.id, exc_info=True)
            await asyncio.sleep(DASHBOARD_REFRESH_INTERVAL_S)

    return asyncio.create_task(_loop())


GAME_KEY = "aoe2"
PLATFORM = "discord"
DASHBOARD_REFRESH_INTERVAL_S = 15
