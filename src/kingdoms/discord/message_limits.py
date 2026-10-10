"""Discord message-size safety — truncate or paginate, never fail.

Discord caps a message body at 2000 chars, an embed description at 4096,
and a forum thread starter post at 2000 (plus the embed). Any view that
renders a list of variable length must expect to cross the cap: this
module is the single seam for that.

Two policies:

- ``truncate_body`` — one message, cut at a line boundary, with an
  explicit ``(+N more)`` marker. For compact summaries.
- ``MultiMessageRegistry`` — paginated follow-up messages, tracked by
  registered message ids: growing a list appends messages, shrinking it
  deletes the extra ones, and the pagination stays stable across
  refreshes. For lists that must be read whole (live dashboards).

Nothing here raises: a render that crosses a limit degrades to a
smaller, clearly-marked output.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.discord.message_limits")

MAX_MESSAGE = 2000
MAX_EMBED_DESCRIPTION = 4096
_ELLIPSIS_MARK = "…"


def truncate_body(body: str, limit: int = MAX_MESSAGE) -> str:
    """Fit one message body under ``limit``, cutting on a line boundary.

    When the body has to shrink, a final ``(+N more)`` line states how
    much is hidden — a truncated list is never silently shorter.
    """
    if len(body) <= limit:
        return body
    lines = body.split("\n")
    kept: list[str] = []
    total = 0
    for line in lines:
        cost = len(line) + 1
        if total + cost > limit:
            break
        kept.append(line)
        total += cost
    while kept:  # shrink until the marker itself fits
        hidden = len(lines) - len(kept)
        marker = f"{_ELLIPSIS_MARK} (+{hidden} more)"
        if total + len(marker) <= limit:
            kept.append(marker)
            break
        dropped = kept.pop()
        total -= len(dropped) + 1
    if not kept:  # pathological: not even one line + marker fits
        return body[: limit - 1] + _ELLIPSIS_MARK
    result = "\n".join(kept)
    if len(result) > limit:  # single pathological line: hard cut
        result = result[: limit - 1] + _ELLIPSIS_MARK
    return result


def paginate(body: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split one body into page strings, each within ``limit``.

    Splits on line boundaries when possible (a page never starts or
    ends mid-list-item); a single line longer than the limit is hard-cut
    across pages.
    """
    if len(body) <= limit:
        return [body]
    pages: list[str] = []
    current: list[str] = []
    total = 0
    for line in body.split("\n"):
        cost = len(line) + 1
        if total + cost > limit and current:
            pages.append("\n".join(current))
            current = []
            total = 0
        while len(line) + 1 > limit:  # pathological long line: hard-cut
            pages.append(line[: limit - 1] + _ELLIPSIS_MARK)
            line = line[limit - 1 :]
            total = 0
            current = []
        current.append(line)
        total += len(line) + 1
    if current:
        pages.append("\n".join(current))
    return pages


class _MessageSink(Protocol):
    """The subset of a Discord channel/thread used by the registry."""

    async def send(self, content: str, **kwargs: Any) -> Any: ...

    async def fetch_message(self, message_id: int) -> Any: ...


class MultiMessageRegistry:
    """Publish ``body`` across one or more messages, tracked by ids.

    ``publish`` is idempotent and size-driven: it edits the first
    message in place, edits the following pages, appends new pages when
    the list grew, and deletes the surplus pages when it shrank — the
    message ids live in a registry keyed by ``registry_key`` (the
    caller owns that key, e.g. ``live:<guild_id>``), so nothing leaks
    and nothing duplicates.
    """

    def __init__(
        self,
        registry: Any,
        platform: str,
        message_key: str,
        *,
        limit: int = MAX_MESSAGE,
    ) -> None:
        self._registry = registry
        self._platform = platform
        self._message_key = message_key
        self._limit = limit

    async def _ids(self, entity_id: str) -> list[str]:
        registered = await self._registry.resolve(self._platform, self._message_key, entity_id)
        if registered is None:
            return []
        raw = str(getattr(registered, "message_id", "") or "")
        return [part for part in raw.split(",") if part]

    async def _store(self, entity_id: str, ids: list[str], channel_id: str) -> None:
        await self._registry.register(
            platform=self._platform,
            message_key=self._message_key,
            entity_id=entity_id,
            channel_id=channel_id,
            message_id=",".join(ids),
        )

    async def publish(self, channel: _MessageSink, entity_id: str, body: str) -> list[str]:
        """Render ``body`` into ``channel``, reconciling message count."""
        pages = paginate(body, limit=self._limit)
        ids = await self._ids(entity_id)
        messages = await self._fetch_messages(channel, ids)
        if not messages:
            return await self._send_all(channel, entity_id, pages)
        for message, page in zip(messages[: len(pages)], pages, strict=False):
            if message.content != page:
                await message.edit(content=page)
        if len(pages) > len(messages):  # grew: append the surplus pages
            for page in pages[len(messages) :]:
                messages.append(await channel.send(page))
        elif len(messages) > len(pages):  # shrank: delete the surplus
            from kingdoms.discord.pinned_marks import is_pinned_view

            for message in messages[len(pages) :]:
                if is_pinned_view(message):
                    continue
                await message.delete()
            messages = messages[: len(pages)]
        ids = [str(m.id) for m in messages]
        await self._store(entity_id, ids, str(messages[0].channel_id))
        return ids

    async def _fetch_messages(self, channel: _MessageSink, ids: list[str]) -> list[Any]:
        messages: list[Any] = []
        for raw_id in ids:
            try:
                messages.append(await channel.fetch_message(int(raw_id)))
            except Exception:
                logger.debug("multi-message: stale id %s dropped", raw_id, exc_info=True)
        return messages

    async def _send_all(self, channel: _MessageSink, entity_id: str, pages: list[str]) -> list[str]:
        fresh = [await channel.send(page) for page in pages]
        ids = [str(m.id) for m in fresh]
        await self._store(entity_id, ids, str(getattr(fresh[0], "channel_id", "")))
        return ids
