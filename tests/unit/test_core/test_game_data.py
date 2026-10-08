"""Unit tests for the GameDataService catalog, pools and lifecycle (kingdoms-services#131)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.models.game_data import MapPoolModel
from kingdoms.core.services.game_data import (
    ADMIN_AUDIT_COLLECTION,
    MAP_PACKS_COLLECTION,
    MAP_POOL_HISTORY_COLLECTION,
    MAP_POOLS_COLLECTION,
    MAPS_COLLECTION,
    ActivePoolArchiveError,
    ArchivedEntryError,
    GameDataService,
    NameTakenError,
)


class FakeGameDataDatabase:
    """In-memory GameDataDatabase: per-collection docs keyed by _id."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _collection(self, name: str) -> dict[str, dict[str, Any]]:
        return self.collections.setdefault(name, {})

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        self._collection(collection)[document["_id"]] = document

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        return self._collection(collection).get(entry_id)

    async def find_by_name(self, collection: str, game_key: str, name: str) -> dict[str, Any] | None:
        for doc in self._collection(collection).values():
            if doc.get("game_key") == game_key and doc.get("name") == name and doc.get("archived_at") is None:
                return doc
        return None

    async def find_active_maps(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        return [
            d
            for d in self._collection(MAPS_COLLECTION).values()
            if d.get("game_key") == game_key and d.get("archived_at") is None
        ]

    async def find_active_factions(self, game_key: str, guild_id: str | None = None) -> list[dict[str, Any]]:
        return [
            d
            for d in self._collection("factions").values()
            if d.get("game_key") == game_key and d.get("archived_at") is None
        ]

    async def find_ladder_activations(self, ladder_id: str) -> list[dict[str, Any]]:
        rows = [
            d
            for d in self._collection(MAP_POOL_HISTORY_COLLECTION).values()
            if d.get("ladder_id") == ladder_id
        ]
        return sorted(rows, key=lambda d: d["activated_at"])

    async def find_open_activations(self, map_pool_id: str) -> list[dict[str, Any]]:
        return [
            d
            for d in self._collection(MAP_POOL_HISTORY_COLLECTION).values()
            if d.get("map_pool_id") == map_pool_id and d.get("deactivated_at") is None
        ]


class FakeAudit:
    """In-memory audit recorder."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        self.lines.append((action, payload))


def _service() -> tuple[GameDataService, FakeGameDataDatabase, FakeAudit]:
    db, audit = FakeGameDataDatabase(), FakeAudit()
    return GameDataService(db, audit), db, audit


GAME = "aoe2"


@pytest.mark.asyncio
async def test_create_and_get_map() -> None:
    svc, _, audit = _service()
    entry = await svc.create_map(GAME, "Arabia", filename="arabia.v0", description="The classic")
    assert entry.id == f"map:{GAME}:Arabia"
    fetched = await svc.get_map(entry.id)
    assert fetched is not None and fetched.filename == "arabia.v0"
    assert ("map.create", {"game_key": GAME, "name": "Arabia"}) in audit.lines


@pytest.mark.asyncio
async def test_create_map_name_taken() -> None:
    svc, _, _ = _service()
    await svc.create_map(GAME, "Arabia", filename="arabia.v0")
    with pytest.raises(NameTakenError):
        await svc.create_map(GAME, "Arabia", filename="other.v0")


@pytest.mark.asyncio
async def test_archive_map_archival_only() -> None:
    svc, _, _ = _service()
    entry = await svc.create_map(GAME, "Gold Rush", filename="goldrush.v0")
    archived = await svc.archive_map(entry.id)
    assert archived.archived_at is not None
    with pytest.raises(ArchivedEntryError):
        await svc.archive_map(entry.id)
    assert await svc.list_maps(GAME) == []
    assert await svc.get_map(entry.id) is not None


@pytest.mark.asyncio
async def test_archived_name_can_be_recreated() -> None:
    svc, _, _ = _service()
    entry = await svc.create_map(GAME, "Arena", filename="arena.v0")
    await svc.archive_map(entry.id)
    recreated = await svc.create_map(GAME, "Arena", filename="arena.v1")
    assert recreated.id == entry.id
    assert recreated.archived_at is None


@pytest.mark.asyncio
async def test_civ_and_rule_crud() -> None:
    svc, _, _ = _service()
    faction = await svc.create_faction(GAME, "Franks", faction_key="franks")
    assert faction.faction_key == "franks"
    assert [c.name for c in await svc.list_factions(GAME)] == ["Franks"]
    await svc.archive_faction(faction.id)
    rule = await svc.create_rule(GAME, "1v1 RM", rule_key="rm_1v1", params={"team_size": "1"})
    fetched = await svc.get_rule(rule.id)
    assert fetched is not None and fetched.params == {"team_size": "1"}


@pytest.mark.asyncio
async def test_map_pack_validates_map_ids() -> None:
    svc, _, _ = _service()
    map_ = await svc.create_map(GAME, "Black Forest", filename="bf.v0")
    pack = await svc.create_map_pack(GAME, "Closed Maps", map_ids=(map_.id,))
    assert pack.map_ids == (map_.id,)
    with pytest.raises(ValueError, match="unknown map"):
        await svc.create_map_pack(GAME, "Broken", map_ids=("map:aoe2:Nowhere",))


@pytest.mark.asyncio
async def test_map_pool_requires_maps_and_duplicate() -> None:
    svc, _, _ = _service()
    with pytest.raises(ValueError, match="at least one"):
        await svc.create_map_pool(GAME, "Empty")
    map_ = await svc.create_map(GAME, "Nomad", filename="nomad.v0")
    pool = await svc.create_map_pool(GAME, "Open Pool", map_ids=(map_.id,))
    with pytest.raises(NameTakenError):
        await svc.create_map_pool(GAME, "Open Pool", map_ids=(map_.id,))
    copy = await svc.duplicate_map_pool(pool.id, "Open Pool Copy")
    assert copy.map_ids == pool.map_ids and copy.id != pool.id


@pytest.mark.asyncio
async def test_resolve_pool_map_ids_unions_packs_and_maps() -> None:
    svc, _, _ = _service()
    a = await svc.create_map(GAME, "MapA", filename="a.v0")
    b = await svc.create_map(GAME, "MapB", filename="b.v0")
    c = await svc.create_map(GAME, "MapC", filename="c.v0")
    pack = await svc.create_map_pack(GAME, "P1", map_ids=(a.id, b.id))
    pool = await svc.create_map_pool(GAME, "Mixed", map_ids=(a.id, c.id), map_pack_ids=(pack.id,))
    assert await svc.resolve_pool_map_ids(pool) == (a.id, c.id, b.id)


@pytest.mark.asyncio
async def test_activate_map_pool_transactional_history() -> None:
    svc, db, _ = _service()
    m1 = await svc.create_map(GAME, "Arabia", filename="arabia.v0")
    m2 = await svc.create_map(GAME, "Arena", filename="arena.v0")
    pool1 = await svc.create_map_pool(GAME, "Pool1", map_ids=(m1.id,))
    pool2 = await svc.create_map_pool(GAME, "Pool2", map_ids=(m2.id,))
    a1 = await svc.activate_map_pool("ladder:1", pool1.id)
    assert a1.deactivated_at is None
    a2 = await svc.activate_map_pool("ladder:1", pool2.id)
    history = await db.find_ladder_activations("ladder:1")
    assert len(history) == 2
    assert history[0]["deactivated_at"] is not None
    assert history[1]["deactivated_at"] is None
    assert history[1]["_id"] == a2.id


@pytest.mark.asyncio
async def test_activate_archived_pool_rejected() -> None:
    svc, _, _ = _service()
    m = await svc.create_map(GAME, "Islands", filename="islands.v0")
    pool = await svc.create_map_pool(GAME, "Pool", map_ids=(m.id,))
    await svc.archive_map_pool(pool.id)
    with pytest.raises(ArchivedEntryError):
        await svc.activate_map_pool("ladder:1", pool.id)


@pytest.mark.asyncio
async def test_archive_active_pool_rejected() -> None:
    svc, _, _ = _service()
    m = await svc.create_map(GAME, "Highway", filename="highway.v0")
    pool = await svc.create_map_pool(GAME, "Active", map_ids=(m.id,))
    await svc.activate_map_pool("ladder:1", pool.id)
    with pytest.raises(ActivePoolArchiveError):
        await svc.archive_map_pool(pool.id)


@pytest.mark.asyncio
async def test_archive_pool_after_deactivation_allowed() -> None:
    svc, _, _ = _service()
    m1 = await svc.create_map(GAME, "Oasis", filename="oasis.v0")
    m2 = await svc.create_map(GAME, "Serengeti", filename="serengeti.v0")
    pool1 = await svc.create_map_pool(GAME, "Old", map_ids=(m1.id,))
    pool2 = await svc.create_map_pool(GAME, "New", map_ids=(m2.id,))
    await svc.activate_map_pool("ladder:1", pool1.id)
    await svc.activate_map_pool("ladder:1", pool2.id)
    archived = await svc.archive_map_pool(pool1.id)
    assert archived.archived_at is not None


@pytest.mark.asyncio
async def test_seed_from_data_idempotent() -> None:
    svc, _, _ = _service()
    data = {
        "maps": [{"name": "Arabia", "filename": "arabia.v0"}, {"name": "Arena", "filename": "arena.v0"}],
        "civs": [{"name": "Franks", "faction_key": "franks"}],
        "rules": [{"name": "1v1 RM", "rule_key": "rm_1v1"}],
    }
    counts = await svc.seed_from_data(GAME, data)
    assert counts == {"maps": 2, "factions": 1, "rules": 1}
    again = await svc.seed_from_data(GAME, data)
    assert again == {"maps": 0, "factions": 0, "rules": 0}
    assert len(await svc.list_maps(GAME)) == 2


@pytest.mark.asyncio
async def test_audit_recorder_failure_never_raises() -> None:
    class BrokenAudit:
        async def record(self, action: str, payload: dict[str, Any]) -> None:
            raise RuntimeError("audit store down")

    svc, _, _ = _service()
    svc._audit = BrokenAudit()
    entry = await svc.create_map(GAME, "Steppe", filename="steppe.v0")
    assert entry.name == "Steppe"


def test_collections_constants() -> None:
    assert MAPS_COLLECTION == "maps"
    assert MAP_PACKS_COLLECTION == "map_packs"
    assert MAP_POOLS_COLLECTION == "map_pools"
    assert MAP_POOL_HISTORY_COLLECTION == "map_pool_history"
    assert ADMIN_AUDIT_COLLECTION == "admin_audit"


def test_map_pool_model_roundtrip() -> None:
    pool = MapPoolModel(_id="map_pool:aoe2:X", game_key="aoe2", name="X", map_ids=("a",), map_pack_ids=("p",))
    doc = pool.to_mongo()
    assert doc["_id"] == pool.id
    assert MapPoolModel.from_mongo(doc) == pool
