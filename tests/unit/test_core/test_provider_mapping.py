"""ProviderMappingService tests: resolve fallback, update, cache-aside."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.provider_mapping import (
    ProviderMappingError,
    ProviderMappingService,
    parse_mapping_lines,
)


class FakeMappingDB:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def get_mapping(self, provider: str) -> dict[str, Any] | None:
        return self.docs.get(provider)

    async def upsert_mapping(self, document: dict[str, Any]) -> None:
        self.docs[str(document["provider"])] = {k: v for k, v in document.items() if k != "_id"}


class FakeState:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        return self.store.get(f"{scope}:{key}")

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        assert ttl is not None
        self.store[f"{scope}:{key}"] = dict(value)
        return True

    async def delete_state(self, scope: str, key: str) -> bool:
        self.deleted.append(f"{scope}:{key}")
        return self.store.pop(f"{scope}:{key}", None) is not None


async def test_resolve_falls_back_to_identity() -> None:
    service = ProviderMappingService(FakeMappingDB())
    assert await service.resolve("aoe2techtree", "factions", "Franks") == "Franks"


async def test_update_then_resolve() -> None:
    service = ProviderMappingService(FakeMappingDB())
    await service.update_kind("aoe2techtree", "factions", {"Franks": "franks_id"})
    assert await service.resolve("aoe2techtree", "factions", "Franks") == "franks_id"


async def test_update_invalidates_cache() -> None:
    state = FakeState()
    service = ProviderMappingService(FakeMappingDB(), state)
    await service.get("aoe2techtree")
    await service.update_kind("aoe2techtree", "maps", {"Arabia": "arabia_rms"})
    assert "provider_mappings:aoe2techtree" in state.deleted


async def test_unknown_kind_is_refused() -> None:
    service = ProviderMappingService(FakeMappingDB())
    try:
        await service.update_kind("aoe2techtree", "units", {})
        raise AssertionError("expected ProviderMappingError")
    except ProviderMappingError:
        pass


def test_parse_mapping_lines() -> None:
    assert parse_mapping_lines(["Franks=1", " Goths = 2 ", ""]) == {"Franks": "1", "Goths": "2"}
    try:
        parse_mapping_lines(["Franks 1"])
        raise AssertionError("expected ProviderMappingError")
    except ProviderMappingError:
        pass
