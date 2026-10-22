"""FactionContentService tests: store/get, cache-aside behaviour, invalidation."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.faction_content import CACHE_SCOPE, FactionContentService

DOC = {
    "entity_id": "faction:aoe2:franks",
    "locale": "fr",
    "name": "Francs",
    "summary": "Civilisation de cavalerie.",
    "source_url": "https://example.org",
    "provider": "aoe2techtree",
}


class _FakeCursor:
    """Minimal async cursor over a precomputed list."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def __aiter__(self) -> _FakeCursor:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


class FakeCollection:
    """Minimal async Mongo collection seam: upsert + find_one."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.indexes: list[Any] = []

    async def replace_one(self, filter: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        assert upsert
        self.docs[str(doc["_id"])] = dict(doc)

    async def find_one(self, filter: dict[str, Any]) -> dict[str, Any] | None:
        return self.docs.get(str(filter["_id"]))

    async def create_index(self, keys: Any) -> None:
        normalized = keys if isinstance(keys, list) else [keys]
        if normalized not in self.indexes:
            self.indexes.append(normalized)

    def find(self, filter: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor(self._find_sync(filter))

    def _find_sync(self, filter: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for doc in self.docs.values():
            if self._matches(doc, filter):
                results.append(doc)
        return results

    @staticmethod
    def _matches(doc: dict[str, Any], filter: dict[str, Any]) -> bool:
        for key, expected in filter.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$all" in expected:
                    if not all(tag in (doc.get(key) or []) for tag in expected["$all"]):
                        return False
                elif "$regex" in expected:
                    import re as _re

                    if not _re.match(expected["$regex"], str(actual)):
                        return False
                else:
                    return False
            elif actual != expected:
                return False
        return True



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
    doc = await service.get("faction:aoe2:franks", "fr")
    assert doc is not None
    assert doc["name"] == "Francs"
    assert doc["provider"] == "aoe2techtree"


async def test_get_misses_when_not_stored() -> None:
    service = FactionContentService(FakeDatabase())
    assert await service.get("faction:aoe2:franks", "fr") is None


async def test_get_populates_cache_then_hits_cache() -> None:
    db = FakeDatabase()
    state = FakeState()
    service = FactionContentService(db, state)
    await service.store(DOC)
    baseline = state.get_calls
    first = await service.get("faction:aoe2:franks", "fr")
    assert first is not None
    assert state.get_calls == baseline + 1
    second = await service.get("faction:aoe2:franks", "fr")
    assert second is not None
    assert state.get_calls == baseline + 2
    assert db.collections["faction_content"].docs  # durable store untouched on the cache hit


async def test_store_invalidates_cached_entry() -> None:
    state = FakeState()
    service = FactionContentService(FakeDatabase(), state)
    await service.store(DOC)
    await service.get("faction:aoe2:franks", "fr")
    updated = dict(DOC, name="Francs (maj)")
    await service.store(updated)
    assert f"{CACHE_SCOPE}:faction:aoe2:franks:fr" in state.deleted
    doc = await service.get("faction:aoe2:franks", "fr")
    assert doc is not None
    assert doc["name"] == "Francs (maj)"


async def test_store_is_idempotent_upsert() -> None:
    db = FakeDatabase()
    service = FactionContentService(db)
    await service.store(DOC)
    await service.store(DOC)
    assert len(db.collections["faction_content"].docs) == 1


async def test_ensure_indexes_creates_query_indexes() -> None:
    db = FakeDatabase()
    service = FactionContentService(db)
    await service.ensure_indexes()
    collection = db.collections["faction_content"]
    assert ["tags"] in collection.indexes
    assert [("entity_id", 1), ("locale", 1)] in collection.indexes


async def test_query_by_tags_scopes_and_filters() -> None:
    db = FakeDatabase()
    service = FactionContentService(db)
    hybrid = dict(DOC, entity_id="map:aoe2:public:islands", tags=["terrain:hybrid", "water"])
    hybrid["_id"] = "map:aoe2:public:islands:fr"
    service._database["faction_content"].docs[hybrid["_id"]] = hybrid
    faction = dict(DOC, tags=["terrain:hybrid"])
    faction["_id"] = "faction:aoe2:franks:fr"
    service._database["faction_content"].docs[faction["_id"]] = faction

    maps = await service.query_by_tags(["terrain:hybrid"], entity_prefix="map:aoe2:public:")
    assert [d["entity_id"] for d in maps] == ["map:aoe2:public:islands"]
    everything = await service.query_by_tags(["terrain:hybrid"])
    assert len(everything) == 2


async def test_list_map_content_caches_guild_list_and_invalidates_on_store() -> None:
    state = FakeState()
    service = FactionContentService(FakeDatabase(), state)
    doc = dict(DOC, entity_id="map:aoe2:public:arabia")
    doc["_id"] = "map:aoe2:public:arabia:fr"
    service._database["faction_content"].docs[doc["_id"]] = dict(doc)

    found = await service.list_map_content(["map:aoe2:public:arabia"], "fr", cache_key="guild:1:fr")
    assert [d["entity_id"] for d in found] == ["map:aoe2:public:arabia"]

    baseline = state.get_calls
    cached = await service.list_map_content(["map:aoe2:public:arabia"], "fr", cache_key="guild:1:fr")
    assert [d["entity_id"] for d in cached] == ["map:aoe2:public:arabia"]
    assert state.get_calls == baseline + 1  # one cache read, no Mongo

    updated = dict(doc, name="Arabia (maj)")
    await service.store(updated)
    assert any(k.startswith("faction_content_list:guild:1:fr") for k in state.deleted)
    assert any("tracker:map:aoe2:public:arabia:fr" in k for k in state.deleted)
