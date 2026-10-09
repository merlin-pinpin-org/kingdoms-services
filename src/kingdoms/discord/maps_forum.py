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
LIQUIPEDIA_MAP_URL = "https://liquipedia.net/ageofempires/"


def liquipedia_map_url(name: str) -> str:
    """Build the map's Liquipedia page URL (the post's default source link)."""
    return LIQUIPEDIA_MAP_URL + name.strip().replace(" ", "_")


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
            await _refresh_map_post(bot, platform, guild_id, entry, service)
            continue
        from kingdoms.core.ids import footer
        from kingdoms.discord.content_posts import entity_post_content

        _, summary, source, image = await entity_post_content(entry.id, "", guild_id, bot)
        source = source or liquipedia_map_url(entry.name)
        description = summary or (entry.description or "_Aucune description._")
        pools = await _map_pools_link(service, entry, guild_id, bot=bot)
        view = _map_post_view(
            entry.id,
            entry.name,
            description,
            pools,
            source,
            image,
            entry.resource_url,
            editable=entry.owner_guild_id == guild_id,
            map_type=getattr(entry, "map_type", ""),
            filenames=tuple(getattr(entry, "filenames", ()) or ()),
        )
        tail = f"Source : {source}\n" if source else ""
        content = f"{tail}**{entry.name}**\n{description}"
        if pools:
            content = f"{content}\n\n**Pools**\n" + "\n".join(pools)
        if entry.resource_url:
            content = f"{content}\n{entry.resource_url}"
        content = f"{content}\n\n{footer(entry.id)}"
        thread_id = await platform.create_map_post(guild_id, forum_id, entry.name, content, view=view)
        await service.set_map_forum_message(entry.id, thread_id)
        created += 1
    return created


async def _map_pools_link(service: Any, entry: Any, guild_id: str, bot: Any = None) -> list[str]:
    """Build the map's pool links: one line per pool containing the map.

    Pool posts are matched by thread name (the pools sync's own
    convention); a pool without a live post still lists as plain text,
    so the map post never links into a dead thread.
    """
    try:
        pools = await service.list_map_pools(entry.game_key, guild_id=guild_id)
    except Exception:
        return []
    thread_ids: dict[str, str] = {}
    guild = bot.get_guild(int(guild_id)) if bot is not None and guild_id.isdigit() else None
    if guild is not None:
        forum = discord.utils.get(guild.forums, name=f"{entry.game_key}-map-pools")
        if forum is not None:
            thread_ids = {thread.name: str(thread.id) for thread in forum.threads}
    lines = []
    for pool in pools:
        if entry.id not in pool.map_ids:
            continue
        thread_id = thread_ids.get(pool.name)
        if thread_id:
            lines.append(f"- [{pool.name}](https://discord.com/channels/{guild_id}/{thread_id}/{thread_id})")
        else:
            lines.append(f"- {pool.name}")
    return lines


def _map_post_view(
    map_id: str,
    name: str,
    description: str,
    pools: list[str],
    source: str,
    image: str,
    resource_url: str | None,
    editable: bool = False,
    map_type: str = "",
    filenames: tuple[str, ...] = (),
) -> discord.ui.LayoutView:
    """Build the map post's layout: type, description, files, pools, image.

    The order is designer-chosen and unordered by nature: pools' links,
    description, associated filenames, map type, image, the id footer,
    then the add-to-pool entry point (admins, pools in edition).
    ``editable`` marks a guild-owned map: its post carries the edit
    entry point too.
    """
    from kingdoms.core.ids import footer
    from kingdoms.discord.maps_pool_flow import MapAddToPoolButton, MapEditButton

    view = discord.ui.LayoutView(timeout=None)
    blocks: list[discord.ui.Item[discord.ui.LayoutView]] = [discord.ui.TextDisplay(f"## {name}")]
    if map_type:
        blocks.append(discord.ui.TextDisplay(f"**Type** : {map_type}"))
    if description:
        blocks.append(discord.ui.TextDisplay(description))
    if filenames:
        blocks.append(discord.ui.TextDisplay("**Fichiers**\n" + "\n".join(f"`{f}`" for f in filenames)))
    if pools:
        blocks.append(discord.ui.TextDisplay("**Map pools**\n" + "\n".join(pools)))
    media = resource_url or image
    if media:
        blocks.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media)))
    blocks.append(discord.ui.TextDisplay(f"Source : {source}\n{footer(map_id)}" if source else footer(map_id)))
    view.add_item(
        discord.ui.Section(
            *blocks,
            accessory=MapAddToPoolButton(map_id),
        )
    )
    if editable:
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(MapEditButton(map_id))
        view.add_item(row)
    return view


async def _refresh_map_post(bot: Any, platform: Any, guild_id: str, entry: Any, service: Any) -> None:
    """Keep an existing map post's content and layout in sync (best-effort)."""
    import time

    from kingdoms.core.ids import footer
    from kingdoms.core.services.entity_forum import (
        _content_fingerprint,
        _last_layout_render,
        _note_stale_edit,
        _remember_layout_render,
        _stagger_stale_edit,
    )
    from kingdoms.discord.content_posts import entity_post_content

    thread_id = str(entry.forum_message_id)
    if _stagger_stale_edit(thread_id, time.monotonic()):
        return
    try:
        _, summary, source, image = await entity_post_content(entry.id, "", guild_id, bot)
        source = source or liquipedia_map_url(entry.name)
        description = summary or (entry.description or "_Aucune description._")
        pools = await _map_pools_link(service, entry, guild_id, bot=bot)
        view = _map_post_view(
            entry.id,
            entry.name,
            description,
            pools,
            source,
            image,
            entry.resource_url,
            editable=entry.owner_guild_id == guild_id,
            map_type=getattr(entry, "map_type", ""),
            filenames=tuple(getattr(entry, "filenames", ()) or ()),
        )
        tail = f"Source : {source}\n" if source else ""
        content = f"{tail}**{entry.name}**\n{description}"
        if pools:
            content = f"{content}\n\n**Pools**\n" + "\n".join(pools)
        if entry.resource_url:
            content = f"{content}\n{entry.resource_url}"
        content = f"{content}\n\n{footer(entry.id)}"
        fingerprint = _content_fingerprint(content)
        if _last_layout_render(thread_id) == fingerprint:
            return
        _remember_layout_render(thread_id, fingerprint)
        await platform.edit_forum_post(guild_id, entry.forum_message_id, content, view)
    except discord.HTTPException as exc:
        if getattr(exc, "code", None) == 30046:
            _note_stale_edit(thread_id, time.monotonic())
            return
        logger.debug("maps forum: post refresh skipped (thread %s)", thread_id, exc_info=True)
    except Exception:
        logger.debug("maps forum: post refresh skipped (thread %s)", thread_id, exc_info=True)


def start_maps_forum_sync(bot: Any) -> asyncio.Task[None]:
    """Sync every game's maps forum periodically, forever, quietly."""

    async def _loop() -> None:
        await asyncio.sleep(10)
        while True:
            for guild in list(bot.guilds):
                try:
                    from kingdoms.discord.wiring import guild_has_game

                    service = _build_service()
                    for game_key in await service.list_game_keys():
                        if not await guild_has_game(str(guild.id), game_key):
                            continue
                        created = await sync_maps_forum(str(guild.id), game_key, bot, service)
                        if created:
                            logger.info("maps forum: %d posts created (guild %s, game %s)", created, guild.id, game_key)
                except Exception:
                    logger.warning("maps forum sync failed (guild %s) — best-effort", guild.id, exc_info=True)
            await asyncio.sleep(SYNC_INTERVAL_S)

    return asyncio.create_task(_loop())
