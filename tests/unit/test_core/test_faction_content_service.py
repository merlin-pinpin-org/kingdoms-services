"""FactionContentService tests: store/get, cache-aside behaviour, invalidation."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.faction_content import CACHE_SCOPE, FactionContentService

DOC = {
    "entity_id": "faction:aoe2:Franks",
    "locale": "fr",
    "name": "Francs",
    "summary": "Civilisation de cavalerie.",
    "source_url": "https://example.org",
    "provider": "aoe2techtree",
}


class FakeCollection:
    """Minimal async Mongo collection seam: upsert + find_one."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def replace_one(self, filter: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        assert upsert
        self.docs[str(doc["_id"])] = dict(doc)

    async def find_one(self, filter: dict[str, Any]) -> dict[str, Any] | None:
        return self.docs.get(str(filter["_id"]))


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


class FakeState:
    """In-memory StateService seam recording cache traffic."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.deleted: list[str] = []

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        self.get_calls += 1
        return self.store.get(f"{scope}:{key}")

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        self.set_calls += 1
        assert ttl is not None
        self.store[f"{scope}:{key}"] = dict(value)
        return True

    async def delete_state(self, scope: str, key: str) -> bool:
        self.deleted.append(f"{scope}:{key}")
        return self.store.pop(f"{scope}:{key}", None) is not None


async def test_store_then_get_roundtrip() -> None:
    db = FakeDatabase()
    service = FactionContentService(db)
    await service.store(DOC)
    doc = await service.get("faction:aoe2:Franks", "fr")
    assert doc is not None
    assert doc["name"] == "Francs"
    assert doc["provider"] == "aoe2techtree"


async def test_get_misses_when_not_stored() -> None:
    service = FactionContentService(FakeDatabase())
    assert await service.get("faction:aoe2:Franks", "fr") is None


async def test_get_populates_cache_then_hits_cache() -> None:
    db = FakeDatabase()
    state = FakeState()
    service = FactionContentService(db, state)
    await service.store(DOC)
    first = await service.get("faction:aoe2:Franks", "fr")
    assert first is not None
    assert state.get_calls == 1
    second = await service.get("faction:aoe2:Franks", "fr")
    assert second is not None
    assert state.get_calls == 2
    assert db.collections["faction_content"].docs  # durable store untouched on the cache hit


async def test_store_invalidates_cached_entry() -> None:
    state = FakeState()
    service = FactionContentService(FakeDatabase(), state)
    await service.store(DOC)
    await service.get("faction:aoe2:Franks", "fr")
    updated = dict(DOC, name="Francs (maj)")
    await service.store(updated)
    assert f"{CACHE_SCOPE}:faction:aoe2:Franks:fr" in state.deleted
    doc = await service.get("faction:aoe2:Franks", "fr")
    assert doc is not None
    assert doc["name"] == "Francs (maj)"


async def test_store_is_idempotent_upsert() -> None:
    db = FakeDatabase()
    service = FactionContentService(db)
    await service.store(DOC)
    await service.store(DOC)
    assert len(db.collections["faction_content"].docs) == 1
