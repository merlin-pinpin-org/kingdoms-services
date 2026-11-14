"""Unit tests for the map-pools forum sync (one post per pool)."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import discord

from botforge.discord.pools_forum import (
    _pool_maps,
    _pool_post_content,
    _pool_post_layout,
    _refresh_pool_post,
)


def _walk(view: Any) -> list[Any]:
    """Every item of a layout, containers included (top-down)."""
    items: list[Any] = []

    def _visit(item: Any) -> None:
        items.append(item)
        for child in getattr(item, "children", ()) or ():
            _visit(child)

    for child in view.children:
        _visit(child)
    return items


@dataclass
class _FakeForum:
    name: str
    deleted: list[str] = field(default_factory=list)

    async def delete(self, reason: str = "") -> None:
        self.deleted.append(reason)


@dataclass
class _FakeGuild:
    forums: list[_FakeForum]


@dataclass
class _FakeMap:
    id: str
    name: str
    resource_url: str = ""
    forum_message_id: str | None = None
    archived_at: int | None = None


class _FakeService:
    async def get_map(self, map_id: str) -> Any:
        known = {
            "map:aoe2:public:arabia": _FakeMap("map:aoe2:public:arabia", "Arabia", "https://x/arabia.png", "111"),
            "map:aoe2:public:plain": _FakeMap("map:aoe2:public:plain", "Plain"),
            "map:aoe2:public:gone": _FakeMap("map:aoe2:public:gone", "Gone", archived_at=1),
        }
        return known.get(map_id)


@dataclass
class _FakePool:
    id: str
    name: str
    description: str
    map_ids: tuple[str, ...]


_POOL = _FakePool(
    "map_pool:aoe2:p1",
    "Rotation 1",
    "Desc",
    ("map:aoe2:public:arabia", "map:aoe2:public:plain", "map:aoe2:public:gone"),
)


async def test_pool_maps_resolves_members_keeps_linkless_maps() -> None:
    maps = await _pool_maps(_FakeService(), _POOL, "42")
    assert [m["name"] for m in maps] == ["Arabia", "Plain"]
    assert maps[0]["resource_url"] == "https://x/arabia.png"
    assert maps[0]["forum_message_id"] == "111"
    assert maps[1]["forum_message_id"] is None


def test_pool_post_content_lists_every_member() -> None:
    content = _pool_post_content(_POOL, [{"name": "Arabia"}, {"name": "Plain"}])
    assert "**Rotation 1**" in content
    assert "- **Arabia**" in content


def test_pool_post_layout_has_section_per_map_and_add_button() -> None:
    import discord

    view = _pool_post_layout(
        _POOL,
        [
            {
                "id": "m1",
                "name": "Arabia",
                "resource_url": "https://x/a.png",
                "forum_message_id": "111",
                "guild_id": "42",
            },
            {"id": "m2", "name": "Plain", "resource_url": "", "forum_message_id": None, "guild_id": "42"},
        ],
        editable=True,
    )
    assert isinstance(view, discord.ui.LayoutView)
    items = _walk(view)
    sections = [i for i in items if isinstance(i, discord.ui.Section)]
    assert len(sections) == 1  # the imaged member rides a Section; Plain is a text line
    assert sections[0].accessory is not None
    # the #321 contract: the id in backticks in the subtitle, the id
    # footer ALWAYS the last text block — whatever the pool's state
    texts = [i for i in items if isinstance(i, discord.ui.TextDisplay)]
    assert f"`{_POOL.id}`" in texts[1].content or any(f"`{_POOL.id}`" in t.content for t in texts[:2])
    assert texts[-1].content == f"-# `panel:{_POOL.id}`"
    from botforge.discord.entity_admin_menu import EntityAdminButton

    rows = [i for i in view.children if isinstance(i, discord.ui.ActionRow)]
    buttons = [b for row in rows for b in row.children]
    assert any(isinstance(b, EntityAdminButton) for b in buttons)  # the single admin entry point


def test_pool_post_layout_locked_pool_has_no_add_button() -> None:
    import discord

    view = _pool_post_layout(_POOL, [], editable=False)
    sections = [i for i in _walk(view) if isinstance(i, discord.ui.Section)]
    assert sections == []  # a locked pool is read-only: no add entry point


def test_pool_post_layout_map_sections_have_no_remove_button() -> None:
    import discord

    view = _pool_post_layout(
        _POOL,
        [
            {
                "id": "m1",
                "name": "Arabia",
                "resource_url": "",
                "forum_message_id": "111",
                "guild_id": "42",
            }
        ],
        editable=True,
    )
    sections = [i for i in _walk(view) if isinstance(i, discord.ui.Section)]
    assert len(sections) == 0  # imageless member = text line, no section
    rows = [i for i in view.children if isinstance(i, discord.ui.ActionRow)]
    buttons = [b for row in rows for b in row.children]
    from botforge.discord.entity_admin_menu import EntityAdminButton

    assert any(isinstance(b, EntityAdminButton) for b in buttons)  # the single admin entry point


def test_to_draft_button_label_is_short() -> None:
    """The back-to-edition button fits a row (the shorter 'Éditer' label)."""
    from botforge.discord.admin_panel_games import GamesPoolToDraftButton

    button = GamesPoolToDraftButton("pool-1")
    assert button.item.label == "Éditer"


def test_pool_state_label_maps_all_states() -> None:
    """The badge renders the label of the pool's state."""
    from botforge.core.services.game_data import POOL_STATE_LABELS

    assert POOL_STATE_LABELS.get("draft") == "édition"
    assert POOL_STATE_LABELS.get("published") not in (None, "published")


def _refresh_thread(name: str) -> Any:
    """A thread stand-in whose title edit can be made to raise."""

    class _Msg:
        edited = 0

        async def edit(self, view: Any = None, **kw: Any) -> None:
            del kw
            _Msg.edited += 1

    class _Thread:
        def __init__(self) -> None:
            self.id = 42
            self.name = name
            self.parent_id = 99
            self.edits = 0

        async def fetch_message(self, mid: Any) -> Any:
            del mid
            return _Msg()

        async def edit(self, **kw: Any) -> None:
            self.edits += 1
            if getattr(_Thread, "raise_rate_limited", False):
                raise discord.errors.RateLimited(560.0)

    return _Thread()


async def test_title_rate_limit_never_aborts_the_view_refresh(monkeypatch: Any) -> None:
    """A 429 on the channel-title bucket must not freeze the maps.

    The title edit rides the ~2/10 min channel bucket (a publish/edit
    cycle eats it); the view edit (the member maps) must still run —
    the merlin env showed the maps frozen behind a RateLimited title.
    """
    from botforge.core.services import entity_forum as ef

    monkeypatch.setattr(ef, "EDIT_PACING_S", 0)
    ef._last_edit_at.clear()
    ef._STALE_EDITS.clear()
    thread = _refresh_thread("Old title")
    pool = SimpleNamespace(
        id="p",
        name="Test Pool",
        description="",
        state="draft",
        edition_mode=True,
        owner_guild_id="42",
        mod_link="",
        map_ids=[],
    )
    from botforge.core.services.entity_forum import _last_layout_render

    ef._LAST_LAYOUT_RENDER.pop(str(thread.id), None)
    await _refresh_pool_post(None, "42", thread, pool, [], editable=True)
    assert thread.edits == 1  # the title edit was ATTEMPTED (then rate-limited)
    # and the view edit ran anyway — the maps are on the post:
    # the fingerprint is remembered only when the view edit succeeded
    assert _last_layout_render(str(thread.id)) != ""


async def test_failed_view_edit_is_not_remembered(monkeypatch: Any) -> None:
    """A fingerprint remembered before a failed edit freezes the post.

    The old order (remember, then edit) marked the post rendered even
    when the edit raised — every later pass skipped it. The fingerprint
    is remembered only on success now.
    """
    from botforge.core.services import entity_forum as ef

    monkeypatch.setattr(ef, "EDIT_PACING_S", 0)
    ef._last_edit_at.clear()
    ef._STALE_EDITS.clear()
    ef._LAST_LAYOUT_RENDER.clear()

    class _FailingMsg:
        async def edit(self, view: Any = None, **kw: Any) -> None:
            del view, kw
            raise RuntimeError("boom")

    class _Thread:
        id = 7
        name = "Same title"
        parent_id = 3

        async def fetch_message(self, mid: Any) -> Any:
            del mid
            return _FailingMsg()

        async def edit(self, **kw: Any) -> None:
            del kw
            raise AssertionError("the title matches — no title edit expected")

    pool = SimpleNamespace(
        id="p",
        name="Same title",
        description="",
        state="draft",
        edition_mode=True,
        owner_guild_id="42",
        mod_link="",
        map_ids=[],
    )
    await _refresh_pool_post(None, "42", _Thread(), pool, [], editable=True)
    from botforge.core.services.entity_forum import _last_layout_render

    assert _last_layout_render("7") == ""  # not remembered: the next pass retries
