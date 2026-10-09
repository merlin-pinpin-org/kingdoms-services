"""The map-pools forum: one post per pool, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-map-pools`` forum (e.g. ``aoe2-map-pools``). Every visible
pool (owned by this guild or public) gets exactly one post built as a
component layout: a header (name + description), one **section per
member map** (map name linked to its ``<game>-maps`` forum post, the
map's image when it has one, and a **Remove** accessory button,
admin-guarded at click time), and an **Add map** button that opens
the pool picker. Every member map gets its section even without an
image. The sync is idempotent and self-healing — a deleted post is
recreated, a disbanded pool's post is deleted, a deleted forum is
recreated, and every refresh re-attaches the current components.
Legacy per-pool forums (``map-pools-<slug>``) are deleted on sight.
Nothing here is ladder-specific: pools are a core game-catalog
concept; this surface just mirrors them.
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
MAX_SECTIONS = 10
MAX_TEXT_CHUNKS = 4
TEXT_CHUNK_MAPS = 20


def _build_service() -> Any:
    from kingdoms.discord.wiring import build_games_service

    return build_games_service()


def _pools_forum_name(game_key: str) -> str:
    return f"{game_key}{POOLS_FORUM_SUFFIX}"


def _pool_state_badge(state: str) -> str:
    """Render the pool's state as a labeled badge line."""
    from kingdoms.core.services.game_data import POOL_STATE_LABELS

    label = POOL_STATE_LABELS.get(state, state)
    return f"**État** : {label}"


def _pool_post_layout(
    pool: Any,
    maps: list[dict[str, Any]],
    editable: bool = False,
) -> discord.ui.LayoutView:
    """Build the pool post's component layout: state, maps, transition.

    The pool's lifecycle state leads the post, then its member maps are
    listed read-only (name + image + type when known, each image linking
    to its map post). The **Add map** entry point only renders on a pool
    still in edition (draft, or published-and-reopened); the transition
    button proposes the next state(s) the lifecycle allows.
    """
    from kingdoms.discord.maps_pool_flow import PoolAddMapButton, PoolTransitionButton

    view = discord.ui.LayoutView(timeout=None)
    header = f"## {pool.name}\n{_pool_state_badge(getattr(pool, 'state', 'draft'))}"
    if pool.description:
        header += f"\n{pool.description}"
    mod_link = getattr(pool, "mod_link", "")
    if mod_link:
        header += f"\n**Mod à installer** : {mod_link}"
    from kingdoms.core.ids import footer

    header += f"\n{footer(pool.id)}"
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(header)))
    for m in maps[:MAX_SECTIONS]:
        text = f"### {m['name']}"
        if m.get("map_type"):
            text += f" — *{m['map_type']}*"
        image = m.get("resource_url") or m.get("image_url") or ""
        if m.get("forum_message_id") and m.get("guild_id"):
            link = f"https://discord.com/channels/{m['guild_id']}/{m['forum_message_id']}/{m['forum_message_id']}"
            view.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(text),
                    accessory=discord.ui.Thumbnail(image)
                    if image
                    else discord.ui.Button(label="Voir la map", url=link),
                )
            )
        else:
            view.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(text),
                    accessory=discord.ui.Button(label="-", disabled=True),
                )
            )
    overflow = maps[MAX_SECTIONS:]
    for chunk_start in range(0, len(overflow), TEXT_CHUNK_MAPS * MAX_TEXT_CHUNKS):
        chunk = overflow[chunk_start : chunk_start + TEXT_CHUNK_MAPS * MAX_TEXT_CHUNKS]
        view.add_item(discord.ui.TextDisplay("\n".join(f"- {m['name']}" for m in chunk)))
    view.add_item(discord.ui.Separator())
    if editable:
        view.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay("Ajouter une map à ce pool :"),
                accessory=PoolAddMapButton(pool.id),
            )
        )
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(PoolTransitionButton(pool.id))
    view.add_item(row)
    return view


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
    """Ensure every game's pools forum and one post per pool (idempotent)."""
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    service = service if service is not None else _build_service()
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return 0
    actions = await _purge_legacy_forums(guild)
    from kingdoms.discord.wiring import granted_game_keys, guild_has_game

    for game_key in await granted_game_keys(guild_id, tuple(await service.list_game_keys())):
        if not await guild_has_game(guild_id, game_key):
            continue
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
        maps = await _pool_maps(service, pool, guild_id)
        editable = pool.edition_mode and pool.owner_guild_id == guild_id
        if name not in existing:
            await _create_pool_post(platform, guild_id, forum, pool, maps, editable)
            actions += 1
        else:
            await _refresh_pool_post(guild, guild_id, existing[name], pool, maps, editable)
    for name, thread in existing.items():
        if name not in wanted:
            await thread.delete(reason=f"kingdoms: pool {name} no longer visible")
            actions += 1
    return actions


async def _create_pool_post(
    platform: Any,
    guild_id: str,
    forum: Any,
    pool: Any,
    maps: list[dict[str, Any]],
    editable: bool = False,
) -> None:
    await platform.create_map_post(
        guild_id,
        str(forum.id),
        pool.name,
        _pool_post_content(pool, maps),
        view=_pool_post_layout(pool, maps, editable),
    )


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
    try:
        starter = await thread.fetch_message(thread.id)
        if _last_layout_render(str(thread.id)) == fingerprint:
            return
        _remember_layout_render(str(thread.id), fingerprint)
        await starter.edit(view=_pool_post_layout(pool, maps, editable))
    except discord.HTTPException as exc:
        if getattr(exc, "code", None) == 30046:
            _note_stale_edit(str(thread.id), now)
            return
        logger.debug("pools forum: post refresh skipped (thread %s)", thread.id, exc_info=True)
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
