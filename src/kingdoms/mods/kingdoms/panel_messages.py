"""Shared helpers to find a bot's own recent messages in a channel.

A real :class:`discord.TextChannel` exposes no ``.messages`` cache — only
the in-memory test mock does — so the live messages come from the
``history()`` iterator. Every panel/content refresh must go through
these helpers, otherwise the old message is never found on the real API
and each re-deployment duplicates the pinned message (kingdoms#244).
"""
from __future__ import annotations

from typing import Any

import discord

NO_MENTIONS: discord.AllowedMentions = discord.AllowedMentions.none()
"""vues-silencieuses-v1 (Drasah 19/10): the defensive default for every
panel/view send. Discord pings any mention that rides the content by
default — a panel must NEVER ping; the true announcements (Géopolitique
and friends) opt back in explicitly."""


def _squash(text: str) -> str:
    """Collapse repeated lines (order-preserving first occurrences).

    vues-silencieuses-v1: a live Components V2 message exposes its text
    BOTH through the flattened ``components`` tree and the ``layout`` —
    :func:`message_text` therefore collects each TextDisplay several
    times. Squashing both sides of the identity guard through this
    helper keeps the comparison meaningful (same first-occurrence
    sequence) whatever the transport exposes.
    """
    return "\n".join(dict.fromkeys(text.split("\n")))


def view_text(view: Any) -> str:
    r"""Return the text a Components V2 ``LayoutView`` would carry once sent.

    vues-silencieuses-v1: the identity guard of the edit-in-place
    refreshes compares the LIVE message's squashed :func:`message_text`
    with the FRESH view's text — same LIFO walk over the children tree,
    same ``\\n`` join, both sides collapsed through :func:`_squash`,
    so a message and its replacement view read equal exactly when
    nothing changed (the marker rides both).
    """
    parts: list[str] = [""]
    stack: list[Any] = list(getattr(view, "children", None) or [])
    while stack:
        item = stack.pop()
        text = getattr(item, "content", None)
        if isinstance(text, str):
            parts.append(text)
        stack.extend(getattr(item, "children", None) or [])
    return _squash("\n".join(parts))


def message_text_squashed(message: Any) -> str:
    """Return :func:`message_text` collapsed through :func:`_squash`.

    vues-silencieuses-v1: this is the LIVE side of the identity guard —
    it reads equal to :func:`view_text` when the deployed content did
    not change.
    """
    return _squash(message_text(message))


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
