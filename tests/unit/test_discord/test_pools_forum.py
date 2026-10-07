"""Unit tests for the map-pools forum sync (one post per pool)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kingdoms.discord.pools_forum import (
    _pool_content,
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
    archived_at: int | None = None


class _FakeService:
    async def get_map(self, map_id: str) -> Any:
        known = {
            "map:aoe2:arabia": _FakeMap("map:aoe2:arabia", "Arabia"),
            "map:aoe2:gone": _FakeMap("map:aoe2:gone", "Gone", archived_at=1),
        }
        return known.get(map_id)


@dataclass
class _FakePool:
    id: str
    name: str
    description: str
    map_ids: tuple[str, ...]


async def test_pool_content_lists_active_maps() -> None:
    pool = _FakePool("map_pool:aoe2:p1", "Rotation 1", "Desc", ("map:aoe2:arabia", "map:aoe2:gone", "map:aoe2:missing"))
    content, map_names = await _pool_content(_FakeService(), pool)
    assert "**Arabia**" in content
    assert "Gone" not in content
    assert map_names == {"map:aoe2:arabia": "Arabia"}
