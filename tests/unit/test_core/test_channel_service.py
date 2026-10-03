"""Unit tests for the ChannelService (kingdoms-services#5).

Covers the cache-aside resolution flow (cache -> database -> adoption ->
creation) for platform-level and mod-scoped categories, cache expiry and
stale-entry handling, and the per-mod provisioning loop.
"""

from __future__ import annotations

import pytest

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.mod_definition import ChannelCategoryDef, ChannelGroupDef, ModDefinition, RoleDef
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


# ---------------------------------------------------------------------------
# Structured provisioning (kingdoms-services#175): channel groups, kinds,
# admin-only flags and adopt policies, all read from the declaration.
# ---------------------------------------------------------------------------


class FakeStructuredPlatform(FakeChannelsPlatform):
    """Structured seam: channel groups (categories) and kinds, in memory."""

    def __init__(self) -> None:
        super().__init__()
        self.group_seq = 100
        self.groups: dict[str, str] = {}
        self.group_names: dict[str, str] = {}
        self.channel_group: dict[str, str] = {}
        self.channel_kind: dict[str, str] = {}
        self.created_groups: list[tuple[str, int, bool]] = []
        self.created_of_kind: list[tuple[str, str, str, bool, int]] = []

    async def find_group_by_name(self, guild_id: str, name: str) -> str | None:
        return self.groups.get(name)

    async def create_group(self, guild_id: str, name: str, position: int, admin_only: bool) -> str:
        group_id = f"grp{self.group_seq}"
        self.group_seq += 1
        self.groups[name] = group_id
        self.group_names[group_id] = name
        self.created_groups.append((name, position, admin_only))
        self.live.add(group_id)
        return group_id

    async def find_channel_of_kind(
        self, guild_id: str, name: str, kind: str, group_id: str | None
    ) -> str | None:
        for channel_id, channel_name in self.names.items():
            if (
                self.channel_kind.get(channel_id) == kind
                and self.channel_group.get(channel_id) == (group_id or "")
                and channel_name == name
            ):
                return channel_id
        return None

    async def create_channel_of_kind(
        self,
        guild_id: str,
        name: str,
        kind: str,
        group_id: str | None,
        admin_only: bool,
        position: int,
    ) -> str:
        channel_id = f"ch{self.next_id}"
        self.next_id += 1
        self.live.add(channel_id)
        self.names[channel_id] = name
        self.channel_group[channel_id] = group_id or ""
        self.channel_kind[channel_id] = kind
        self.created_of_kind.append((name, kind, group_id or "", admin_only, position))
        return channel_id

    async def single_channel_in_group(self, guild_id: str, group_id: str, kind: str) -> str | None:
        for channel_id, member_of in self.channel_group.items():
            if member_of == group_id and self.channel_kind.get(channel_id) == kind:
                return channel_id
        return None


def make_structured_service() -> (
    tuple[ChannelService, FakeChannelsDatabase, FakeStructuredPlatform, ModRegistry]
):
    """A ChannelService over the structured seam, with a grouped declaration."""
    db = FakeChannelsDatabase()
    platform = FakeStructuredPlatform()
    registry = ModRegistry()
    registry.register(
        ModDefinition(
            name="example",
            channel_groups=(
                ChannelGroupDef(key="main", display_name="Salons"),
                ChannelGroupDef(key="admin", display_name="Admin", admin_only=True),
            ),
            channel_categories=(
                ChannelCategoryDef(key="announce", display_name="Annonces", group="main", kind="announce"),
                ChannelCategoryDef(key="rules", display_name="Règles", group="main", kind="forum"),
                ChannelCategoryDef(key="epoch", display_name="Âge sombre", group="main", adopt="group_single"),
                ChannelCategoryDef(key="requests", display_name="Demandes", group="admin", admin_only=True),
            ),
            roles=(RoleDef(key="member", display_name="Example Member"),),
        )
    )
    service = ChannelService(
        database=db,
        platform=platform,
        cache=StateService(store=InMemoryStateStore(clock=FakeClock())),
        registry=registry,
    )
    return service, db, platform, registry


@pytest.mark.asyncio
async def test_provision_mod_channels_creates_groups_then_channels() -> None:
    """Groups come first (declaration order), channels in their group."""
    service, _, platform, _ = make_structured_service()
    report = await service.provision_mod_channels(GUILD, "example")

    assert [name for name, _, _ in platform.created_groups] == ["Salons", "Admin"]
    assert platform.created_groups[1] == ("Admin", 1, True)
    kinds = {
        (name, kind): (group, admin_only, position)
        for name, kind, group, admin_only, position in platform.created_of_kind
    }
    assert kinds[("Annonces", "announce")] == ("grp100", False, 0)
    assert kinds[("Règles", "forum")] == ("grp100", False, 1)
    assert kinds[("Demandes", "text")] == ("grp101", True, 0)
    assert set(report.group_ids) == {"main", "admin"}
    assert set(report.channel_ids) == {"announce", "rules", "epoch", "requests"}
    assert report.created and not report.adopted
    assert "Salons/Annonces" in report.created and "Admin/Demandes" in report.created


@pytest.mark.asyncio
async def test_provision_mod_channels_is_idempotent() -> None:
    """A second pass adopts everything; nothing new is created."""
    service, _, platform, _ = make_structured_service()
    first = await service.provision_mod_channels(GUILD, "example")
    created_before = len(platform.created_of_kind)
    second = await service.provision_mod_channels(GUILD, "example")

    assert not second.created
    assert set(second.adopted) == set(first.created) | set(first.adopted)
    assert len(platform.created_of_kind) == created_before
    assert second.channel_ids == first.channel_ids


@pytest.mark.asyncio
async def test_provision_mod_channels_adopts_existing_group_and_channel_by_name() -> None:
    """Existing structures are adopted by slug-normalized name, not duplicated."""
    service, _, platform, _ = make_structured_service()
    platform.groups["Salons"] = "grp7"
    platform.group_names["grp7"] = "Salons"
    platform.live.add("grp7")
    existing = await platform.create_channel_of_kind(GUILD, "Annonces", "announce", "grp7", False, 0)

    report = await service.provision_mod_channels(GUILD, "example")

    assert report.group_ids["main"] == "grp7"
    assert report.channel_ids["announce"] == existing
    assert "Salons" in report.adopted and "Salons/Annonces" in report.adopted
    assert ("Salons", 0, False) not in platform.created_groups


@pytest.mark.asyncio
async def test_provision_mod_channels_adopts_group_single_channel_whatever_its_name() -> None:
    """A renameable singleton is adopted by its group, never by its name."""
    service, _, platform, _ = make_structured_service()
    platform.groups["Salons"] = "grp9"
    platform.group_names["grp9"] = "Salons"
    platform.live.add("grp9")
    renamed = await platform.create_channel_of_kind(GUILD, "Âge féodal", "text", "grp9", False, 0)

    report = await service.provision_mod_channels(GUILD, "example")

    assert report.channel_ids["epoch"] == renamed
    assert "Salons/Âge sombre" in report.adopted


@pytest.mark.asyncio
async def test_provision_mod_channels_falls_back_to_the_legacy_seam() -> None:
    """A legacy (non-structured) platform still provisions channels flatly."""
    db = FakeChannelsDatabase()
    platform = FakeChannelsPlatform()
    registry = ModRegistry()
    registry.register(
        ModDefinition(
            name="example",
            channel_categories=(
                ChannelCategoryDef(key="announce", display_name="Annonces", group="main"),
            ),
            channel_groups=(ChannelGroupDef(key="main", display_name="Salons"),),
        )
    )
    service = ChannelService(
        database=db,
        platform=platform,
        cache=StateService(store=InMemoryStateStore(clock=FakeClock())),
        registry=registry,
    )
    report = await service.provision_mod_channels(GUILD, "example")
    assert "Salons" in report.created
    assert "Annonces" in report.created
    assert platform.created == ["Salons", "Annonces"]
