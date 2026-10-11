"""Shared helpers to find a bot's own recent messages in a channel.

A real :class:`discord.TextChannel` exposes no ``.messages`` cache — only
the in-memory test mock does — so the live messages come from the
``history()`` iterator. Every panel/content refresh must go through
these helpers, otherwise the old message is never found on the real API
and each re-deployment duplicates the pinned message (kingdoms#244).
"""
from __future__ import annotations

from typing import Any


async def channel_messages(channel: Any) -> list[Any]:
    """Return the mock cache when present, else the last 100 real messages.

    The ``.messages`` attribute only exists on the test mock; a real
    channel is read through ``history()`` (100 messages cover the pinned
    panels of every kingdoms channel with room to spare).
    """
    cached = getattr(channel, "messages", None)
    if cached is not None:
        return list(cached)
    return [message async for message in channel.history(limit=100)]


def message_text(message: Any) -> str:
    """Return the message content plus every text of the components tree.

    A Components V2 message carries an empty ``content``: its text lives
    inside the view's ``TextDisplay`` items, so the tree is walked to
    collect every string (markers ride there).
    """
    parts = [str(getattr(message, "content", "") or "")]
    stack: list[Any] = list(getattr(message, "components", None) or [])
    layout = getattr(message, "layout", None)
    if layout is not None:
        stack.append(layout)
    while stack:
        item = stack.pop()
        text = getattr(item, "content", None)
        if isinstance(text, str):
            parts.append(text)
        stack.extend(getattr(item, "children", None) or [])
    return "\n".join(parts)
