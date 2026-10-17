"""Pinned-view marks: every pinned message carries its view id.

A pinned surface (admin panel, home menu, live dashboard, boot status)
is never a deletable message: any delete path checks the mark first.
The mark is a footer line carrying the view's id (``view:<scope>:<slug>``)
rendered at the end of the
pinned message's content — readable without fetching history.
"""

from __future__ import annotations

import re
from typing import Any

PIN_MARK_PREFIX = ""

# a view id in a footer line: view:<scope>:<slug> (scope may be multi-part)
_VIEW_ID_RE = re.compile(r"-# `view:[a-z0-9_-]+(?::[a-z0-9_-]+)*`")


def pinned_mark(suffix: str) -> str:
    """Render the pinned-view id footer (``<suffix>`` — a view id)."""
    from kingdoms.core.ids import footer

    return footer(f"{PIN_MARK_PREFIX}{suffix}")


def is_pinned_view(message: Any) -> bool:
    """Whether a message carries a pinned-view mark (never delete it).

    Reads the message's content — the mark is the last footer line
    (a ``view:<scope>:<slug>`` id); a message without one is free to
    delete.
    """
    content = str(getattr(message, "content", "") or "")
    if _VIEW_ID_RE.search(content):
        return True
    # LayoutView-only messages carry the mark in the components' text
    for embed in getattr(message, "embeds", None) or []:
        if _VIEW_ID_RE.search(str(getattr(embed, "description", "") or "")):
            return True
    return False
