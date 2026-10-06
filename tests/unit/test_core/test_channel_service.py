"""Unit tests for the ChannelService (kingdoms-services#5).

Covers the cache-aside resolution flow (cache -> database -> adoption ->
creation) for platform-level and mod-scoped categories, cache expiry and
stale-entry handling, and the per-mod provisioning loop.
"""

from __future__ import annotations

import pytest

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.mod_definition import ChannelCategoryDef, ModDefinition
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.state import StateService
from tests.mocks.state_mock import FakeClock, InMemoryStateStore

GUILD = "123456"


class FakeChannelsDatabase:
    """In-memory ChannelsDatabase: documents keyed by guild:category."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelModel] = {}
        self.upserts: list[ChannelModel] = []
        self.deletes: list[str] = []

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        return self.channels.get(f"{guild_id}:{category}")

    async def upsert_channel(self, channel: ChannelModel) -> None:
        self.channels[channel.id] = channel
        self.upserts.append(channel)

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        existed = f"{guild_id}:{category}" in self.channels
        if existed:
            self.deletes.append(f"{guild_id}:{category}")
            del self.channels[f"{guild_id}:{category}"]
        return existed


class FakeChannelsPlatform:
    """In-memory ChannelsPlatform: adoption set, live-channel tracking."""

    def __init__(self) -> None:
        self.next_id = 2000
        self.live: set[str] = set()
        self.adoptable: dict[str, str] = {}
        self.created: list[str] = []
        self.names: dict[str, str] = {}

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        return self.adoptable.get(name)

    async def create_channel(self, guild_id: str, name: str) -> str:
        channel_id = f"ch{self.next_id}"
        self.next_id += 1
        self.live.add(channel_id)
        self.created.append(name)
        self.names[channel_id] = name
        return channel_id

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        return channel_id in self.live


def make_service() -> tuple[ChannelService, FakeChannelsDatabase, FakeChannelsPlatform, InMemoryStateStore, FakeClock]:
    """Build a ChannelService with in-memory fakes and a real StateService."""
    db = FakeChannelsDatabase()
    platform = FakeChannelsPlatform()
    clock = FakeClock()
    store = InMemoryStateStore(clock=clock)
    registry = ModRegistry()
    registry.register(
        ModDefinition(
            name="example",
            channel_categories=(ChannelCategoryDef(key="announce", display_name="Annonces"),),
        )
    )
    service = ChannelService(
        database=db,
        platform=platform,
        cache=StateService(store),
        registry=registry,
    )
    return service, db, platform, store, clock


def channel_names(registry: ModRegistry) -> dict[str, str]:
    """Shorthand: the mod's declared categories as display names."""
    return {"example:announce": "Annonces"}


class TestPlatformCategories:
    async def test_creates_and_persists_a_platform_category(self) -> None:
        service, db, platform, _store, _clock = make_service()
        channel = await service.get_channel_for_category(GUILD, "logs")
        assert channel.id.startswith("ch")
        assert channel.name == "LOGS"
        assert db.channels[f"{GUILD}:logs"].channel_id == channel.id
        assert platform.created == ["LOGS"]

    async def test_second_resolution_hits_the_cache(self) -> None:
        service, db, platform, _store, _clock = make_service()
        first = await service.get_channel_for_category(GUILD, "logs")
        platform.created.clear()
        db.upserts.clear()
        second = await service.get_channel_for_category(GUILD, "logs")
        assert second.id == first.id
        assert platform.created == []
        assert db.upserts == []

    async def test_unknown_platform_category_fails_loudly(self) -> None:
        service, _db, _platform, _store, _clock = make_service()
        with pytest.raises(ValueError, match="logs2"):
            await service.get_channel_for_category(GUILD, "logs2")


class TestModCategories:
    async def test_resolves_a_mod_scoped_category(self) -> None:
        service, _db, platform, _store, _clock = make_service()
        channel = await service.get_channel_for_category(GUILD, "example:announce")
        assert channel.name == "Annonces"
        assert platform.created == ["Annonces"]

    async def test_unknown_mod_fails_loudly(self) -> None:
        service, _db, _platform, _store, _clock = make_service()
        with pytest.raises(KeyError):
            await service.get_channel_for_category(GUILD, "unknown:announce")

    async def test_undeclared_mod_category_fails_loudly(self) -> None:
        service, _db, _platform, _store, _clock = make_service()
        with pytest.raises(KeyError, match="does not declare"):
            await service.get_channel_for_category(GUILD, "example:undeclared")

    async def test_setup_mod_channels_provisions_every_declared_category(self) -> None:
        service, _db, platform, _store, _clock = make_service()
        channels = await service.setup_mod_channels(GUILD, "example")
        assert list(channels) == ["example:announce"]
        assert channels["example:announce"].name == "Annonces"
        assert platform.created == ["Annonces"]


class TestCacheAsideFlow:
    async def test_adopts_an_existing_channel_by_name(self) -> None:
        service, db, platform, _store, _clock = make_service()
        platform.live.add("ch-existing")
        platform.adoptable["LOGS"] = "ch-existing"
        channel = await service.get_channel_for_category(GUILD, "logs")
        assert channel.id == "ch-existing"
        assert db.channels[f"{GUILD}:logs"].channel_id == "ch-existing"

    async def test_cache_hit_skips_the_database(self) -> None:
        service, db, _platform, _store, _clock = make_service()
        first = await service.get_channel_for_category(GUILD, "logs")
        db.channels.clear()
        second = await service.get_channel_for_category(GUILD, "logs")
        assert second.id == first.id
        assert _store.values  # the cache entry exists

    async def test_cached_channel_deleted_on_platform_is_re_resolved(self) -> None:
        service, db, _platform, _store, _clock = make_service()
        first = await service.get_channel_for_category(GUILD, "logs")
        _platform.live.discard(first.id)
        second = await service.get_channel_for_category(GUILD, "logs")
        assert second.id != first.id
        assert db.channels[f"{GUILD}:logs"].channel_id == second.id

    async def test_stored_channel_deleted_on_platform_is_dropped_then_recreated(self) -> None:
        service, db, platform, _store, clock = make_service()
        first = await service.get_channel_for_category(GUILD, "logs")
        clock.advance(3600)
        platform.live.discard(first.id)
        second = await service.get_channel_for_category(GUILD, "logs")
        assert second.id != first.id
        assert f"{GUILD}:logs" in db.deletes
        assert db.channels[f"{GUILD}:logs"].channel_id == second.id
