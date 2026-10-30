"""Unit tests for the ephemeral live dashboard surface (#147).

The live view is ephemeral-only (owner direction): rendered on demand
(/live, the menu button, the refresh button), never pinned, never a
persistent message. Covered here: the render itself (title, body,
relative timestamp, the refresh button, the id footer) and the
guild-scoped snapshot filter.
"""

from __future__ import annotations

import pytest

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.live import (
    DEFAULT_GAME_KEY as GAME_KEY,
)
from kingdoms.discord.live import build_dashboard_view, live_view_key, scope_snapshot_to_guild


class _CatalogBot:
    """A bot stand-in exposing the message catalog."""

    messages = MessageCatalog("config")


def _view_text(view: object) -> str:
    """Flatten a LayoutView's TextDisplays into text."""
    if view is None:
        return ""
    parts: list[str] = []
    for item in getattr(getattr(view, "children", []), "__iter__", lambda: [])():
        for child in getattr(item, "children", []) or []:
            text = getattr(child, "content", "") or getattr(child, "label", "")
            if isinstance(text, str):
                parts.append(text)
        text = getattr(item, "content", "") or getattr(item, "label", "")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def test_live_view_key_is_stable() -> None:
    """The declared view id: the same for every surface."""
    assert live_view_key(GAME_KEY) == "view:aoe2:live"


def test_build_dashboard_view_renders_the_essentials() -> None:
    """Title, body, relative timestamp, refresh button, id footer."""
    snapshot = {"players": [], "generated_at": 1730000000000, "degraded": False}
    view = build_dashboard_view(snapshot, "🟡 someone is in a lobby", _CatalogBot(), locale="fr")
    text = _view_text(view)
    assert "Dashboard en direct" in text, "the guild locale renders (FR catalog)"
    assert "🟡 someone is in a lobby" in text
    assert "<t:1730000000:R>" in text, "the relative timestamp rides the container"
    assert "Rafraîchir" in text, "the refresh button renders in the locale"
    assert "panel:game:aoe2:live" in text, "the view id is the footer"


def test_build_dashboard_view_degraded_renders_red() -> None:
    """A degraded snapshot renders (the colour flips, the id stays)."""
    snapshot = {"players": [], "generated_at": 1730000000000, "degraded": True}
    view = build_dashboard_view(snapshot, "⚠️ stale", _CatalogBot(), locale="fr")
    assert "panel:game:aoe2:live" in _view_text(view)


@pytest.mark.asyncio
async def test_scope_snapshot_filters_by_cached_members() -> None:
    """The dashboard scope keeps only the guild's members."""
    from kingdoms.discord import member_cache

    snapshot = {
        "players": [
            {"user_id": "10", "profile_id": "111", "state": "in_lobby", "match_ref": "m", "since": 1},
            {"user_id": "20", "profile_id": "222", "state": "offline", "match_ref": "", "since": 2},
        ],
        "generated_at": 3,
    }

    class _Member:
        def __init__(self, uid: int, name: str) -> None:
            self.id = uid
            self.display_name = name

    class _Guild:
        id = 424242

        async def fetch_members(self, limit: int | None = None):
            yield _Member(10, "alice")

    member_cache._cache.pop(424242, None)
    try:
        scoped = await scope_snapshot_to_guild(snapshot, _Guild())
        assert [p["user_id"] for p in scoped["players"]] == ["10"]
    finally:
        member_cache._cache.pop(424242, None)


@pytest.mark.asyncio
async def test_scope_snapshot_fails_open_on_empty_roster() -> None:
    """An unresolvable roster never blanks the dashboard."""
    snapshot = {
        "players": [{"user_id": "10", "profile_id": "111", "state": "in_lobby", "match_ref": "m", "since": 1}],
        "generated_at": 3,
    }

    class _Guild:
        id = 434343

        async def fetch_members(self, limit: int | None = None):
            return
            yield  # pragma: no cover - an empty async generator

    from kingdoms.discord import member_cache

    member_cache._cache.pop(434343, None)
    try:
        scoped = await scope_snapshot_to_guild(snapshot, _Guild())
        assert len(scoped["players"]) == 1, "an unresolvable roster fails open"
    finally:
        member_cache._cache.pop(434343, None)
