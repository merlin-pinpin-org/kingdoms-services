"""The games forums: one read-only map post per active map, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-maps`` forum (e.g. ``aoe2-maps``). Every non-archived map
must have exactly one post in its game's forum — the post is the
map's public surface and the pool builder's picker source. The map
document stores the post's ``forum_message_id`` (the map-message
link); the sync is idempotent and runs periodically (self-healing):
a deleted post is recreated, a new map gets its post, a new game
gets its forum. Nothing is hardcoded per game: the loop lists the
game keys from the catalog, so seeding cs2 maps creates and
maintains the cs2 forum without any code change.

Nothing here is ladder-specific: the wiring is generic, and the
admin surface lives in the games admin section.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import discord

logger = logging.getLogger("kingdoms.games.maps_forum")

GAMES_CATEGORY_NAME = "games"
FORUM_SUFFIX = "-maps"
SYNC_INTERVAL_S = 3600


def maps_forum_wiring_ready() -> bool:
    """Whether the maps forum wiring can be built (Mongo configured)."""
    return bool(os.environ.get("MONGO_URI"))


def _forum_name(game_key: str) -> str:
    """Build the per-game forum name (``aoe2`` -> ``aoe2-maps``)."""
    return f"{game_key}{FORUM_SUFFIX}"


def _build_service() -> Any:
    """Build the GameDataService over the shared Mongo adapter."""
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


async def sync_maps_forum(guild_id: str, game_key: str, bot: Any, service: Any = None) -> int:
    """Ensure the game's forum and one post per active map (idempotent).

    Existing posts are kept (matched by the stored ``forum_message_id``),
    missing posts are created, dead links are recreated. Returns the
    number of posts created.
    """
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    service = service if service is not None else _build_service()
    category_id = await platform.ensure_category(guild_id, GAMES_CATEGORY_NAME)
    forum_name = _forum_name(game_key)
    await platform.adopt_legacy_forum(guild_id, "maps", forum_name, category_id)
    forum_id = await platform.ensure_forum(guild_id, forum_name, category_id)
    created = 0
    maps = await service.list_maps(game_key)
    for entry in maps:
        if entry.archived_at is not None:
            continue
        if entry.forum_message_id and await platform.forum_thread_exists(guild_id, entry.forum_message_id):
            await _ensure_add_button(platform, guild_id, entry.forum_message_id, entry.id)
            continue
        content = f"**{entry.name}**\n{entry.description or '_Aucune description._'}"
        if entry.resource_url:
            content = f"{content}\n{entry.resource_url}"
        thread_id = await platform.create_map_post(
            guild_id, forum_id, entry.name, content, view=_map_post_view(entry.id)
        )
        await service.set_map_forum_message(entry.id, thread_id)
        created += 1
    return created


def _map_post_view(map_id: str) -> Any:
    """Build the map post's view: the pool-flow entry point (admins)."""
    from kingdoms.discord.maps_pool_flow import MapAddToPoolButton

    view = discord.ui.View(timeout=None)
    view.add_item(MapAddToPoolButton(map_id))
    return view


async def _ensure_add_button(platform: Any, guild_id: str, thread_id: str, map_id: str) -> None:
    """Attach the add-to-pool button to pre-flow posts (best-effort)."""
    from kingdoms.discord.maps_pool_flow import _ADD_NS

    try:
        await platform.ensure_forum_post_view(guild_id, thread_id, _map_post_view(map_id), _ADD_NS)
    except Exception:
        logger.debug("maps forum: add-button migration skipped (thread %s)", thread_id, exc_info=True)


def start_maps_forum_sync(bot: Any) -> asyncio.Task[None]:
    """Sync every game's maps forum periodically, forever, quietly."""

    async def _loop() -> None:
        await asyncio.sleep(10)
        while True:
            for guild in list(bot.guilds):
                try:
                    service = _build_service()
                    for game_key in await service.list_game_keys():
                        created = await sync_maps_forum(str(guild.id), game_key, bot, service)
                        if created:
                            logger.info(
                                "maps forum: %d posts created (guild %s, game %s)", created, guild.id, game_key
                            )
                except Exception:
                    logger.warning("maps forum sync failed (guild %s) — best-effort", guild.id, exc_info=True)
            await asyncio.sleep(SYNC_INTERVAL_S)

    return asyncio.create_task(_loop())
