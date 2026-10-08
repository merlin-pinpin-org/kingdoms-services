"""Kingdoms mod seed engine — unit tests (kingdoms.mods.kingdoms.kingdoms_seed)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdoms_seed import seed_kingdoms_mod

SEED: dict[str, Any] = {
    "season_id": "kingdoms-aoe2-123-1",
    "kingdoms": ["Aquitaine"],
    "territories": [
        {"map_key": "arabia", "owner": "gaia"},
        {"map_key": "kawasan", "owner": "Aquitaine"},
    ],
    "lords": [{"player_id": "111", "kingdom": "Aquitaine", "role": "lord", "display_name": "Rollon"}],
}


class _FakeCollection:
    """In-memory Mongo collection (find_one + insert_one)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return self.docs.get(str(query.get("_id")))

    async def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs[doc["_id"]] = doc


class _FakeDatabase:
    """In-memory database keyed by collection name."""

    def __init__(self) -> None:
        self.kingdoms_kingdoms = _FakeCollection()
        self.kingdoms_territories = _FakeCollection()
        self.kingdoms_lords = _FakeCollection()

    def __getitem__(self, name: str) -> _FakeCollection:
        return getattr(self, name)


async def test_seed_writes_kingdoms_territories_and_lords() -> None:
    database = _FakeDatabase()
    report = await seed_kingdoms_mod(database, SEED, "123")
    assert report.season_id == "kingdoms-aoe2-123-1"
    assert report.kingdoms == 1
    assert report.territories == 2
    assert report.lords == 1
    assert "k-1" in database.kingdoms_kingdoms.docs
    assert database.kingdoms_territories.docs["territory:kingdoms-aoe2-123-1:arabia"]["owner_kingdom_id"] == "gaia"
    assert (
        database.kingdoms_territories.docs["territory:kingdoms-aoe2-123-1:kawasan"]["owner_kingdom_id"] == "k-1"
    )


async def test_seed_is_idempotent() -> None:
    database = _FakeDatabase()
    await seed_kingdoms_mod(database, SEED, "123")
    report = await seed_kingdoms_mod(database, SEED, "123")
    assert report.kingdoms == 0
    assert report.territories == 0
    assert report.lords == 0


async def test_seed_requires_a_season_id() -> None:
    with pytest.raises(ValueError, match="season_id"):
        await seed_kingdoms_mod(_FakeDatabase(), {"kingdoms": ["A"]}, "123")
