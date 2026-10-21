"""Generic entity-forum sync engine — one post per entity, per forum.

The maps forum (``<game>-maps``) and the pools forum
(``<game>-map-pools``) follow the same pattern: ensure a forum in the
``games`` category, then ensure exactly one post per visible entity,
on demand (a deleted post is recreated, a vanished entity's post is
deleted). That pattern is engine-grade: any mod can declare a forum of
entity posts without re-implementing the sync loop.

An ``EntityForumSpec`` names the forum (no magic words — the name is
data), the category, and how to list the entities and build each
post. ``sync_entity_forum`` is idempotent; ``register_entity_forum_sync``
declares it to the scope-surfaces registry (the sync button runs it). Matched posts are refreshed when their layout
builder changes over time.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import discord

from kingdoms.discord.message_limits import truncate_body

logger = logging.getLogger("kingdoms.core.entity_forum")

GAMES_CATEGORY_NAME = "games"
EDIT_BACKOFF_S = 23 * 3600
_STALE_EDITS: dict[str, float] = {}


def _content_fingerprint(content: str) -> str:
    """Hash one post's rendered content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _stagger_stale_edit(thread_id: str, now: float) -> bool:
    """Guard Discord's 'edits to messages older than 1 hour' rate limit.

    Discord allows only a few edits per hour on old messages; editing every
    sync cycle burns the quota instantly. A post whose last edit failed with
    the stale-edit limit is skipped for EDIT_BACKOFF_S, so a full forum pass
    stays far under the cap while genuine content changes still go through.
    """
    until = _STALE_EDITS.get(thread_id, 0.0)
    return until > now


def _note_stale_edit(thread_id: str, now: float) -> None:
    """Record one stale-edit rejection; the post retries after the backoff."""
    _STALE_EDITS[thread_id] = now + EDIT_BACKOFF_S


_LAST_LAYOUT_RENDER: dict[str, str] = {}


def _last_layout_render(thread_id: str) -> str:
    """Return the fingerprint last pushed to a components-v2 post, if any."""
    return _LAST_LAYOUT_RENDER.get(thread_id, "")


def _remember_layout_render(thread_id: str, fingerprint: str) -> None:
    """Remember the last fingerprint pushed to one layout post."""
    _LAST_LAYOUT_RENDER[thread_id] = fingerprint


def forget_layout_renders() -> None:
    """Drop every remembered fingerprint (a locale change re-renders).

    The fingerprints are process-memory change detection; after a guild
    language change the recomputed (translated) content must push even
    when the pre-change pass remembers the same thread.
    """
    _LAST_LAYOUT_RENDER.clear()


@dataclass(frozen=True)
class EntityForumSpec:
    """Declarative description of one entity-post forum.

    ``tree_channel_for`` returns the channel **key** of the tree
    folder the entities' forum lives in (``civs`` — the provisioning
    stored its id under ``tree:<scope>:<key>``); ``tree_scope_for``
    returns the scope (``games/<game_key>``). When the entities span
    several forums (e.g. one per game), the entities are grouped by
    that pair and each group is synced to its own forum. The forum is
    resolved by its **stored channel id** — never by its translated
    name; the provisioning owns creation.
    """

    list_entities: Callable[[str], Awaitable[list[Any]]] | None = None
    tree_scope_for: Callable[[Any], str] | None = None
    tree_channel_for: Callable[[Any], str] | None = None
    entity_name: Callable[[Any], str] = field(default=lambda entity: str(getattr(entity, "name", "")))
    display_name_for: Callable[[Any, str], Awaitable[str]] | None = None
    build_post: Callable[[Any, str], Awaitable[tuple[str, Any | None]]] | None = None


async def sync_entity_forum(guild_id: str, bot: Any, spec: EntityForumSpec) -> int:
    """Ensure the forum and one post per entity (idempotent, self-healing).

    Posts are matched by thread name (the entity's display name); the
    spec's ``build_post`` returns ``(content, view)`` for each entity.
    Returns the number of forum actions taken. The pass holds the
    guild's forum-sync lock when the platform provides it (the
    concurrent syncs never double-create).
    """
    lock = None
    try:
        from kingdoms.discord.maps_forum import _sync_lock

        lock = _sync_lock(guild_id)
    except Exception:
        lock = None
    if lock is None:
        return await _sync_entity_forum_locked(guild_id, bot, spec)
    async with lock:
        return await _sync_entity_forum_locked(guild_id, bot, spec)


async def _sync_entity_forum_locked(guild_id: str, bot: Any, spec: EntityForumSpec) -> int:
    """Run the sync body (the caller holds the guild's sync lock)."""
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform

    platform = DiscordChannelsPlatform(bot)
    if spec.list_entities is None or spec.build_post is None:
        return 0
    entities = await spec.list_entities(guild_id)
    groups: dict[tuple[str, str], list[Any]] = {}
    for entity in entities:
        scope = spec.tree_scope_for(entity) if spec.tree_scope_for is not None else "games"
        channel_key = spec.tree_channel_for(entity) if spec.tree_channel_for is not None else "civs"
        groups.setdefault((scope, channel_key), []).append(entity)
    actions = 0
    for (scope, channel_key), members in groups.items():
        actions += await _sync_one_forum(guild_id, bot, spec, platform, scope, channel_key, members)
    return actions


async def _sync_one_forum(
    guild_id: str,
    bot: Any,
    spec: EntityForumSpec,
    platform: Any,
    tree_scope: str,
    tree_channel: str,
    entities: list[Any],
) -> int:
    """Sync one tree forum (resolved by stored id) against its entities."""
    guild = bot.get_guild(int(guild_id))
    if guild is None or spec.build_post is None:
        return 0
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    stored = await stored_channel_id(guild_id, tree_scope, tree_channel)
    forum = None
    if stored and stored.isdigit():
        channel = guild.get_channel(int(stored))
        forum = channel if channel is not None and channel.type == discord.ChannelType.forum else None
    if forum is None:
        return 0  # the provisioning owns creation; nothing stored yet
    wanted: dict[str, Any] = {}
    for entity in entities:
        key = spec.entity_name(entity)
        if spec.display_name_for is not None:
            key = await spec.display_name_for(entity, guild_id)
        wanted[key] = entity
    existing = {thread.name: thread for thread in forum.threads}
    actions = 0
    for name, entity in wanted.items():
        content, view = await spec.build_post(entity, guild_id)
        if name not in existing:
            await platform.create_map_post(guild_id, str(forum.id), name, content, view=view)
            actions += 1
        else:
            await _refresh_post(existing[name], content, view)
    for name, thread in existing.items():
        if name not in wanted:
            await thread.delete(reason=f"kingdoms: entity {name} no longer exists")
            actions += 1
    return actions


async def _refresh_post(thread: Any, content: str, view: Any | None) -> None:
    """Edit one existing post, only when its content actually changed.

    The rendered content is hashed before editing: an unchanged post is left
    alone (zero edits on a steady-state cycle), which keeps the sync far
    under Discord's old-message edit quota. Rejected stale edits back off.
    """
    import time

    now = time.monotonic()
    if _stagger_stale_edit(str(thread.id), now):
        return
    fingerprint = _content_fingerprint(truncate_body(content))
    try:
        starter = await thread.fetch_message(thread.id)
        if isinstance(view, discord.ui.LayoutView):
            current = _last_layout_render(thread.id)
        else:
            current = starter.content
        if _content_fingerprint(current or "") == fingerprint:
            return
        _remember_layout_render(thread.id, fingerprint)
        if isinstance(view, discord.ui.LayoutView):
            await starter.edit(view=view)
        else:
            await starter.edit(content=truncate_body(content), view=view)
    except discord.HTTPException as exc:
        if getattr(exc, "code", None) == 30046:
            _note_stale_edit(str(thread.id), now)
            logger.info("entity forum: stale-edit quota hit (thread %s) - backing off", thread.id)
            return
        logger.debug("entity forum: post refresh skipped (thread %s)", thread.id, exc_info=True)
    except Exception:
        logger.debug("entity forum: post refresh skipped (thread %s)", thread.id, exc_info=True)


def register_entity_forum_sync(spec: EntityForumSpec) -> None:
    """Declare one entity forum's mirror sync (the sync button runs it)."""
    from kingdoms.discord.scope_surfaces import register_scope_sync

    async def _sync(guild_id: str, bot: Any) -> int:
        return await sync_entity_forum(guild_id, bot, spec)

    register_scope_sync("games", _sync)
