"""Unit tests for AoE2 seeding (#137): idempotence and wiring."""

from __future__ import annotations

from typing import Any

from kingdoms.core.games.aoe2.seed import seed_aoe2


class FakeDatabase:
    """In-memory stand-in for the async MongoDB adapter."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, name)


class _FakeCollection:
    def __init__(self, db: FakeDatabase, name: str) -> None:
        self._db = db
        self._name = name

    def _docs(self) -> dict[str, dict[str, Any]]:
        return self._db.collections.setdefault(self._name, {})

    async def replace_one(self, filt: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        self._docs()[doc["_id"]] = doc

    async def find_one(self, filt: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self._docs().values():
            if all(doc.get(k) == v for k, v in filt.items()):
                return doc
        return None

    def find(self, filt: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor([d for d in self._docs().values() if all(d.get(k) == v for k, v in filt.items())])


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, key: str, direction: int) -> _FakeCursor:
        return _FakeCursor(sorted(self._docs, key=lambda d: d.get(key, 0), reverse=direction < 0))

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as err:
            raise StopAsyncIteration from err


def _data() -> dict[str, Any]:
    return {
        "game_key": "aoe2",
        "maps": [{"name": "Arabia", "filename": "arabia", "description": "Classic"}],
        "civs": [{"name": "Franks", "faction_key": "franks"}],
        "map_pools": [{"name": "Season 1", "description": "First pool", "maps": ["Arabia"]}],
        "ladders": [
            {
                "owner_ref": "guild:default",
                "name": "AoE2 Community Ladder",
                "map_pool": "Season 1",
                "season": {"name": "Season 1", "start_in_days": 0, "duration_days": 90, "reset_ratings": False},
            }
        ],
    }


async def test_seed_creates_catalog_pool_ladder_season() -> None:
    db = FakeDatabase()
    result = await seed_aoe2(db, _data(), now=1_000_000)
    assert result["maps"] == 1
    assert result["civs"] == 1
    assert result["map_pools"] == 1
    assert result["ladders"] == 1
    assert result["seasons"] == 1
    assert "map:aoe2:Arabia" in db.collections["maps"]
    assert "map_pool:aoe2:Season 1" in db.collections["map_pools"]
    assert "ladder:aoe2:guild:default" in db.collections["ladders"]
    assert "season:ladder:aoe2:guild:default:Season 1" in db.collections["seasons"]
    ladder = db.collections["ladders"]["ladder:aoe2:guild:default"]
    assert ladder["active_map_pool_id"] == "map_pool:aoe2:Season 1"


async def test_seed_is_idempotent() -> None:
    db = FakeDatabase()
    first = await seed_aoe2(db, _data(), now=1_000_000)
    second = await seed_aoe2(db, _data(), now=2_000_000)
    assert first["maps"] == 1 and second["maps"] == 0
    assert first["map_pools"] == 1 and second["map_pools"] == 0
    assert first["ladders"] == 1 and second["ladders"] == 0
    assert first["seasons"] == 1 and second["seasons"] == 0
    assert len(db.collections["map_pool_history"]) == 1


async def test_seed_yaml_file_is_valid() -> None:
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[3] / "config" / "games" / "aoe2" / "seed.yaml"
    data = yaml.safe_load(path.read_text())
    assert data["game_key"] == "aoe2"
    assert len(data["maps"]) >= 8
    civs = {c["name"] for c in data["civs"]}
    assert len(civs) == len(data["civs"])
    assert "Franks" in civs
