"""Structured provisioning tests: channel groups, kinds and adopt policies.

Regression guard for the declaration-driven salons-first structure
(kingdoms-services#175): a mod's channels must provision inside their
declared Discord categories — never flat at the guild root.
"""

from __future__ import annotations

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.mod_definition import (
    ChannelCategoryDef,
    ChannelGroupDef,
    ModDefinition,
    RoleDef,
)
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.state import StateService
from tests.mocks.state_mock import FakeClock, InMemoryStateStore

GUILD = "123456"


class FakeChannelsDatabase:
    """In-memory ChannelsDatabase: documents keyed by guild:category."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelModel] = {}

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        return self.channels.get(f"{guild_id}:{category}")

    async def upsert_channel(self, channel: ChannelModel) -> None:
        self.channels[channel.id] = channel

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        return self.channels.pop(f"{guild_id}:{category}", None) is not None


class FakeStructuredPlatform:
    """In-memory ChannelsPlatform + StructuredChannelsPlatform seam."""

    def __init__(self) -> None:
        self.next_id = 3000
        self.live: set[str] = set()
        self.groups: dict[str, str] = {}  # name -> id
        self.channels: dict[str, tuple[str, str, str | None]] = {}  # id -> (name, kind, group_id)
        self.access_policies: list[str] = []

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        for channel_id, (chan_name, _kind, _group) in self.channels.items():
            if chan_name == name and _group is None:
                return channel_id
        return None

    async def create_channel(self, guild_id: str, name: str) -> str:
        channel_id = f"ch{self.next_id}"
        self.next_id += 1
        self.live.add(channel_id)
        self.channels[channel_id] = (name, "text", None)
        return channel_id

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        return channel_id in self.live or channel_id in self.groups.values()

    async def apply_access_policy(self, guild_id: str, channel_id: str, policy: dict[str, object]) -> None:
        self.access_policies.append(channel_id)

    async def get_channel_overwrites(self, guild_id: str, channel_id: str) -> dict[str, dict[str, bool]] | None:
        return None

    async def find_group_by_name(self, guild_id: str, name: str) -> str | None:
        return self.groups.get(name)

    async def create_group(self, guild_id: str, name: str, position: int, admin_only: bool) -> str:
        group_id = f"gr{self.next_id}"
        self.next_id += 1
        self.groups[name] = group_id
        return group_id

    async def find_channel_of_kind(
        self, guild_id: str, name: str, kind: str, group_id: str | None
    ) -> str | None:
        for channel_id, (chan_name, chan_kind, chan_group) in self.channels.items():
            if chan_name == name and chan_kind == kind and chan_group == group_id:
                return channel_id
        return None

    async def create_channel_of_kind(
        self, guild_id: str, name: str, kind: str, group_id: str | None, admin_only: bool, position: int
    ) -> str:
        channel_id = f"ch{self.next_id}"
        self.next_id += 1
        self.live.add(channel_id)
        self.channels[channel_id] = (name, kind, group_id)
        return channel_id

    async def single_channel_in_group(self, guild_id: str, group_id: str, kind: str) -> str | None:
        found: str | None = None
        for channel_id, (_name, chan_kind, chan_group) in self.channels.items():
            if chan_kind == kind and chan_group == group_id:
                if found is not None:
                    return None
                found = channel_id
        return found


def make_service() -> tuple[ChannelService, FakeStructuredPlatform, FakeChannelsDatabase]:
    """Build a ChannelService over a structured in-memory platform."""
    db = FakeChannelsDatabase()
    platform = FakeStructuredPlatform()
    registry = ModRegistry()
    registry.register(
        ModDefinition(
            name="example",
            seasonal=True,
            channel_groups=(
                ChannelGroupDef(key="general", display_name="Général", position=0),
                ChannelGroupDef(key="admin", display_name="Admin", admin_only=True, position=1),
            ),
            channel_categories=(
                ChannelCategoryDef(key="tavern", display_name="Taverne", group="general"),
                ChannelCategoryDef(
                    key="rules", display_name="Règles", group="general", kind="forum"
                ),
                ChannelCategoryDef(key="settings", display_name="Paramètres", group="admin"),
                ChannelCategoryDef(
                    key="epoch",
                    display_name="Âge sombre",
                    group="general",
                    kind="announce",
                    adopt="group_single",
                ),
            ),
            roles=(RoleDef(key="member", display_name="Example Member"),),
        )
    )
    service = ChannelService(
        database=db,
        platform=platform,  # type: ignore[arg-type]
        cache=StateService(InMemoryStateStore(clock=FakeClock())),
        registry=registry,
    )
    return service, platform, db


class TestStructuredProvisioning:
    async def test_provision_creates_groups_then_channels_inside(self) -> None:
        service, platform, _db = make_service()
        report = await service.provision_mod_channels(GUILD, "example")
        assert set(platform.groups) == {"Général", "Admin"}
        for _channel_id, (name, _kind, group_id) in platform.channels.items():
            assert group_id is not None, f"channel {name} provisioned outside a category"
            assert group_id in platform.groups.values()
        assert report.created and not report.adopted
        assert "Général/Taverne" in report.created

    async def test_channels_resolve_in_their_declared_group(self) -> None:
        service, platform, _db = make_service()
        channel = await service.get_channel_for_category(GUILD, "example:settings")
        _, _name, group_id = platform.channels[channel.id]
        assert group_id == platform.groups["Admin"]

    async def test_kind_is_honored_at_creation(self) -> None:
        service, platform, _db = make_service()
        await service.provision_mod_channels(GUILD, "example")
        kinds = {name: kind for name, kind, _ in platform.channels.values()}
        assert kinds["Règles"] == "forum"
        assert kinds["Taverne"] == "text"

    async def test_group_single_adopts_the_renamed_epoch_channel(self) -> None:
        service, platform, db = make_service()
        await service.provision_mod_channels(GUILD, "example")
        # Simulate an age switch: the epoch channel is renamed.
        epoch_id = db.channels[f"{GUILD}:example:epoch"].channel_id
        _old_name, kind, group_id = platform.channels[epoch_id]
        platform.channels[epoch_id] = ("Âge féodal", kind, group_id)
        del db.channels[f"{GUILD}:example:epoch"]
        channel = await service.get_channel_for_category(GUILD, "example:epoch")
        assert channel.id == epoch_id  # adopted by group, whatever its name

    async def test_setup_mod_channels_provisions_groups_too(self) -> None:
        service, platform, _db = make_service()
        await service.setup_mod_channels(GUILD, "example")
        assert set(platform.groups) == {"Général", "Admin"}

    async def test_rerun_is_idempotent_no_duplicates(self) -> None:
        service, platform, _db = make_service()
        first = await service.provision_mod_channels(GUILD, "example")
        second = await service.provision_mod_channels(GUILD, "example")
        assert not second.created
        assert len(platform.channels) == 4
        assert len(platform.groups) == 2
        assert first.created
