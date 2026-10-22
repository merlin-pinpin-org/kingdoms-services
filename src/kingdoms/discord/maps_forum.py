"""The games forums: one read-only map post per active map, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-maps`` forum (e.g. ``aoe2-maps``). Every non-archived map
must have exactly one post in its game's forum — the post is the
map's public surface and the pool builder's picker source. The map
document stores the post's ``forum_message_id`` (the map-message
link); the sync is idempotent and on demand (the sync button):
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

LIQUIPEDIA_MAP_URL = "https://liquipedia.net/ageofempires/"


def liquipedia_map_url(name: str) -> str:
    """Build the map's Liquipedia page URL (the post's default source link)."""
    return LIQUIPEDIA_MAP_URL + name.strip().replace(" ", "_")




def maps_forum_wiring_ready() -> bool:
    """Whether the maps forum wiring can be built (Mongo configured)."""
    return bool(os.environ.get("MONGO_URI"))


def _build_service() -> Any:
    """Build the GameDataService over the shared Mongo adapter."""
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


async def _forum_alive(bot: Any, guild_id: str, forum_id: str | None) -> bool:
    """Whether the stored forum id still resolves to a live forum."""
    if not forum_id or not forum_id.isdigit():
        return False
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    channel = guild.get_channel(int(forum_id)) if guild is not None else None
    return channel is not None and getattr(channel, "type", None) == discord.ChannelType.forum


_SYNC_LOCKS: dict[str, asyncio.Lock] = {}


def _sync_lock(guild_id: str) -> asyncio.Lock:
    """One forum-sync at a time per guild (concurrent clicks)."""
    return _SYNC_LOCKS.setdefault(guild_id, asyncio.Lock())


async def sync_maps_forum(guild_id: str, game_key: str, bot: Any, service: Any = None) -> int:
    """Ensure the game's forum and one post per active map (idempotent).

    Existing posts are kept (matched by the stored ``forum_message_id``),
    missing posts are created, dead links are recreated. Returns the
    number of posts created. The whole pass holds the guild's sync
    lock: two concurrent syncs never create the same
    post twice.
    """
    async with _sync_lock(guild_id):
        return await _sync_maps_forum_locked(guild_id, game_key, bot, service)


async def _sync_maps_forum_locked(guild_id: str, game_key: str, bot: Any, service: Any = None) -> int:
    """Run the sync body (the caller holds the guild's sync lock)."""
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    platform = DiscordChannelsPlatform(bot)
    service = service if service is not None else _build_service()
    stored_forum_id = await stored_channel_id(guild_id, f"games/{game_key}", "maps")
    if not await _forum_alive(bot, guild_id, stored_forum_id):
        return 0  # the provisioning owns creation; nothing stored yet
    forum_id = str(stored_forum_id)
    created = 0
    maps = await service.list_maps(game_key)
    for entry in maps:
        if entry.archived_at is not None:
            continue
        if entry.forum_message_id and await platform.forum_thread_exists(guild_id, entry.forum_message_id):
            await _refresh_map_post(bot, platform, guild_id, entry, service)
            continue
        from kingdoms.core.ids import footer

        _, summary, source, image, icon, walls, nomad, tc = await _map_content_full(
            entry.id, guild_id, bot
        )
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
            filenames=(),
            game_key=str(getattr(entry, "game_key", "")),
            owned_by_guild=entry.owner_guild_id == guild_id,
            icon_url=icon,
            walls=walls,
            nomad=nomad,
            tc=tc,
        )
        display = f"{entry.name[:1].upper()}{entry.name[1:]}"
        tail = f"{source}\n" if source else ""
        content = f"## {display}\n{tail}{description}"
        if pools:
            content = f"{content}\n\n**Pools**\n" + "\n".join(pools)
        if entry.resource_url:
            content = f"{content}\n{entry.resource_url}"
        content = f"{content}\n\n{footer(entry.id)}"
        post_name = f"{entry.name[:1].upper()}{entry.name[1:]}" if entry.name else entry.name
        thread_id = await platform.create_map_post(guild_id, forum_id, post_name, content, view=view)
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
    lines = []
    for pool in pools:
        if entry.id not in pool.map_ids:
            continue
        # the pool's stored thread link is the source of truth: the
        # thread titles carry the state emoji now (a name match missed)
        thread_id = str(getattr(pool, "forum_thread_id", "") or "")
        if thread_id:
            lines.append(
                f"- [{pool.name}](https://discord.com/channels/{guild_id}/{thread_id}/{thread_id})"
            )
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
    game_key: str = "",
    owned_by_guild: bool = False,
    icon_url: str = "",
    walls: str = "",
    nomad: str = "",
    tc: str = "",
) -> discord.ui.LayoutView:
    """Build the map post on the shared entity-card template.

    Header: capitalized title with the in-game icon (Gallery) as the
    thumbnail; then the properties (one setting per line), the
    description, the main image, the pool links, the source link and
    the id footer — the game-agnostic contract from
    ``kingdoms.discord.ui.entity_cards``.
    """
    from kingdoms.discord.entity_admin_menu import EntityAdminButton
    from kingdoms.discord.ui.entity_cards import map_card

    def _admin() -> Any:
        return EntityAdminButton("map", map_id, game_key=game_key, owned=owned_by_guild)

    buttons: list[Any] = [_admin]
    return map_card(
        map_id=map_id,
        name=name,
        props=tuple(_map_props_lines(map_type, walls, nomad, tc)),
        description=description,
        image_url=image,
        icon_url=icon_url,
        source_url=source,
        pool_links=tuple(pools),
        buttons=tuple(buttons),
    )



async def _heal_thread_title(platform: Any, guild_id: str, thread_id: str, display: str) -> None:
    """Align a post's thread title with its display name (best-effort).

    A post created before the capitalize rule (or renamed by hand)
    heals on every sync — the title is part of the post's face.
    """
    if not guild_id.isdigit() or not thread_id.isdigit():
        return
    guild = platform._bot.get_guild(int(guild_id))
    if guild is None:
        return
    channel = guild.get_channel_or_thread(int(thread_id))
    if channel is None or channel.name == display[:100]:
        return
    try:
        await channel.edit(name=display[:100], reason="kingdoms: post title heal")
    except Exception:
        logger.debug("maps forum: title heal skipped (%s)", thread_id, exc_info=True)


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

    thread_id = str(entry.forum_message_id)
    if _stagger_stale_edit(thread_id, time.monotonic()):
        return
    try:
        _, summary, source, image, icon, walls, nomad, tc = await _map_content_full(
            entry.id, guild_id, bot
        )
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
            filenames=(),
            game_key=str(getattr(entry, "game_key", "")),
            owned_by_guild=entry.owner_guild_id == guild_id,
            icon_url=icon,
            walls=walls,
            nomad=nomad,
            tc=tc,
        )
        display = f"{entry.name[:1].upper()}{entry.name[1:]}"
        tail = f"{source}\n" if source else ""
        content = f"## {display}\n{tail}{description}"
        if pools:
            content = f"{content}\n\n**Pools**\n" + "\n".join(pools)
        if entry.resource_url:
            content = f"{content}\n{entry.resource_url}"
        content = f"{content}\n\n{footer(entry.id)}"
        fingerprint = _content_fingerprint(content)
        if _last_layout_render(thread_id) == fingerprint:
            return
        _remember_layout_render(thread_id, fingerprint)
        await _heal_thread_title(platform, guild_id, thread_id, display)
        await platform.edit_forum_post(guild_id, entry.forum_message_id, content, view)
    except discord.HTTPException as exc:
        if getattr(exc, "code", None) == 30046:
            _note_stale_edit(thread_id, time.monotonic())
            return
        logger.debug("maps forum: post refresh skipped (thread %s)", thread_id, exc_info=True)
    except Exception:
        logger.debug("maps forum: post refresh skipped (thread %s)", thread_id, exc_info=True)


def register_maps_forum_sync() -> None:
    """Declare the maps forums' mirror sync (the sync button runs it)."""
    from kingdoms.discord.scope_surfaces import register_scope_sync

    async def _sync(guild_id: str, bot: Any) -> int:
        actions = 0
        service = _build_service()
        from kingdoms.discord.wiring import granted_game_keys, guild_has_game

        for game_key in await granted_game_keys(guild_id, tuple(await service.list_game_keys())):
            if not await guild_has_game(guild_id, game_key):
                continue
            actions += await sync_maps_forum(guild_id, game_key, bot, service)
        return actions

    register_scope_sync("games", _sync)


async def _map_content_full(
    entity_id: str, guild_id: str, bot: Any
) -> tuple[str, str, str, str, str, str, str, str]:
    """Resolve a map's full stored content for its post.

    ``(name, summary, source, image, icon, walls, nomad, tc)`` — the
    traits (walls/nomad/tc) live as separate keys next to the summary
    since the label-line layout split them out of the summary blob.
    """
    from kingdoms.discord.content_posts import content_service, guild_locale

    service = content_service()
    locale = await guild_locale(guild_id, bot)
    doc = None
    if service is not None:
        for candidate in (locale, "en"):
            try:
                doc = await service.get(entity_id, candidate)
            except Exception:
                doc = None
            if doc is not None:
                break
    if doc is None:
        return "", "", "", "", "", "", "", ""
    if doc.get("split_keys") or (doc.get("icon_url") and doc.get("walls") is not None):
        image_url = str(doc.get("image_url") or "")
        # a page without a Gallery has no in-game icon — the main image
        # becomes the thumbnail so every card keeps the same shape
        return (
            str(doc.get("name") or ""),
            str(doc.get("summary") or ""),
            str(doc.get("source_url") or ""),
            image_url,
            str(doc.get("icon_url") or "") or image_url,
            str(doc.get("walls") or ""),
            str(doc.get("nomad") or ""),
            str(doc.get("tc") or ""),
        )
    # Stale doc (pre-split import: no icon_url / trait keys, the summary
    # carries the old inline blob) — re-resolve from Liquipedia once and
    # re-store the new keys; the next pass takes the fast path.
    migrated = await _migrate_stale_map_doc(entity_id, doc, service, locale)
    if migrated is not None:
        doc = migrated
    return (
        str(doc.get("name") or ""),
        str(doc.get("summary") or ""),
        str(doc.get("source_url") or ""),
        str(doc.get("image_url") or ""),
        str(doc.get("icon_url") or ""),
        str(doc.get("walls") or ""),
        str(doc.get("nomad") or ""),
        str(doc.get("tc") or ""),
    )


async def _migrate_stale_map_doc(
    entity_id: str, doc: dict[str, Any], service: Any, locale: str
) -> dict[str, Any] | None:
    """Re-resolve a stale Liquipedia map doc and store the new keys."""
    try:
        from kingdoms.mapsdata.seed import LiquipediaUnconfiguredError, fetch_map_seed

        seed = await fetch_map_seed(str(doc.get("name") or entity_id.rsplit(":", 1)[-1]))
    except LiquipediaUnconfiguredError:
        return None
    except Exception:
        logger.warning("stale map doc migration failed (%s) — best-effort", entity_id, exc_info=True)
        return None
    if seed is None:
        return None
    updated = dict(doc)
    updated.update(
        {
            "summary": seed.description,
            "icon_url": seed.icon_url,
            "walls": seed.walls,
            "nomad": seed.nomad,
            "tc": seed.tc,
            "image_url": seed.image_url or doc.get("image_url") or "",
            # the one-shot marker: a map without an icon would otherwise
            # never satisfy the freshness check and re-fetch forever
            "split_keys": True,
        }
    )
    try:
        await service.store(updated)
    except Exception:
        logger.warning("stale map doc re-store failed (%s) — best-effort", entity_id, exc_info=True)
        return updated
    logger.info("map doc migrated to the split keys (%s)", entity_id)
    return updated

def _map_props_lines(map_type: str, walls: str, nomad: str, tc: str) -> list[str]:
    """Render the map's properties as label lines (Type/Walls/Nomad/TC)."""
    props = [f"**Type** : {map_type}"] if map_type else []
    if walls:
        props.append(f"**Walls** : {walls}")
    if nomad:
        shown = "Oui" if nomad.strip().lower() in ("yes", "true") else nomad
        props.append(f"**Nomad** : {shown}")
    if tc and tc.isdigit() and int(tc) > 1:
        props.append(f"**TC** : {tc}")
    return props
