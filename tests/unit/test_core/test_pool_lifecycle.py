"""Unit tests for the map-pool lifecycle state machine."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.game_data import (
    POOL_STATE_CLOSED,
    POOL_STATE_DRAFT,
    POOL_STATE_PUBLISHED,
    POOL_STATE_USED,
    GameDataService,
    InvalidPoolStateError,
)


class _FakeDb:
    """Minimal GameDataDatabase seam: an in-memory store."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _col(self, collection: str) -> dict[str, dict[str, Any]]:
        return self.collections.setdefault(collection, {})

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        self._col(collection)[str(document["_id"])] = document

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        return self._col(collection).get(entry_id)

    async def find_by_name(self, collection: str, game_key: str, name: str) -> dict[str, Any] | None:
        for doc in self._col(collection).values():
            if doc.get("game_key") == game_key and doc.get("name") == name:
                return doc
        return None

    async def find_ladder_activations(self, ladder_id: str) -> list[dict[str, Any]]:
        """No activation history in the fake (empty)."""
        del ladder_id
        return []

    async def find_open_activations(self, map_pool_id: str) -> list[dict[str, Any]]:
        """No open activation in the fake (empty)."""
        del map_pool_id
        return []


@pytest.mark.asyncio
async def test_lifecycle_happy_path() -> None:
    """draft -> published -> used -> closed, edition following the state."""
    service = GameDataService(_FakeDb())  # type: ignore[arg-type]
    m = await service.create_map("aoe2", "Arabia", filename="arabia.v0")
    pool = await service.create_map_pool("aoe2", "P", map_ids=(m.id,))
    assert pool.state == POOL_STATE_DRAFT and pool.edition_mode

    published = await service.transition_map_pool(pool.id, POOL_STATE_PUBLISHED)
    assert published.state == POOL_STATE_PUBLISHED

    reopened = await service.transition_map_pool(pool.id, POOL_STATE_DRAFT)
    assert reopened.state == POOL_STATE_DRAFT and reopened.edition_mode

    published = await service.transition_map_pool(pool.id, POOL_STATE_PUBLISHED)
    used = await service.transition_map_pool(pool.id, POOL_STATE_USED)
    assert used.state == POOL_STATE_USED and not used.edition_mode

    closed = await service.transition_map_pool(pool.id, POOL_STATE_CLOSED)
    assert closed.state == POOL_STATE_CLOSED


@pytest.mark.asyncio
async def test_lifecycle_refuses_invalid_transitions() -> None:
    """The machine refuses jumps the spec does not allow."""
    service = GameDataService(_FakeDb())  # type: ignore[arg-type]
    m = await service.create_map("aoe2", "Arabia", filename="arabia.v0")
    pool = await service.create_map_pool("aoe2", "P", map_ids=(m.id,))

    with pytest.raises(InvalidPoolStateError, match="cannot move"):
        await service.transition_map_pool(pool.id, POOL_STATE_USED)

    published = await service.transition_map_pool(pool.id, POOL_STATE_PUBLISHED)
    with pytest.raises(InvalidPoolStateError, match="cannot move"):
        await service.transition_map_pool(published.id, POOL_STATE_CLOSED)


@pytest.mark.asyncio
async def test_used_pool_is_frozen() -> None:
    """A used pool never accepts composition changes again."""
    service = GameDataService(_FakeDb())  # type: ignore[arg-type]
    m = await service.create_map("aoe2", "Arabia", filename="arabia.v0")
    pool = await service.create_map_pool("aoe2", "P", map_ids=(m.id,))

    activation = await service.activate_map_pool("ladder:1:aoe2", pool.id)
    del activation
    used = await service.get_map_pool(pool.id)
    assert used is not None and used.state == POOL_STATE_USED

    with pytest.raises(Exception, match="locked"):
        await service.update_map_pool(pool.id, map_ids=())
