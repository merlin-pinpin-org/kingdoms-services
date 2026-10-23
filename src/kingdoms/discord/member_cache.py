"""Guild member cache: one chunked fetch per guild, TTL + event-maintained.

The membership checks (the players panel's guild filter, the live
dashboard's snapshot scope) are per-player; without a cache each check
hits the Discord API (fetch_member) and the panels take seconds to
open. The cache resolves the whole roster ONCE per guild (chunked
fetch), serves every check from memory, and the member events
(on_member_join/on_member_remove) keep it fresh between TTLs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

MEMBER_CACHE_TTL_S = 3600.0  # the owner's 1h
_cache: dict[int, tuple[float, dict[int, str]]] = {}
_pending: dict[int, Any] = {}


def _now() -> float:
    return time.monotonic()


async def guild_members(guild: Any) -> dict[int, str]:
    """Resolve the guild's members (id → display name), cached for the TTL.

    The first call per guild (or after the TTL elapsed) does one chunked
    fetch of the whole roster; every later call is a memory read. A
    fetch failure serves the stale entry when there is one; an EMPTY
    first fetch is NOT cached (the callers treat 'no roster' as 'do
    not filter' — fail-open, an unresolvable roster must never blank
    the dashboards).
    """
    guild_id = int(guild.id)
    entry = _cache.get(guild_id)
    if entry is not None and entry[0] > _now():
        return entry[1]

    # one fetch at a time per guild (concurrent panels share the result)
    pending = _pending.get(guild_id)
    if pending is not None:
        return await pending  # type: ignore[no-any-return]

    async def _fetch() -> dict[int, str]:
        members: dict[int, str] = {}
        try:
            async for member in guild.fetch_members(limit=None):
                members[int(member.id)] = str(member.display_name)
        except Exception:
            logger.warning(
                "member cache fetch failed (guild %s) — serving the stale entry",
                guild_id,
                exc_info=True,
            )
            if entry is not None:
                return dict(entry[1])  # stale beats empty
            return {}
        if not members and entry is None:
            # an empty first fetch is a resolution failure (missing
            # privileged intent, API hiccup) — not a guild of zero
            # members; leave the cache unset so the callers fail open
            return members
        _cache[guild_id] = (_now() + MEMBER_CACHE_TTL_S, members)
        return members

    coro = _fetch()
    _pending[guild_id] = coro
    try:
        return await coro
    finally:
        _pending.pop(guild_id, None)


async def is_guild_member(guild: Any, user_id: str | int) -> bool:
    """Return whether one user is a member of the guild (the cached roster)."""
    try:
        members = await guild_members(guild)
    except Exception:
        return False
    return int(user_id) in members


async def member_display_name(guild: Any, user_id: str | int) -> str:
    """Return the member's display name (empty when not a member)."""
    members = await guild_members(guild)
    return members.get(int(user_id), "")


def on_member_join(member: Any) -> None:
    """Record one join immediately (the cache stays fresh between TTLs)."""
    entry = _cache.get(int(member.guild.id))
    if entry is None:
        return  # no cache yet: the next fetch carries the member
    members = dict(entry[1])
    members[int(member.id)] = str(member.display_name)
    _cache[int(member.guild.id)] = (entry[0], members)


def on_member_remove(member: Any) -> None:
    """Drop one leave immediately (the cache stays fresh between TTLs)."""
    entry = _cache.get(int(member.guild.id))
    if entry is None:
        return
    members = dict(entry[1])
    members.pop(int(member.id), None)
    _cache[int(member.guild.id)] = (entry[0], members)
