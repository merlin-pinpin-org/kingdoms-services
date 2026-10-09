"""Pinned-view marks: every pinned message carries a ``fixe:`` id.

A pinned surface (admin panel, home menu, live dashboard, boot status)
is never a deletable message: any delete path checks the mark first.
The mark is a footer line ``fixe:<suffix>`` rendered at the end of the
pinned message's content — readable without fetching history.
"""

from __future__ import annotations

from typing import Any

PIN_MARK_PREFIX = "fixe:"


def pinned_mark(suffix: str) -> str:
    """Render the pinned-view id footer (``fixe:<suffix>``)."""
    from kingdoms.core.ids import footer

    return footer(f"{PIN_MARK_PREFIX}{suffix}")


def is_pinned_view(message: Any) -> bool:
    """Whether a message carries a pinned-view mark (never delete it).

    Reads the message's content — the mark is the last footer line; a
    message without the ``fixe:`` prefix is free to delete.
    """
    content = str(getattr(message, "content", "") or "")
    if PIN_MARK_PREFIX in content:
        return True
    # LayoutView-only messages carry the mark in the components' text
    for embed in getattr(message, "embeds", None) or []:
        if PIN_MARK_PREFIX in str(getattr(embed, "description", "") or ""):
            return True
    return False
