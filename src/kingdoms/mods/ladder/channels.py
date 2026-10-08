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
SEASON_ADMIN_CHANNEL_NAME = "\U0001f6e1-season-admin"

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


def _channel_by_id(guild: Any, channel_id: str) -> Any | None:
    for channel in guild.channels:
        if str(channel.id) == str(channel_id):
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
    wiring = _ladder_wiring()
    ladder_id = str(getattr(bot, "_ladder_id", "") or "")
    season = await _active_season(wiring, ladder_id)
    if season is None:
        return
    season_name = _season_display_name(season)
    suffix = _season_slug(season_name)
    scope = _season_scope(season)
    channels = await _season_channels(guild, bot, scope, season_name, suffix)
    if channels is None:
        return
    await _apply_season_read_only(guild, channels, scope)
    await _ensure_pinned_home(guild, scope, channels)
    await _ensure_pinned_season_admin(guild, scope, channels)
    surface = _ladder_surface(wiring)
    await _sync_dashboard(guild, channels, surface, ladder_id, scope)
    await _sync_leaderboard(guild, channels, surface, ladder_id, scope)
    await _sync_history(guild, channels, wiring, ladder_id, scope)




class _SeasonChannels:
    """One season's salon ids, resolved by stored id — never by name."""

    def __init__(
        self,
        category_id: str,
        home: str,
        dashboard: str,
        leaderboard: str,
        history: str,
        season_admin: str,
    ) -> None:
        self.category_id = category_id
        self.home = home
        self.dashboard = dashboard
        self.leaderboard = leaderboard
        self.history = history
        self.season_admin = season_admin


async def _season_channels(guild: Any, bot: Any, scope: str, season_name: str, suffix: str) -> _SeasonChannels | None:
    """Provision and resolve the season's category and salons (id-based).

    Each salon (and the season category) is persisted in the channels
    registry under a logical key; the stored ids win, the names only
    label the first creation — a renamed channel stays found, a deleted
    one is re-provisioned under its logical key.
    """
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    guild_id = str(guild.id)
    category_key = f"ladder-category:{scope}"
    salon_specs = (
        (f"ladder-home:{scope}", f"{HOME_CHANNEL_NAME}-{suffix}"),
        (f"ladder-dashboard:{scope}", f"{DASHBOARD_CHANNEL_NAME}-{suffix}"),
        (f"ladder-leaderboard:{scope}", f"{LEADERBOARD_CHANNEL_NAME}-{suffix}"),
        (f"ladder-history:{scope}", f"{HISTORY_CHANNEL_NAME}-{suffix}"),
        (f"ladder-season-admin:{scope}", f"{SEASON_ADMIN_CHANNEL_NAME}-{suffix}"),
    )
    db = await _channels_db()
    if db is None:
        return None

    category_id = await _resolve_or_create_category(
        guild, platform, db, guild_id, category_key, season_category_name(season_name)
    )
    if category_id is None:
        return None
    ids: dict[str, str] = {}
    for key, name in salon_specs:
        channel_id = await _resolve_or_create_channel(guild, platform, db, guild_id, key, name, category_id)
        if channel_id is not None:
            ids[key] = channel_id
    if len(ids) != len(salon_specs):
        return None
    return _SeasonChannels(
        category_id=category_id,
        home=ids[salon_specs[0][0]],
        dashboard=ids[salon_specs[1][0]],
        leaderboard=ids[salon_specs[2][0]],
        history=ids[salon_specs[3][0]],
        season_admin=ids[salon_specs[4][0]],
    )


async def _apply_season_read_only(guild: Any, channels: _SeasonChannels, scope: str) -> None:
    """Keep the season salons read-only (pinned surfaces; ids, never names)."""
    from kingdoms.discord.pinned_views import apply_read_only_policy

    for channel_id, category in (
        (channels.home, f"ladder:home:{scope}"),
        (channels.dashboard, f"ladder:dashboard:{scope}"),
        (channels.leaderboard, f"ladder:leaderboard:{scope}"),
        (channels.history, f"ladder:history:{scope}"),
        (channels.season_admin, f"ladder:season-admin:{scope}"),
    ):
        await apply_read_only_policy(guild, _channel_by_id(guild, channel_id), category, str(guild.id))


async def _channels_db() -> Any | None:
    """Build the channels persistence seam; None when Mongo is absent."""
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.discord.logs_platform import MongoLogsDatabase

        return MongoLogsDatabase(get_async_database())
    except Exception:
        logger.warning("ladder channels: channels db build failed", exc_info=True)
        return None


async def _resolve_or_create_category(
    guild: Any, platform: Any, db: Any, guild_id: str, key: str, name: str
) -> str | None:
    """Resolve the category by stored id; adopt by name once; else create."""
    from kingdoms.core.models.channel import ChannelModel

    stored = await db.find_channel(guild_id, key)
    if stored is not None and _category_exists(guild, stored.channel_id):
        return str(stored.channel_id)
    if stored is not None:
        await db.delete_channel(guild_id, key)
    existing = _category_by_name(guild, name)
    category_id = existing if existing is not None else await platform.ensure_category(guild_id, name)
    await db.upsert_channel(
        ChannelModel(
            _id=f"{guild_id}:{key}",
            guild_id=guild_id,
            platform="discord",
            category=key,
            channel_id=category_id,
            name=name,
        )
    )
    return category_id


async def _resolve_or_create_channel(
    guild: Any, platform: Any, db: Any, guild_id: str, key: str, name: str, category_id: str
) -> str | None:
    """Resolve a salon by stored id; adopt by name once; else create."""
    from kingdoms.core.models.channel import ChannelModel

    stored = await db.find_channel(guild_id, key)
    if stored is not None and _channel_exists(guild, stored.channel_id):
        return str(stored.channel_id)
    if stored is not None:
        await db.delete_channel(guild_id, key)
    channel_id = await platform.ensure_channel(guild_id, name, category_id)
    await db.upsert_channel(
        ChannelModel(
            _id=f"{guild_id}:{key}",
            guild_id=guild_id,
            platform="discord",
            category=key,
            channel_id=channel_id,
            name=name,
        )
    )
    return str(channel_id)


def _category_exists(guild: Any, category_id: str) -> bool:
    return any(str(c.id) == str(category_id) for c in getattr(guild, "categories", ()))


def _category_by_name(guild: Any, name: str) -> str | None:
    for c in getattr(guild, "categories", ()):
        if c.name == name:
            return str(c.id)
    return None


def _channel_exists(guild: Any, channel_id: str) -> bool:
    for c in getattr(guild, "channels", ()):
        if str(c.id) == str(channel_id):
            return True
    return False


def _ladder_wiring() -> Any | None:
    try:
        from kingdoms.mods.ladder.commands import build_ladder_wiring

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


async def _ladder_footer(guild_id: str) -> str:
    """Visible ids footer: the guild's ladder id and its active season id."""
    wiring = _ladder_wiring()
    if wiring is None:
        return ""
    ladder_id = str(getattr(wiring.bot, "_ladder_id", "") or "")
    if not ladder_id:
        return ""
    footer = ladder_id
    if wiring.season_service is not None:
        try:
            season = await wiring.season_service.get_active_season(ladder_id)
            if season is not None:
                footer = f"{footer} · {season.id}"
        except Exception:
            logger.debug("ladder footer: season id unavailable", exc_info=True)
    return footer


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


async def _ensure_pinned_home(guild: Any, scope: str, channels: _SeasonChannels) -> None:
    """Keep the pinned ladder menu alive in the home salon (self-healing)."""
    from typing import cast

    from kingdoms.core.services.pinned_menu import PinnedMenuChannel, PinnedMenuService
    from kingdoms.mods.ladder.home import build_ladder_menu_layout

    channel = _channel_by_id(guild, channels.home)
    if channel is None:
        return

    class _Delivery:
        async def deliver(self, channel: object, layout: object) -> str:
            """Send the layout into the season salon and return its id."""
            message = await channel.send(view=layout)  # type: ignore[attr-defined]
            return str(message.id)

    async def _build(guild_id: str) -> object:
        enrollments_open, queue_paused = await _ladder_flags(guild_id)
        footer = await _ladder_footer(guild_id)
        return build_ladder_menu_layout(
            enrollments_open=enrollments_open, queue_paused=queue_paused, footer_id=footer
        )

    service = PinnedMenuService(cast("Any", _Delivery()))
    await service.ensure(
        f"{guild.id}:{scope}",
        cast("PinnedMenuChannel", channel),
        marker=LADDER_PIN_MARKER,
        build_layout=_build,
        pin_reason="kingdoms: pinned ladder menu (ladder home salon)",
    )

async def _ensure_pinned_season_admin(guild: Any, scope: str, channels: _SeasonChannels) -> None:
    """Keep the pinned per-season config panel alive in the season admin salon."""
    from kingdoms.discord.mod_admin_channels import ensure_pinned_season_admin_panel
    from kingdoms.mods.ladder.admin_channel import LADDER_ADMIN_CHANNEL_SPEC

    channel = _channel_by_id(guild, channels.season_admin)
    if channel is None:
        return
    await ensure_pinned_season_admin_panel(LADDER_ADMIN_CHANNEL_SPEC, guild, scope, channel)


async def _salon_message(guild: Any, channel: Any, key: str, view: Any, scope: str) -> None:
    """Edit-in-place one salon's message (registry-addressed); recreate when gone."""
    registry = _ladder_registry()
    if channel is None or registry is None:
        return
    entity = f"{guild.id}:{scope}"
    registered = await registry.resolve(_REGISTRY_PLATFORM, key, entity)
    if registered is not None:
        try:
            message = await channel.fetch_message(int(registered.message_id))
            await message.edit(view=view)
            return
        except Exception:
            await registry.forget(_REGISTRY_PLATFORM, key, entity)
    message = await channel.send(view=view)
    await registry.register(
        platform=_REGISTRY_PLATFORM,
        message_key=key,
        entity_id=str(guild.id),
        channel_id=str(channel.id),
        message_id=str(message.id),
        guild_id=f"{guild.id}:{scope}",
    )


async def _sync_dashboard(guild: Any, channels: _SeasonChannels, surface: Any, ladder_id: str, scope: str) -> None:
    """Render the queue salon: the live queue plus the join/leave actions."""
    from kingdoms.mods.ladder.home import LadderJoinButton, LadderLeaveButton

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
        guild, _channel_by_id(guild, channels.dashboard), _SALON_KEYS[DASHBOARD_CHANNEL_NAME], view, scope
    )


async def _sync_leaderboard(guild: Any, channels: _SeasonChannels, surface: Any, ladder_id: str, scope: str) -> None:
    rows = await surface.leaderboard_view(ladder_id, page_size=15)
    if rows:
        lines = [f"**#{r.rank}** {r.display_name} — {r.rating} elo ({r.wins}V/{r.losses}D)" for r in rows]
    else:
        lines = ["_Aucun joueur classé pour l'instant._"]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("## 🏆 Classement\n" + "\n".join(lines))))
    await _salon_message(
        guild, _channel_by_id(guild, channels.leaderboard), _SALON_KEYS[LEADERBOARD_CHANNEL_NAME], view, scope
    )


async def _sync_history(guild: Any, channels: _SeasonChannels, wiring: Any, ladder_id: str, scope: str) -> None:
    from kingdoms.mods.ladder.models import MATCH_STATUS_COMPLETED

    docs = await wiring.service._db.find_ladder_matches(ladder_id, [MATCH_STATUS_COMPLETED])
    lines = [
        f"**{d.get('winner_user_id') or '?'}** bat {d.get('loser_user_id') or '?'}" for d in docs[:10]
    ] or ["_Aucun match joué pour l'instant._"]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("## 📜 Derniers matchs\n" + "\n".join(lines))))
    await _salon_message(
        guild, _channel_by_id(guild, channels.history), _SALON_KEYS[HISTORY_CHANNEL_NAME], view, scope
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
