"""The map-pools forum: one post per pool, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-map-pools`` forum (e.g. ``aoe2-map-pools``). Every visible
pool (owned by this guild or public) gets exactly one post built as a
component layout: a header (name + description), one **section per
member map** (map name linked to its ``<game>-maps`` forum post, the
map's image when it has one, and a **Remove** accessory button,
admin-guarded at click time), and an **Add map** button that opens
the pool picker. Every member map gets its section even without an
image. The sync is idempotent, on demand (the sync button) — a deleted post is
recreated, a disbanded pool's post is deleted, a deleted forum is
recreated, and every refresh re-attaches the current components.
Legacy per-pool forums (``map-pools-<slug>``) are deleted on sight.
Nothing here is ladder-specific: pools are a core game-catalog
concept; this surface just mirrors them.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.games.pools_forum")

GAMES_CATEGORY_NAME = "games"
MAX_SECTIONS = 10
MAX_TEXT_CHUNKS = 4
TEXT_CHUNK_MAPS = 20


def _build_service() -> Any:
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


def _pool_state_badge(state: str) -> str:
    """Render the pool's state as a labeled badge line."""
    from kingdoms.core.services.game_data import pool_state_badge

    return pool_state_badge(state)



def _pool_thread_title(pool: Any) -> str:
    """Build the pool's thread title: the state emoji rides the name."""
    from kingdoms.core.services.game_data import POOL_STATE_EMOJIS

    name = pool.name[:1].upper() + pool.name[1:]
    emoji = POOL_STATE_EMOJIS.get(getattr(pool, "state", ""), "")
    title = f"{emoji} {name}" if emoji else name
    return title[:100]


def _pool_post_layout(
    pool: Any,
    maps: list[dict[str, Any]],
    editable: bool = False,
) -> discord.ui.LayoutView:
    """Build the pool post on the shared pool-card template.

    State header (accent container), then one Section per member map
    (the name links to the map's post, the map image is the accessory
    thumbnail), the overflow as text lines, the id footer and the
    action row (add map when in edition, transition, delete).
    """
    from kingdoms.discord.entity_admin_menu import EntityAdminButton
    from kingdoms.discord.ui.entity_cards import pool_card

    state_line = _pool_state_badge(getattr(pool, "state", "draft"))
    mod_link = getattr(pool, "mod_link", "")
    if mod_link:
        state_line += f"\n**Mod à installer** : {mod_link}"

    sections: list[tuple[str, str, str]] = []
    for m in maps:
        image = m.get("image_url") or m.get("resource_url") or ""
        link = ""
        if m.get("forum_message_id") and m.get("guild_id"):
            link = (
                f"https://discord.com/channels/{m['guild_id']}/"
                f"{m['forum_message_id']}/{m['forum_message_id']}"
            )
        name = m["name"]
        if m.get("map_type"):
            name += f" — *{m['map_type']}*"
        sections.append((name, link, image))

    buttons: list[Any] = [
        lambda: EntityAdminButton(
            "pool", pool.id, game_key=str(getattr(pool, "game_key", "")), owned=editable
        )
    ]
    return pool_card(
        pool_id=pool.id,
        name=pool.name,
        state_line=state_line,
        description=pool.description or "",
        member_sections=tuple(sections),
        buttons=tuple(buttons),
    )



def _pool_post_content(pool: Any, maps: list[dict[str, Any]]) -> str:
    """Fallback plain-content body when components are unavailable."""
    lines = [f"**{pool.name}**", pool.description or "", "", "Maps :"]
    lines += [f"- **{m['name']}**" for m in maps] or ["_Aucune map._"]
    return "\n".join(lines)


async def _map_image_url(entry: Any) -> str:
    """Resolve the map's image URL from the stored content (best-effort)."""
    try:
        from kingdoms.discord.content_posts import content_service

        service = content_service()
        if service is None:
            return ""
        for locale in ("fr", "en"):
            doc = await service.get(entry.id, locale)
            if doc and doc.get("image_url"):
                return str(doc["image_url"])
    except Exception:
        logger.debug("pools forum: image resolve skipped (%s)", entry.id, exc_info=True)
    return ""


async def _pool_maps(service: Any, pool: Any, guild_id: str) -> list[dict[str, Any]]:
    """Resolve the pool's member maps (kept even when incomplete)."""
    maps: list[dict[str, Any]] = []
    for map_id in pool.map_ids:
        entry = await service.get_map(map_id)
        if entry is None or entry.archived_at is not None:
            continue
        maps.append(
            {
                "id": entry.id,
                "name": entry.name,
                "resource_url": entry.resource_url,
                "forum_message_id": entry.forum_message_id if entry.forum_message_id else None,
                "guild_id": guild_id,
                "image_url": await _map_image_url(entry),
                "map_type": getattr(entry, "map_type", ""),
                "filenames": tuple(getattr(entry, "filenames", ()) or ()),
            }
        )
    return maps


async def sync_pools_forum(guild_id: str, bot: Any, service: Any = None) -> int:
    """Ensure every game's pools forum and one post per pool (idempotent).

    The pass holds the guild's forum-sync lock (shared with the maps
    and civs syncs): two concurrent syncs never create
    the same post twice.
    """
    from kingdoms.discord.maps_forum import _sync_lock

    async with _sync_lock(guild_id):
        return await _sync_pools_forum_locked(guild_id, bot, service)


async def _sync_pools_forum_locked(guild_id: str, bot: Any, service: Any = None) -> int:
    """Run the sync body (the caller holds the guild's sync lock)."""
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    service = service if service is not None else _build_service()
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return 0
    actions = 0
    from kingdoms.discord.wiring import granted_game_keys, guild_has_game

    for game_key in await granted_game_keys(guild_id, tuple(await service.list_game_keys())):
        if not await guild_has_game(guild_id, game_key):
            continue
        actions += await _sync_game_pools(guild, guild_id, platform, service, game_key, bot)
    return actions





async def _refresh_member_map_posts(bot: Any, guild_id: str, wanted: dict[str, Any]) -> None:
    """Re-render the member maps' posts after a pools change (best-effort).

    The cross-references: every member map's post carries the pool
    links, and the pools' threads were just created/renamed — the
    member maps' posts re-render so a fresh forum gets valid links
    everywhere (the pools first, then the maps that reference them).
    """
    if not any(getattr(p, "forum_thread_id", "") for p in wanted.values()):
        return
    member_maps = sorted({m for p in wanted.values() for m in (p.map_ids or ())})
    try:
        from kingdoms.discord.maps_forum import refresh_map_posts

        await refresh_map_posts(bot, guild_id, member_maps)
    except Exception:
        logger.warning("pools forum: member map posts refresh failed — best-effort", exc_info=True)


async def _sync_game_pools(
    guild: discord.Guild,
    guild_id: str,
    platform: Any,
    service: Any,
    game_key: str,
    bot: Any = None,
) -> int:
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    forum_id = await stored_channel_id(guild_id, f"games/{game_key}", "map-pools")
    forum = None
    if forum_id and forum_id.isdigit():
        channel = guild.get_channel(int(forum_id))
        forum = channel if channel is not None and channel.type == discord.ChannelType.forum else None
    if forum is None:
        return 0  # the provisioning owns creation; nothing stored yet
    pools = await service.list_all_map_pools(game_key, guild_id=guild_id)
    wanted: dict[str, Any] = {pool.id: pool for pool in pools}
    existing = {t.name: t for t in forum.threads}
    by_thread_id = {str(t.id): t for t in forum.threads}
    actions = 0
    for pool_id, pool in wanted.items():
        maps = await _pool_maps(service, pool, guild_id)
        editable = pool.edition_mode and pool.owner_guild_id == guild_id
        # the stored pool->thread link wins (rename-proof); the name is
        # the legacy fallback for posts created before the link existed
        thread = by_thread_id.get(str(getattr(pool, "forum_thread_id", "") or ""))
        legacy = existing.get(pool.name)
        if thread is None and legacy is not None:
            thread = legacy
            try:
                await service.set_pool_forum_thread(pool_id, str(thread.id))
            except Exception:
                logger.debug("pools forum: thread link store skipped (%s)", pool_id, exc_info=True)
        if thread is None:
            await _create_pool_post(platform, guild_id, forum, service, pool, maps, editable)
            actions += 1
        else:
            await _refresh_pool_post(guild, guild_id, thread, pool, maps, editable)
    for name, thread in existing.items():
        linked = any(
            str(getattr(p, "forum_thread_id", "") or "") == str(thread.id) for p in wanted.values()
        )
        if name not in wanted and not linked:
            await thread.delete(reason=f"kingdoms: pool {name} no longer visible")
            actions += 1
    if actions:
        await _refresh_member_map_posts(bot, guild_id, wanted)
    return actions


async def _create_pool_post(
    platform: Any,
    guild_id: str,
    forum: Any,
    service: Any,
    pool: Any,
    maps: list[dict[str, Any]],
    editable: bool = False,
) -> None:
    thread_id = await platform.create_map_post(
        guild_id,
        str(forum.id),
        _pool_thread_title(pool),
        _pool_post_content(pool, maps),
        view=_pool_post_layout(pool, maps, editable),
    )
    try:
        await service.set_pool_forum_thread(pool.id, str(thread_id))
    except Exception:
        logger.debug("pools forum: thread link store skipped (%s)", pool.id, exc_info=True)


async def _refresh_pool_post(
    guild: discord.Guild,
    guild_id: str,
    thread: Any,
    pool: Any,
    maps: list[dict[str, Any]],
    editable: bool = False,
) -> None:
    """Edit one pool post, only when its content actually changed."""
    import time

    from kingdoms.core.services.entity_forum import (
        _content_fingerprint,
        _last_layout_render,
        _note_stale_edit,
        _remember_layout_render,
        _stagger_stale_edit,
    )

    now = time.monotonic()
    if _stagger_stale_edit(str(thread.id), now):
        return
    content = _pool_post_content(pool, maps)
    fingerprint = _content_fingerprint(content)
    # the name and the state live in the layout (thread title + badge),
    # not in the plain-content fallback — they ride the fingerprint too
    # so a rename or a transition re-edits the post
    fingerprint = _content_fingerprint(f"{getattr(pool, 'name', '')}\n{getattr(pool, 'state', '')}\n{fingerprint}")
    try:
        starter = await thread.fetch_message(thread.id)
        if _last_layout_render(str(thread.id)) == fingerprint:
            return
        _remember_layout_render(str(thread.id), fingerprint)
        # the title compare uses the EXPECTED title (emoji included) —
        # a stale comparison re-titled every pool on every sync pass and
        # burned the channel's PATCH budget (the 492s 429 of the test env)
        expected_title = _pool_thread_title(pool)
        if thread.name != expected_title:
            await thread.edit(name=expected_title, reason="kingdoms: pool state or name changed")
        await starter.edit(view=_pool_post_layout(pool, maps, editable))
    except discord.HTTPException as exc:
        if getattr(exc, "code", None) == 30046:
            _note_stale_edit(str(thread.id), now)
            return
        logger.debug("pools forum: post refresh skipped (thread %s)", thread.id, exc_info=True)
    except Exception:
        logger.debug("pools forum: post refresh skipped (thread %s)", thread.id, exc_info=True)


def register_pools_forum_sync() -> None:
    """Declare the pools forums' mirror sync (the sync button runs it)."""
    from kingdoms.discord.scope_surfaces import register_scope_sync

    async def _sync(guild_id: str, bot: Any) -> int:
        return await sync_pools_forum(guild_id, bot)

    register_scope_sync("games", _sync)
