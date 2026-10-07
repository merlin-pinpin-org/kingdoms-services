"""The map-pools forum: one post per pool, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-map-pools`` forum (e.g. ``aoe2-map-pools``). Every visible
pool (owned by this guild or public) gets exactly one post carrying
one **Remove** button per member map (admin-guarded at click time).
Posts are matched by thread name (the pool's), so the sync is
idempotent and self-healing — a deleted post is recreated, a
disbanded pool's post is deleted, a deleted forum is recreated.
Legacy per-pool forums (``map-pools-<slug>``, one forum per pool)
are deleted on sight. Nothing here is ladder-specific: pools are a
core game-catalog concept; this surface just mirrors them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.games.pools_forum")

GAMES_CATEGORY_NAME = "games"
POOLS_FORUM_SUFFIX = "-map-pools"
LEGACY_PREFIX = "map-pools-"
SYNC_INTERVAL_S = 3600


def _build_service() -> Any:
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


def _pools_forum_name(game_key: str) -> str:
    return f"{game_key}{POOLS_FORUM_SUFFIX}"


def _pool_post_view(pool: Any, map_names: dict[str, str]) -> Any:
    from kingdoms.discord.maps_pool_flow import PoolRemoveMapButton

    view = discord.ui.View(timeout=None)
    for map_id, map_name in map_names.items():
        view.add_item(PoolRemoveMapButton(map_id, pool.id, label=map_name))
    return view


async def sync_pools_forum(guild_id: str, bot: Any, service: Any = None) -> int:
    """Ensure every game's pools forum and one post per pool (idempotent)."""
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    service = service if service is not None else _build_service()
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return 0
    actions = await _purge_legacy_forums(guild)
    for game_key in await service.list_game_keys():
        actions += await _sync_game_pools(guild, guild_id, platform, service, game_key)
    return actions


async def _purge_legacy_forums(guild: discord.Guild) -> int:
    deleted = 0
    for forum in list(guild.forums):
        if forum.name.startswith(LEGACY_PREFIX):
            await forum.delete(reason="kingdoms: legacy per-pool forum superseded by <game>-map-pools")
            deleted += 1
    return deleted


async def _sync_game_pools(
    guild: discord.Guild,
    guild_id: str,
    platform: Any,
    service: Any,
    game_key: str,
) -> int:
    from kingdoms.discord.wiring import guild_category

    forum_name = _pools_forum_name(game_key)
    forum = discord.utils.get(guild.forums, name=forum_name)
    if forum is None:
        forum = await guild.create_forum(
            forum_name,
            category=await guild_category(guild, GAMES_CATEGORY_NAME),
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
            reason=f"kingdoms: {game_key} map pools forum",
        )
    pools = await service.list_map_pools(game_key, guild_id=guild_id)
    wanted: dict[str, Any] = {pool.name: pool for pool in pools if pool.archived_at is None}
    existing = {t.name: t for t in forum.threads}
    actions = 0
    for name, pool in wanted.items():
        if name not in existing:
            await _create_pool_post(platform, guild_id, forum, service, pool)
            actions += 1
        else:
            await _refresh_pool_post(platform, guild_id, existing[name], service, pool)
    for name, thread in existing.items():
        if name not in wanted:
            await thread.delete(reason=f"kingdoms: pool {name} no longer visible")
            actions += 1
    return actions


async def _pool_content(service: Any, pool: Any) -> tuple[str, dict[str, str]]:
    map_names: dict[str, str] = {}
    lines: list[str] = []
    for map_id in pool.map_ids:
        entry = await service.get_map(map_id)
        if entry is None or entry.archived_at is not None:
            continue
        map_names[map_id] = entry.name
        lines.append(f"- **{entry.name}**")
    content = f"**{pool.name}**\n{pool.description or '_Aucune description._'}\n\nMaps :\n"
    content += "\n".join(lines) if lines else "_Aucune map._"
    return content, map_names


async def _create_pool_post(platform: Any, guild_id: str, forum: Any, service: Any, pool: Any) -> None:
    content, map_names = await _pool_content(service, pool)
    await platform.create_map_post(
        guild_id, str(forum.id), pool.name, content, view=_pool_post_view(pool, map_names)
    )


async def _refresh_pool_post(platform: Any, guild_id: str, thread: Any, service: Any, pool: Any) -> None:
    """Keep an existing pool post's content and buttons in sync (best-effort)."""
    from kingdoms.discord.maps_pool_flow import _REMOVE_NS

    content, map_names = await _pool_content(service, pool)
    try:
        await thread.edit(content=content)
        await platform.ensure_forum_post_view(
            guild_id, str(thread.id), _pool_post_view(pool, map_names), _REMOVE_NS
        )
    except Exception:
        logger.debug("pools forum: post refresh skipped (thread %s)", thread.id, exc_info=True)


def start_pools_forum_sync(bot: Any) -> asyncio.Task[None]:
    """Sync every guild's pools forums periodically, forever, quietly."""

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
