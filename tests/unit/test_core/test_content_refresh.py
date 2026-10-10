"""Content refresh tests: catalog reconciliation with the vendored dataset."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kingdoms.core.games.aoe2.content_refresh import refresh_aoe2_content

DATASET_DIR = Path(__file__).resolve().parents[3] / "data" / "core" / "aoe2techtree"


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def replace_one(self, filter: Any, doc: dict[str, Any], upsert: bool = False) -> None:
        self.docs[str(doc["_id"])] = dict(doc)

    async def find_one(self, filter: dict[str, Any]) -> dict[str, Any] | None:
        if "_id" in filter:
            return self.docs.get(str(filter["_id"]))
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filter.items()):
                return doc
        return None


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


async def test_refresh_creates_missing_factions_and_content(monkeypatch: Any) -> None:
    from kingdoms.core.models import db as db_module

    database = FakeDatabase()
    monkeypatch.setattr(db_module, "get_async_database", lambda: database)

    counts = await refresh_aoe2_content(DATASET_DIR)

    assert counts["factions"] == 56
    assert counts["content_docs"] == 112
    factions = database.collections["factions"].docs
    assert "faction:aoe2:franks" in factions
    assert "faction:aoe2:dravidians" in factions
    content = database.collections["faction_content"].docs
    assert "faction:aoe2:franks:fr" in content
    assert content["faction:aoe2:franks:fr"]["name"] == "Francs"


async def test_refresh_is_idempotent(monkeypatch: Any) -> None:
    from kingdoms.core.models import db as db_module

    database = FakeDatabase()
    monkeypatch.setattr(db_module, "get_async_database", lambda: database)

    await refresh_aoe2_content(DATASET_DIR)
    counts = await refresh_aoe2_content(DATASET_DIR)

    assert counts["factions"] == 0
    assert counts["content_docs"] == 112
