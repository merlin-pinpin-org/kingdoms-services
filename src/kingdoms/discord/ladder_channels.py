"""The ladder's channel surface: a Ladder category, four self-healing salons.

The ladder mod owns four public salons under one ``Ladder`` category
(#221), provisioned and healed like every managed surface:

- ``🏰-ladder-home``       — the pinned ladder menu (the button-only home);
- ``📊-ladder-dashboard``  — the live queue (who waits, since when);
- ``🏆-ladder-leaderboard``— the ranking (top players, ratings, W/L);
- ``📜-ladder-history``   — the played matches (recent completed games).

Nothing here is a command: the home is the pinned menu, the three data
salons are bot-written and member-read. The home menu is the same button-
only layout the ephemeral home answers with (the DynamicItems serve both
surfaces — one custom_id, one dispatch path, restart-proof), kept alive
by the same PinnedMenuService as the admin pin. Each data salon holds
**one** message, addressed by logical key through the message registry
(the #130 contract: platform message ids live only there): the sync
edits it in place, recreates it when deleted — the salon is never
spammed, only ever current. The ladder home salon hosts the pinned menu (same
button-only layout the ephemeral home answers with; the DynamicItems
serve both surfaces — one custom_id, one dispatch path).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import discord

logger = logging.getLogger("kingdoms.ladder.channels")

LADDER_CATEGORY_PREFIX = "Ladder - "
HOME_CHANNEL_NAME = "🏰-ladder-home"
DASHBOARD_CHANNEL_NAME = "📊-ladder-dashboard"
LEADERBOARD_CHANNEL_NAME = "🏆-ladder-leaderboard"
HISTORY_CHANNEL_NAME = "📜-ladder-history"

LADDER_PIN_MARKER = "ladder:home:"

_REGISTRY_PLATFORM = "discord"
_SALON_KEYS = {
    DASHBOARD_CHANNEL_NAME: "ladder:dashboard",
    LEADERBOARD_CHANNEL_NAME: "ladder:leaderboard",
    HISTORY_CHANNEL_NAME: "ladder:history",
}
SYNC_INTERVAL_S = 300


def ladder_channels_wiring_ready() -> bool:
    """Whether the ladder salons can be provisioned (Mongo configured)."""
    return bool(os.environ.get("MONGO_URI"))


def _channel_by_name(guild: Any, name: str) -> Any | None:
    for channel in guild.channels:
        if channel.name == name:
            return channel
    return None


async def _active_season(wiring: Any, ladder_id: str) -> Any | None:
    """Resolve the ladder's active season; None before the first season."""
    if wiring is None or wiring.season_service is None or not ladder_id:
        return None
    try:
        return await wiring.season_service.get_active_season(ladder_id)
    except Exception:
        logger.warning("ladder channels: active season resolve failed", exc_info=True)
        return None


def season_category_name(season_name: str) -> str:
    """Format the season's channel category: 'Ladder - Saison 1'."""
    return f"{LADDER_CATEGORY_PREFIX}{season_name}"


def _season_scope(season: Any) -> str:
    """Return the registry scope of one season's salons (per-season isolation)."""
    return str(getattr(season, "id", "") or "")


def _season_display_name(season: Any) -> str:
    return str(getattr(season, "name", "") or "saison")


def _season_slug(season_name: str) -> str:
    """Return the channel-name slug of a season ('Saison 1' -> 'saison-1')."""
    import re as _re

    return _re.sub(r"[^a-z0-9]+", "-", season_name.lower()).strip("-") or "saison"


async def sync_ladder_channels(guild: Any, bot: Any) -> None:
    """Ensure the season's category, the four salons and their content.

    The salons are **season-scoped**: each season gets its own category
    ``Ladder - <season name>`` with the four salons, and its salon messages
    are keyed per season in the message registry — creating a season
    provisions a fresh surface; the previous season's channels stay as
    history. Without an active season the surface stays silent: the
    channels belong to the season.
    """
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    wiring = _ladder_wiring()
    ladder_id = str(getattr(bot, "_ladder_id", "") or "")
    season = await _active_season(wiring, ladder_id)
    if season is None:
        return
    platform = DiscordChannelsPlatform(bot)
    guild_id = str(guild.id)
    season_name = _season_display_name(season)
    category_id = await platform.ensure_category(guild_id, season_category_name(season_name))
    # The salons carry the season's short name: a new season provisions a
    # fresh set under its own category, the previous season's channels stay
    # as history (ensure_channel would otherwise adopt the old season's
    # salon by name and hoist it under the new category).
    suffix = _season_slug(season_name)
    for name in (HOME_CHANNEL_NAME, DASHBOARD_CHANNEL_NAME, LEADERBOARD_CHANNEL_NAME, HISTORY_CHANNEL_NAME):
        await platform.ensure_channel(guild_id, f"{name}-{suffix}", category_id)
    if wiring is None or not ladder_id:
        return
    scope = _season_scope(season)
    await _ensure_pinned_home(guild, scope, suffix)
    surface = _ladder_surface(wiring)
    await _sync_dashboard(guild, surface, ladder_id, scope, suffix)
    await _sync_leaderboard(guild, surface, ladder_id, scope, suffix)
    await _sync_history(guild, wiring, ladder_id, scope, suffix)


def _ladder_wiring() -> Any | None:
    try:
        from kingdoms.discord.ladder_commands import build_ladder_wiring

        return build_ladder_wiring()
    except Exception:
        logger.warning("ladder channels: wiring build failed", exc_info=True)
        return None


def _ladder_surface(wiring: Any) -> Any:
    from kingdoms.mods.ladder.surface import LadderSurface

    return LadderSurface(wiring.service)


def _ladder_registry() -> Any | None:
    try:
        from kingdoms.discord.messages_platform import build_message_registry

        return build_message_registry()
    except Exception:
        logger.warning("ladder channels: message registry build failed", exc_info=True)
        return None


async def _ladder_flags(guild_id: str) -> tuple[bool, bool]:
    """Resolve (enrollments_open, queue_paused) for the guild's ladder; open+active when unresolvable."""
    wiring = _ladder_wiring()
    if wiring is None:
        return True, False
    ladder_id = str(getattr(wiring.bot, "_ladder_id", "") or "")
    if not ladder_id:
        return True, False
    try:
        ladder = await wiring.service.get_ladder(ladder_id)
    except Exception:
        return True, False
    if ladder is None:
        return True, False
    return bool(ladder.enrollments_open), bool(ladder.queue_paused)


async def _ensure_pinned_home(guild: Any, scope: str, suffix: str) -> None:
    """Keep the pinned ladder menu alive in the home salon (self-healing)."""
    from typing import cast

    from kingdoms.core.services.pinned_menu import PinnedMenuChannel, PinnedMenuService
    from kingdoms.discord.ladder_home import build_ladder_menu_layout

    channel = _channel_by_name(guild, f"{HOME_CHANNEL_NAME}-{suffix}")
    if channel is None:
        return

    class _Delivery:
        async def deliver(self, channel: object, layout: object) -> str:
            message = await channel.send(view=layout)  # type: ignore[attr-defined]
            return str(message.id)

    async def _build(guild_id: str) -> object:
        enrollments_open, queue_paused = await _ladder_flags(guild_id)
        return build_ladder_menu_layout(enrollments_open=enrollments_open, queue_paused=queue_paused)

    service = PinnedMenuService(cast("Any", _Delivery()))
    await service.ensure(
        f"{guild.id}:{scope}",
        cast("PinnedMenuChannel", channel),
        marker=LADDER_PIN_MARKER,
        build_layout=_build,
        pin_reason="kingdoms: pinned ladder menu (ladder home salon)",
    )


async def _salon_message(guild: Any, channel_name: str, key: str, view: Any, scope: str) -> None:
    """Edit-in-place one salon's message (registry-addressed); recreate when gone."""
    channel = _channel_by_name(guild, channel_name)
    registry = _ladder_registry()
    if channel is None or registry is None:
        return
    registered = await registry.resolve(_REGISTRY_PLATFORM, key, f"{guild.id}:{scope}")
    if registered is not None:
        try:
            message = await channel.fetch_message(int(registered.message_id))
            await message.edit(view=view)
            return
        except Exception:
            await registry.forget(_REGISTRY_PLATFORM, key, f"{guild.id}:{scope}")
    message = await channel.send(view=view)
    await registry.register(
        platform=_REGISTRY_PLATFORM,
        message_key=key,
        entity_id=str(guild.id),
        channel_id=str(channel.id),
        message_id=str(message.id),
        guild_id=f"{guild.id}:{scope}",
    )


async def _sync_dashboard(guild: Any, surface: Any, ladder_id: str, scope: str, suffix: str) -> None:
    """Render the queue salon: the live queue plus the join/leave actions."""
    from kingdoms.discord.ladder_home import LadderJoinButton, LadderLeaveButton

    rows = await surface.queue_view(ladder_id, now=int(time.time() * 1000))
    if rows:
        lines = [f"**{r.display_name}** — {r.rating} elo (attente {r.wait_seconds // 60} min)" for r in rows]
    else:
        lines = ["_Personne en queue — le premier à rejoindre ouvre le bal._"]
    view = discord.ui.LayoutView(timeout=None)
    actions: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    join = LadderJoinButton()
    leave = LadderLeaveButton()
    wiring = _ladder_wiring()
    if wiring is not None:
        ladder = await wiring.service.get_ladder(ladder_id)
        if ladder is not None:
            join.item.disabled = bool(ladder.queue_paused) or not bool(ladder.enrollments_open)
            leave.item.disabled = bool(ladder.queue_paused)
    actions.add_item(join)
    actions.add_item(leave)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("## 📊 File d'attente\n" + "\n".join(lines))))
    view.add_item(actions)
    await _salon_message(
        guild, f"{DASHBOARD_CHANNEL_NAME}-{suffix}", _SALON_KEYS[DASHBOARD_CHANNEL_NAME], view, scope
    )


async def _sync_leaderboard(guild: Any, surface: Any, ladder_id: str, scope: str, suffix: str) -> None:
    rows = await surface.leaderboard_view(ladder_id, page_size=15)
    if rows:
        lines = [f"**#{r.rank}** {r.display_name} — {r.rating} elo ({r.wins}V/{r.losses}D)" for r in rows]
    else:
        lines = ["_Aucun joueur classé pour l'instant._"]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("## 🏆 Classement\n" + "\n".join(lines))))
    await _salon_message(
        guild, f"{LEADERBOARD_CHANNEL_NAME}-{suffix}", _SALON_KEYS[LEADERBOARD_CHANNEL_NAME], view, scope
    )


async def _sync_history(guild: Any, wiring: Any, ladder_id: str, scope: str, suffix: str) -> None:
    from kingdoms.mods.ladder.models import MATCH_STATUS_COMPLETED

    docs = await wiring.service._db.find_ladder_matches(ladder_id, [MATCH_STATUS_COMPLETED])
    lines = [
        f"**{d.get('winner_user_id') or '?'}** bat {d.get('loser_user_id') or '?'}" for d in docs[:10]
    ] or ["_Aucun match joué pour l'instant._"]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("## 📜 Derniers matchs\n" + "\n".join(lines))))
    await _salon_message(
        guild, f"{HISTORY_CHANNEL_NAME}-{suffix}", _SALON_KEYS[HISTORY_CHANNEL_NAME], view, scope
    )


def start_ladder_channels_sync(bot: Any) -> asyncio.Task[None]:
    """Sync the ladder salons periodically, forever, quietly (self-healing)."""

    async def _loop() -> None:
        await asyncio.sleep(5)
        while True:
            try:
                for guild in list(bot.guilds):
                    await sync_ladder_channels(guild, bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("ladder channels sync failed — best-effort", exc_info=True)
            await asyncio.sleep(SYNC_INTERVAL_S)

    return asyncio.create_task(_loop())
