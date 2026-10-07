"""The map-pools forums: one forum per pool, one post per member map (#04fcb94c).

Every visible pool (owned by this guild or public) gets its own forum
named ``map-pools/<pool>`` inside the Ladder category: one post per
member map carrying a **Remove** button (admin-guarded at click
time). Posts are matched by thread name (the map's), so the sync is
idempotent and self-healing — a deleted post is recreated, a removed
map's post is deleted, a deleted forum is recreated. Nothing here is
ladder-specific: the pools and their visibility live in the core game
catalog, this surface just mirrors them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.games.pools_forum")

POOLS_FORUM_NAME_PREFIX = "map-pools-"
SYNC_INTERVAL_S = 3600


def _build_service() -> Any:
    from kingdoms.core.games.aoe2.seed import MongoAoE2Database
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.game_data import GameDataService

    return GameDataService(MongoAoE2Database(get_async_database()))


def _pool_forum_name(pool_name: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in pool_name).strip("-")
    return f"{POOLS_FORUM_NAME_PREFIX}{slug or 'pool'}"


def _pool_post_view(map_id: str, pool_id: str) -> Any:
    from kingdoms.discord.maps_pool_flow import PoolRemoveMapButton

    view = discord.ui.View(timeout=None)
    view.add_item(PoolRemoveMapButton(map_id, pool_id))
    return view


async def sync_pools_forum(guild_id: str, bot: Any, service: Any = None) -> int:
    """Ensure every visible pool's forum and posts (idempotent)."""
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    service = service if service is not None else _build_service()
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return 0
    actions = 0
    for game_key in await service.list_game_keys():
        for pool in await service.list_map_pools(game_key, guild_id=guild_id):
            actions += await _sync_one_pool(guild, guild_id, platform, service, pool)
    return actions


async def _sync_one_pool(
    guild: discord.Guild,
    guild_id: str,
    platform: Any,
    service: Any,
    pool: Any,
) -> int:
    """Ensure one pool's forum and its member posts (idempotent)."""
    forum_name = _pool_forum_name(pool.name)
    forum = discord.utils.get(guild.forums, name=forum_name)
    if forum is None:
        forum = await guild.create_forum(
            forum_name,
            category=discord.utils.get(guild.categories, name="Ladder"),
            overwrites={
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, create_public_threads=False
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    create_public_threads=True,
                    manage_threads=True,
                    read_message_history=True,
                ),
            },
            reason=f"kingdoms: map pool {pool.name} forum",
        )
        return 1
    actions = 0
    wanted: dict[str, str] = {}
    for map_id in pool.map_ids:
        entry = await service.get_map(map_id)
        if entry is None or entry.archived_at is not None:
            continue
        wanted[entry.name] = map_id
    existing = {t.name: t for t in forum.threads}
    for name, map_id in wanted.items():
        if name in existing:
            continue
        content = f"**{name}** — map du pool **{pool.name}**"
        await platform.create_map_post(
            guild_id, str(forum.id), name, content, view=_pool_post_view(map_id, pool.id)
        )
        actions += 1
    for name, thread in existing.items():
        if name not in wanted:
            await thread.delete(reason=f"kingdoms: {name} left the pool")
            actions += 1
    return actions


def start_pools_forum_sync(bot: Any) -> asyncio.Task[None]:
    """Sync every guild's pool forums periodically, forever, quietly."""

    async def _loop() -> None:
        await asyncio.sleep(15)
        while True:
            for guild in list(bot.guilds):
                try:
                    actions = await sync_pools_forum(str(guild.id), bot)
                    if actions:
                        logger.info("pools forum: %d changes (guild %s)", actions, guild.id)
                except Exception:
                    logger.warning("pools forum sync failed (guild %s) — best-effort", guild.id, exc_info=True)
            await asyncio.sleep(SYNC_INTERVAL_S)

    return asyncio.create_task(_loop())
