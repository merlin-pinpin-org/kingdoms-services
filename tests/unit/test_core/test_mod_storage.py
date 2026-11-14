"""Unit tests for the mods' storage facade (botforge-services#375, R8's seam)."""

from __future__ import annotations

from typing import Any

import pytest

from botforge.core.services.mod_storage import ModStateCache, ModStorage


class FakeCollection:
    """An in-memory collection handle."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def delete_many(self, query: dict[str, Any]) -> Any:
        """Delete every matching document; return the count result."""

        class _Result:
            deleted_count = len(self.docs)

        self.docs = {}
        return _Result()


class FakeDatabase:
    """An in-memory database keyed by collection id."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]

    async def list_collection_names(self) -> list[str]:
        """Return every existing collection id."""
        return sorted(self.collections)


class FakeState:
    """An in-memory state store."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        """Return one value (None when unset)."""
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set one value; the TTL is ignored by the fake."""
        self.store[key] = value
        return True

    async def delete(self, key: str) -> bool:
        """Delete one value; unknown keys are a no-op."""
        return self.store.pop(key, None) is not None

    async def increment(self, key: str, window: int | None = None) -> int:
        """Increment one counter."""
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value


def test_collections_are_namespaced_under_the_mod() -> None:
    """A mod's collection is prefixed with its id — no collision possible."""
    storage = ModStorage(FakeDatabase(), "kingdoms")
    assert storage.collection_id("realms") == "kingdoms:realms"
    handle = storage.collection("realms")
    assert handle is storage.collection("realms")


def test_collection_names_are_validated() -> None:
    """A name with a prefix attempt or a dot is refused loudly."""
    storage = ModStorage(FakeDatabase(), "kingdoms")
    with pytest.raises(ValueError, match="slug"):
        storage.collection("kingdoms:realms")
    with pytest.raises(ValueError, match="slug"):
        storage.collection("Not-A-Slug")


async def test_wipe_deletes_only_the_mods_namespace() -> None:
    """The per-mod wipe is surgical: the prefix, nothing else."""
    db = FakeDatabase()
    db["kingdoms:realms"].docs = {"a": {}, "b": {}}
    db["kingdoms:attacks"].docs = {"c": {}}
    db["ladder:matches"].docs = {"d": {}}
    db["channels"].docs = {"e": {}}
    storage = ModStorage(db, "kingdoms")
    assert await storage.wipe() == 3
    assert db["ladder:matches"].docs == {"d": {}}
    assert db["channels"].docs == {"e": {}}


class FakeStateService:
    """A StateService double over the fake store (scoped, JSON like the real one)."""

    def __init__(self, store: FakeState) -> None:
        self._store = store

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        """Read one scoped entry (None when unset)."""
        raw = self._store.store.get(f"{scope}:{key}")
        return {"value": raw} if raw is not None else None

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        """Write one scoped entry."""
        self._store.store[f"{scope}:{key}"] = str(value)
        return True

    async def delete_state(self, scope: str, key: str) -> bool:
        """Delete one scoped entry."""
        return self._store.store.pop(f"{scope}:{key}", None) is not None


async def test_state_cache_is_mod_scoped() -> None:
    """A mod's state entries ride the mod:<id>: scope — never the core's."""
    cache = ModStateCache(FakeStateService(FakeState()), "kingdoms")  # type: ignore[arg-type]
    assert cache.scope == "mod:kingdoms"
    await cache.set("beta", {"started": True})
    assert await cache.get("beta") == {"value": "{'started': True}"}
    assert await cache.delete("beta") is True
    assert await cache.get("beta") is None
