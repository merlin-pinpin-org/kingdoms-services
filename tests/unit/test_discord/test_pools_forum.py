"""Unit tests for the map-pools forum sync (one post per pool)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kingdoms.discord.pools_forum import (
    _pool_maps,
    _pool_post_content,
    _pool_post_layout,
    _pools_forum_name,
    _purge_legacy_forums,
)


def test_pools_forum_name() -> None:
    assert _pools_forum_name("aoe2") == "aoe2-map-pools"


@dataclass
class _FakeForum:
    name: str
    deleted: list[str] = field(default_factory=list)

    async def delete(self, reason: str = "") -> None:
        self.deleted.append(reason)


@dataclass
class _FakeGuild:
    forums: list[_FakeForum]


async def test_purge_legacy_forums_deletes_per_pool_forums() -> None:
    legacy = _FakeForum("map-pools-season-1-rotation-1")
    keep = _FakeForum("aoe2-map-pools")
    maps = _FakeForum("aoe2-maps")
    guild = _FakeGuild([legacy, keep, maps])
    deleted = await _purge_legacy_forums(guild)  # type: ignore[arg-type]
    assert deleted == 1
    assert legacy.deleted and not keep.deleted and not maps.deleted


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
            "map:aoe2:arabia": _FakeMap("map:aoe2:arabia", "Arabia", "https://x/arabia.png", "111"),
            "map:aoe2:plain": _FakeMap("map:aoe2:plain", "Plain"),
            "map:aoe2:gone": _FakeMap("map:aoe2:gone", "Gone", archived_at=1),
        }
        return known.get(map_id)


@dataclass
class _FakePool:
    id: str
    name: str
    description: str
    map_ids: tuple[str, ...]


_POOL = _FakePool("map_pool:aoe2:p1", "Rotation 1", "Desc", ("map:aoe2:arabia", "map:aoe2:plain", "map:aoe2:gone"))


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
    )
    assert isinstance(view, discord.ui.LayoutView)
    sections = [i for i in view.children if isinstance(i, discord.ui.Section)]
    assert len(sections) == 3  # two map sections + the add-map section
    assert sections[0].accessory is not None
    assert sections[2].accessory is not None
