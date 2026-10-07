"""The games forum: one read-only map post per active map (core, game-agnostic).

The guild hosts a `games` category with one forum per game (e.g. ``maps``
for aoe2). Every non-archived map in the catalog must have exactly one
post in that forum — the post is the map's public surface and the pool
builder's picker source. The map document stores the post's
``forum_message_id`` (the map-message link); the sync is idempotent and
runs periodically (self-healing): a deleted post is recreated, a new
map gets its post, an archived map keeps its post untouched.

Nothing here is ladder-specific: the wiring is per game_key, and the
admin surface lives in the games admin section.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("kingdoms.games.maps_forum")

GAMES_CATEGORY_NAME = "games"
FORUM_NAME = "maps"
SYNC_INTERVAL_S = 3600


def maps_forum_wiring_ready() -> bool:
    """Whether the maps forum wiring can be built (Mongo configured)."""
    return bool(os.environ.get("MONGO_URI"))


async def sync_maps_forum(guild_id: str, game_key: str, bot: Any) -> int:
    """Ensure the category, the forum and one post per active map.

    Idempotent: existing posts are kept (matched by the stored
    ``forum_message_id``), missing posts are created, dead links are
    recreated. Returns the number of posts created.
    """
    from kingdoms.core.games.aoe2.seed import MongoAoE2Database
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.game_data import GameDataService
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    service = GameDataService(MongoAoE2Database(get_async_database()))
    category_id = await platform.ensure_category(guild_id, GAMES_CATEGORY_NAME)
    forum_id = await platform.ensure_forum(guild_id, FORUM_NAME, category_id)
    created = 0
    maps = await service.list_maps(game_key)
    for entry in maps:
        if entry.archived_at is not None:
            continue
        if entry.forum_message_id and await platform.forum_thread_exists(guild_id, entry.forum_message_id):
            continue
        content = f"**{entry.name}**\n{entry.description or '_Aucune description._'}"
        if entry.resource_url:
            content = f"{content}\n{entry.resource_url}"
        thread_id = await platform.create_map_post(guild_id, forum_id, entry.name, content)
        await service.set_map_forum_message(entry.id, thread_id)
        created += 1
    return created


def start_maps_forum_sync(bot: Any) -> asyncio.Task[None]:
    """Run the maps forum sync periodically, forever, quietly."""

    async def _loop() -> None:
        await asyncio.sleep(60)
        while True:
            for guild in list(bot.guilds):
                try:
                    created = await sync_maps_forum(str(guild.id), "aoe2", bot)
                    if created:
                        logger.info("maps forum: %d posts created (guild %s)", created, guild.id)
                except Exception:
                    logger.warning("maps forum sync failed (guild %s) — best-effort", guild.id, exc_info=True)
            await asyncio.sleep(SYNC_INTERVAL_S)

    return asyncio.create_task(_loop())
