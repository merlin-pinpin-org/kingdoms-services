"""Generic entity-forum sync engine — one post per entity, per forum.

The maps forum (``<game>-maps``) and the pools forum
(``<game>-map-pools``) follow the same pattern: ensure a forum in the
``games`` category, then ensure exactly one post per visible entity,
self-healing (a deleted post is recreated, a vanished entity's post is
deleted). That pattern is engine-grade: any mod can declare a forum of
entity posts without re-implementing the sync loop.

An ``EntityForumSpec`` names the forum (no magic words — the name is
data), the category, and how to list the entities and build each
post. ``sync_entity_forum`` is idempotent; ``start_entity_forum_sync``
runs it periodically. Matched posts are refreshed when their layout
builder changes over time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import discord

logger = logging.getLogger("kingdoms.core.entity_forum")

GAMES_CATEGORY_NAME = "games"
SYNC_INTERVAL_S = 3600


@dataclass(frozen=True)
class EntityForumSpec:
    """Declarative description of one entity-post forum."""

    forum_name: str
    category_name: str = GAMES_CATEGORY_NAME
    list_entities: Callable[[str], Awaitable[list[Any]]] | None = None
    entity_name: Callable[[Any], str] = field(default=lambda entity: str(getattr(entity, "name", "")))
    build_post: Callable[[Any, str], Awaitable[tuple[str, Any | None]]] | None = None


async def sync_entity_forum(guild_id: str, bot: Any, spec: EntityForumSpec) -> int:
    """Ensure the forum and one post per entity (idempotent, self-healing).

    Posts are matched by thread name (the entity's display name); the
    spec's ``build_post`` returns ``(content, view)`` for each entity.
    Returns the number of forum actions taken.
    """
    from kingdoms.discord.channels_platform import DiscordChannelsPlatform
    from kingdoms.discord.wiring import guild_category

    platform = DiscordChannelsPlatform(bot)
    guild = bot.get_guild(int(guild_id))
    if guild is None or spec.list_entities is None or spec.build_post is None:
        return 0
    forum = discord.utils.get(guild.forums, name=spec.forum_name)
    if forum is None:
        forum = await guild.create_forum(
            spec.forum_name,
            category=await guild_category(guild, spec.category_name),
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
            reason=f"kingdoms: {spec.forum_name} forum",
        )
    entities = await spec.list_entities(guild_id)
    wanted = {spec.entity_name(entity): entity for entity in entities}
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
    """Keep an existing post's content and components in sync (best-effort)."""
    try:
        starter = await thread.fetch_message(thread.id)
        await starter.edit(content=content, view=view)
    except Exception:
        logger.debug("entity forum: post refresh skipped (thread %s)", thread.id, exc_info=True)


def start_entity_forum_sync(
    bot: Any,
    spec: EntityForumSpec,
    *,
    interval_s: int = SYNC_INTERVAL_S,
    startup_delay_s: int = 10,
) -> asyncio.Task[None]:
    """Sync the forum in every guild periodically, forever, quietly."""

    async def _loop() -> None:
        await asyncio.sleep(startup_delay_s)
        while True:
            for guild in list(bot.guilds):
                try:
                    actions = await sync_entity_forum(str(guild.id), bot, spec)
                    if actions:
                        logger.info("entity forum %s: %d changes (guild %s)", spec.forum_name, actions, guild.id)
                except Exception:
                    logger.warning(
                        "entity forum %s sync failed (guild %s) — best-effort",
                        spec.forum_name,
                        guild.id,
                        exc_info=True,
                    )
            await asyncio.sleep(interval_s)

    return asyncio.create_task(_loop())
