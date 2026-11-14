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
import re
from dataclasses import dataclass
from typing import Any

import discord

logger = logging.getLogger("botforge.games.maps_forum")

LIQUIPEDIA_MAP_URL = "https://liquipedia.net/ageofempires/"


def liquipedia_map_url(name: str) -> str:
    """Build the map's Liquipedia page URL (the post's default source link)."""
    return LIQUIPEDIA_MAP_URL + name.strip().replace(" ", "_")


def maps_forum_wiring_ready() -> bool:
    """Whether the maps forum wiring can be built (the game service configured)."""
    return bool(os.environ.get("GAME_AOE2_URI"))


def _build_service() -> Any:
    """Build the catalog service over the GAME SERVICE (rule 5)."""
    from botforge.discord.wiring import build_games_service

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
    from botforge.discord.channels_platform import DiscordChannelsPlatform
    from botforge.discord.ui.panels_provisioning import stored_channel_id

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
        # one map failing never freezes the forum (the 2026-11-10
        # incident: the first oversized card raised 50035 and every
        # map AFTER it stayed postless) — the pass logs and moves on
        try:
            created += await _sync_one_map(bot, platform, guild_id, forum_id, entry, service)
        except Exception:
            logger.warning(
                "maps forum: post sync failed for %s (%s) — best-effort, the pass continues",
                entry.id,
                entry.name,
                exc_info=True,
            )
    return created


async def _sync_one_map(
    bot: Any,
    platform: Any,
    guild_id: str,
    forum_id: str,
    entry: Any,
    service: Any,
) -> int:
    """Sync one map's post (create or refresh); 1 when created."""
    from botforge.core.ids import footer

    if entry.forum_message_id and await platform.forum_thread_exists(guild_id, entry.forum_message_id):
        await _refresh_map_post(bot, platform, guild_id, entry, service)
        return 0
    stored = await _map_content_full(entry.id, guild_id, bot)
    summary, source, image, icon = stored.summary, stored.source, stored.image, stored.icon
    walls, nomad, tc, capacity, creator = (
        stored.walls, stored.nomad, stored.tc, stored.capacity, stored.creator
    )
    gallery = stored.gallery
    source = source or liquipedia_map_url(entry.name)
    description = _description_display(summary) if summary else (entry.description or "_Aucune description._")
    pools = await _map_pools_link(service, entry, guild_id, bot=bot)
    from botforge.discord.content_posts import guild_locale

    locale = await guild_locale(guild_id, bot)
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
        player_capacity=capacity,
        creator=creator,
        gallery_images=gallery,
        creator_url=stored.creator_url,
        versions=stored.versions,
        locale=locale,
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
    return 1


async def _map_pools_link(service: Any, entry: Any, guild_id: str, bot: Any = None) -> list[str]:
    """Build the map's pool links: one line per pool containing the map.

    Every pool lists, archived included \u2014 an archived pool is
    immutable but stays visible (its id is reserved until a hard
    delete). Pool posts are matched by thread id; a pool without a
    live post still lists as plain text, so the map post never links
    into a dead thread.
    """
    try:
        pools = await service.list_all_map_pools(entry.game_key, guild_id=guild_id)
    except Exception:
        return []
    lines = []
    for pool in pools:
        if entry.id not in pool.map_ids:
            continue
        # a LINK to the pool's post (no mention, no frozen label): the
        # URL renders clickable; the pool's title lives on its own post
        thread_id = str(getattr(pool, "forum_thread_id", "") or "")
        if thread_id:
            lines.append(
                f"- https://discord.com/channels/{guild_id}/{thread_id}/{thread_id}"
            )
        else:
            lines.append(f"- {pool.name}")
    return lines


async def refresh_map_posts(bot: Any, guild_id: str, map_ids: tuple[str, ...] | list[str]) -> None:
    """Re-edit the given maps' forum posts (best-effort).

    A pool composition change rewrites the pool-link lines on every
    member map's post — add and remove both call this seam with the
    maps they touched (the pool's own post follows the pools sync).
    """
    from botforge.discord.channels_platform import DiscordChannelsPlatform

    service = _build_service()
    platform = DiscordChannelsPlatform(bot)
    for map_id in map_ids:
        try:
            entry = await service.get_map(map_id)
            if entry is None or not getattr(entry, "forum_message_id", ""):
                continue
            await _refresh_map_post(bot, platform, guild_id, entry, service)
        except Exception:
            logger.warning("map post refresh failed (%s) — best-effort", map_id, exc_info=True)


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
    player_capacity: str = "",
    creator: str = "",
    admin_buttons: tuple[Any, ...] = (),
    gallery_images: tuple[str, ...] = (),
    creator_url: str = "",
    versions: tuple[dict[str, str], ...] = (),
    locale: str = "en",
) -> discord.ui.LayoutView:
    """Build the map post on the shared entity-card template.

    Header: capitalized title with the in-game icon (Gallery) as the
    thumbnail; then the properties (one setting per line), the
    description, the main image, the pool links, the source link and
    the id footer — the game-agnostic contract from
    ``botforge.discord.ui.entity_cards``.
    """
    from botforge.discord.entity_admin_menu import EntityAdminButton
    from botforge.discord.ui.entity_cards import map_card

    def _admin() -> Any:
        return EntityAdminButton("map", map_id, game_key=game_key, owned=owned_by_guild)

    # the admin menu is THE SAME card — only the action row swaps
    buttons: list[Any] = list(admin_buttons) if admin_buttons else [_admin]
    return map_card(
        map_id=map_id,
        name=name,
        props=tuple(_map_props_lines(map_type, walls, nomad, tc, player_capacity)),
        creator=creator,
        description=description,
        image_url=image,
        icon_url=icon_url,
        gallery_images=gallery_images,
        source_url=source,
        pool_links=tuple(pools),
        buttons=tuple(buttons),
        creator_url=creator_url,
        versions=versions,
        locale=locale,
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
        await channel.edit(name=display[:100], reason="botforge: post title heal")
    except Exception:
        logger.debug("maps forum: title heal skipped (%s)", thread_id, exc_info=True)


async def map_post_render(
    entry: Any,
    guild_id: str,
    bot: Any,
    service: Any,
    admin_buttons: tuple[Any, ...] = (),
) -> tuple[str, Any]:
    """Render one map's post (content + view) — THE forum post's shape.

    One builder, three consumers: the forum post (the sync), the
    admin's ephemeral preview (the creation and the already-exists
    verdicts show the admin EXACTLY what the post looks like), and the
    map's admin menu (``admin_buttons`` swaps the action row — the
    menu IS the card, like the pools).
    """
    from botforge.core.ids import footer

    stored = await _map_content_full(entry.id, guild_id, bot)
    summary, source, image, icon = stored.summary, stored.source, stored.image, stored.icon
    walls, nomad, tc, capacity, creator = (
        stored.walls, stored.nomad, stored.tc, stored.capacity, stored.creator
    )
    gallery = stored.gallery
    source = source or liquipedia_map_url(entry.name)
    description = _description_display(summary) if summary else (entry.description or "_Aucune description._")
    pools = await _map_pools_link(service, entry, guild_id, bot=bot)
    from botforge.discord.content_posts import guild_locale

    locale = await guild_locale(guild_id, bot)
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
        player_capacity=capacity,
        creator=creator,
        admin_buttons=admin_buttons,
        gallery_images=gallery,
        creator_url=stored.creator_url,
        versions=stored.versions,
        locale=locale,
    )
    display = f"{entry.name[:1].upper()}{entry.name[1:]}"
    tail = f"{source}\n" if source else ""
    content = f"## {display}\n{tail}{description}"
    if pools:
        content = f"{content}\n\n**Pools**\n" + "\n".join(pools)
    if entry.resource_url:
        content = f"{content}\n{entry.resource_url}"
    content = f"{content}\n\n{footer(entry.id)}"
    return content, view


async def _refresh_map_post(bot: Any, platform: Any, guild_id: str, entry: Any, service: Any) -> None:
    """Keep an existing map post's content and layout in sync (best-effort)."""
    import time

    from botforge.core.services.entity_forum import (
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
        content, view = await map_post_render(entry, guild_id, bot, service)
        display = f"{entry.name[:1].upper()}{entry.name[1:]}"
        # the content is ONLY the markdown — the card's face (thumbnail,
        # props, images) lives in the view. A fingerprint of the content
        # alone skips the edit whenever a doc refresh changed the view
        # but not the description (the 2026-11-07 stale-thumbnail bug:
        # posts kept their pre-sync view forever). The fingerprint covers
        # the view's data too.
        stored = await _map_content_full(entry.id, guild_id, bot)
        image, icon = stored.image, stored.icon
        walls, nomad, tc, capacity, creator = (
            stored.walls, stored.nomad, stored.tc, stored.capacity, stored.creator
        )
        gallery = stored.gallery
        pools = await _map_pools_link(service, entry, guild_id, bot=bot)
        signature = "\n".join(
            [
                content,
                f"icon={icon}",
                f"image={image}",
                f"walls={walls}",
                f"nomad={nomad}",
                f"tc={tc}",
                f"capacity={capacity}",
                f"creator={creator}",
                f"creator_url={stored.creator_url}",
                f"type={getattr(entry, 'map_type', '')}",
                f"pools={'|'.join(pools)}",
                f"gallery={'|'.join(gallery)}",
                f"versions={'|'.join(v['name'] for v in stored.versions)}",
            ]
        )
        fingerprint = _content_fingerprint(signature)
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
    from botforge.discord.scope_surfaces import register_scope_sync

    async def _sync(guild_id: str, bot: Any) -> int:
        actions = 0
        service = _build_service()
        from botforge.discord.wiring import granted_game_keys, guild_has_game

        for game_key in await granted_game_keys(guild_id, tuple(await service.list_game_keys())):
            if not await guild_has_game(guild_id, game_key):
                continue
            actions += await sync_maps_forum(guild_id, game_key, bot, service)
        return actions

    register_scope_sync("games", _sync)


@dataclass(frozen=True)
class MapPostContent:
    """One map's stored content, resolved for its post.

    The traits (walls/nomad/tc/capacity/creator) live as separate doc
    keys next to the summary; ``gallery`` is the whole Gallery's
    resolved URLs; ``versions`` are the map's parsed variants
    (name + description each).
    """

    name: str = ""
    summary: str = ""
    source: str = ""
    image: str = ""
    icon: str = ""
    walls: str = ""
    nomad: str = ""
    tc: str = ""
    capacity: str = ""
    creator: str = ""
    creator_url: str = ""
    gallery: tuple[str, ...] = ()
    versions: tuple[dict[str, str], ...] = ()


def _map_post_content_of(doc: dict[str, Any]) -> MapPostContent:
    """Build the post content from a stored content doc."""
    image_url = str(doc.get("image_url") or "")
    # a page without a Gallery has no in-game icon — the main image
    # becomes the thumbnail so every card keeps the same shape
    return MapPostContent(
        name=str(doc.get("name") or ""),
        summary=str(doc.get("summary") or ""),
        source=str(doc.get("source_url") or ""),
        image=image_url,
        icon=str(doc.get("icon_url") or "") or image_url,
        walls=str(doc.get("walls") or ""),
        nomad=str(doc.get("nomad") or ""),
        tc=str(doc.get("tc") or ""),
        capacity=str(doc.get("player_capacity") or ""),
        creator=str(doc.get("creator") or ""),
        creator_url=str(doc.get("creator_url") or ""),
        gallery=tuple(
            str(g.get("url") or "")
            for g in doc.get("gallery_images") or ()
            if str(g.get("url") or "")
        ),
        versions=tuple(
            {
                "name": str(v.get("name") or ""),
                "description": str(v.get("description") or ""),
            }
            for v in doc.get("versions") or ()
            if str(v.get("name") or "")
        ),
    )


async def _map_content_full(entity_id: str, guild_id: str, bot: Any) -> MapPostContent:
    """Resolve a map's full stored content for its post.

    Liquipedia serves English ONLY (owner direction 2026-11-07): the map
    docs are stored under "en", read "en" — a stale per-locale doc
    from an earlier era must never win the read. A stale doc shape
    re-resolves from Liquipedia once and re-stores the new keys; the
    next pass takes the fast path.
    """
    from botforge.discord.content_posts import content_service

    service = content_service()
    doc = None
    if service is not None:
        try:
            doc = await service.content_get(entity_id, "en")
        except Exception:
            doc = None
    if doc is None:
        return MapPostContent()
    if not (doc.get("split_keys") or (doc.get("icon_url") and doc.get("walls") is not None)):
        migrated = await _migrate_stale_map_doc(entity_id, doc, service, "en")
        if migrated is not None:
            doc = migrated
    return _map_post_content_of(doc)


async def _migrate_stale_map_doc(
    entity_id: str, doc: dict[str, Any], service: Any, locale: str
) -> dict[str, Any] | None:
    """Re-resolve a stale Liquipedia map doc and store the new keys."""
    try:
        import os as _os

        from botforge.aoe2.map_seed import LiquipediaUnconfiguredError
        from botforge.game_aoe2.client import GameDataClient

        _uri = _os.environ.get("GAME_AOE2_URI", "")
        _client = GameDataClient(_uri) if _uri else None
        seed = (
            await _client.get_map_seed(str(doc.get("name") or entity_id.rsplit(":", 1)[-1]))
            if _client is not None
            else None
        )
    except LiquipediaUnconfiguredError:
        return None
    except Exception:
        logger.warning("stale map doc migration failed (%s) — best-effort", entity_id, exc_info=True)
        return None
    if seed is None:
        return None
    # the FULL doc shape (map_content_doc) — the migration used to
    # patch the old doc's keys and kept its stale tags, capacity,
    # creator and versions forever (the 2026-11-08 audit)
    from botforge.aoe2.map_seed import map_content_doc

    name = str(doc.get("name") or entity_id.rsplit(":", 1)[-1])
    updated = map_content_doc(entity_id, name, seed)
    # the one-shot marker: a map without an icon would otherwise
    # never satisfy the freshness check and re-fetch forever
    updated["split_keys"] = True
    if not updated["image_url"]:
        updated["image_url"] = str(doc.get("image_url") or "")
    try:
        await service.content_store(updated)
    except Exception:
        logger.warning("stale map doc re-store failed (%s) — best-effort", entity_id, exc_info=True)
        return updated
    logger.info("map doc migrated to the split keys (%s)", entity_id)
    return updated

def _description_display(summary: str) -> str:
    """Render the stored description for the French post.

    The provider's fallback ("Map page on Liquipedia: <url>") is
    English data; the post is French UI — the fallback renders as a
    French line, anything else rides as-is.
    """
    prefix = "Map page on Liquipedia: "
    if summary.startswith(prefix):
        # the provider's fallback carries no content — the source link
        # already lives in the credits; the description stays empty
        # (owner direction 2026-11-08)
        return ""
    return summary


def _walls_slug(walls: str) -> str:
    """Normalize the walls label into its filter slug.

    The valid vocabulary (owner direction 2026-11-08):
    ``stone-walls`` / ``palisade-walls`` / ``fortified-walls`` /
    ``partial-walls`` — English, lowercase, filterable. Empty hides.
    """
    value = walls.strip().lower()
    if not value or value in ("none", "no", "false", "true"):
        return ""
    if "stone" in value:
        return "stone-walls"
    if "wood" in value or "palisade" in value:
        return "palisade-walls"
    if "fortif" in value:
        return "fortified-walls"
    if "partial" in value:
        return "partial-walls"
    if value == "yes":
        return "stone-walls"
    return ""


def _map_props_lines(
    map_type: str, walls: str, nomad: str, tc: str, player_capacity: str = ""
) -> list[str]:
    """Build the map's subtitle tags as backticked FILTER SLUGS.

    English, lowercase, never translated — they are query keys the
    users filter on (owner direction 2026-11-08). The valid vocabulary:
    - the Liquipedia type (`land`/`water`/`hybrid`)
    - the walls (`stone-walls`/`palisade-walls`/`fortified-walls`)
    - `up-to-X-players` — only when X < 8, the nominal stays silent
    - `nomad` — the Liquipedia flag or a TC=0 start
    - `X-tc` — when the start is more than one TC (hyphenated slugs:
      backticked spaces read badly, owner direction 2026-11-08)
    The open/closed/semi-open/chaotic classification is NOT here: it
    is the designers' vocabulary, delegated to botforge-services#348.
    """
    tags: list[str] = []
    type_slug = map_type.strip().lower()
    if type_slug:
        tags.append(f"`{type_slug}`")
    walls_tag = _walls_slug(walls)
    if walls_tag:
        tags.append(f"`{walls_tag}`")
    capacity = re.search(r"up to\s+(\d+)", player_capacity, re.IGNORECASE)
    if capacity and int(capacity.group(1)) < 8:
        tags.append(f"`up-to-{capacity.group(1)}-players`")
    if nomad.strip().lower() == "yes" or tc.strip() == "0":
        tags.append("`nomad`")
    if tc.strip().isdigit() and int(tc.strip()) > 1:
        tags.append(f"`{tc.strip()}-tc`")
    return tags
