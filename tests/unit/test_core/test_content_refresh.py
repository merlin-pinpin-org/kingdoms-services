"""Content refresh tests: catalog reconciliation through the provider seam."""

from __future__ import annotations

from typing import Any

from kingdoms.game_aoe2.content_refresh import refresh_aoe2_content

# The refresh rides the PROVIDER seam (no vendored data): the tests
# inject a fake source standing in for the aoe2techtree provider.
_FAKE_SOURCE = None


class _FakeSource:
    """A stand-in for the aoe2techtree provider over the seam."""

    def __init__(self) -> None:
        self._data = {
            "Franks": {
                "en": {"name": "Franks", "summary": "Cavalry civ", "image_url": "https://x/franks.png"},
                "fr": {"name": "Francs", "summary": "Civilisation de cavalerie", "image_url": "https://x/franks.png"},
            },
            "Dravidians": {
                "en": {"name": "Dravidians", "summary": "Elephant civ", "image_url": "https://x/dravidians.png"},
                "fr": {"name": "Dravidiens", "summary": "Civilisation d'elephants", "image_url": "https://x/dravidians.png"},
            },
        }

    async def list_factions(self) -> list[str]:
        return sorted(self._data)

    async def faction_content(self, key: str, locale: str) -> dict[str, Any] | None:
        entry = self._data.get(key, {}).get(locale)
        if entry is None:
            return None
        from kingdoms.game_aoe2.content_source import LocalizedContent

        return LocalizedContent(
            entity_id=f"faction:aoe2:{key.lower()}",
            locale=locale,
            name=entry["name"],
            summary=entry["summary"],
            source_url="https://github.com/SiegeEngineers/aoe2techtree",
            image_url=entry["image_url"],

        )


def _patch_source(monkeypatch: Any) -> None:
    import kingdoms.game_aoe2.content_source as source_module

    monkeypatch.setattr(
        source_module, "resolve_aoe2_content_source", lambda *a, **k: _FakeSource()
    )


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

    async def create_index(self, keys: Any) -> None:
        del keys


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


async def test_refresh_creates_missing_factions_and_content(monkeypatch: Any) -> None:
    from kingdoms.core.models import db as db_module

    database = FakeDatabase()
    monkeypatch.setattr(db_module, "get_async_database", lambda: database)

    _patch_source(monkeypatch)
    counts = await refresh_aoe2_content()

    assert counts["factions"] == 2
    assert counts["content_docs"] == 4
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

    _patch_source(monkeypatch)
    await refresh_aoe2_content()
    _patch_source(monkeypatch)
    counts = await refresh_aoe2_content()

    assert counts["factions"] == 0
    assert counts["content_docs"] == 4
