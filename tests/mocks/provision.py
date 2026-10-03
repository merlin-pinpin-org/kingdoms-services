"""Test helper: run the real core provisioning against a MockGuild.

kingdoms-services#175 — the salons-first structure is declared in
``config/mods/kingdoms.yaml`` and provisioned by the core ``ChannelService``
through ``DiscordChannelsPlatform``. These helpers wire the real services
(an in-memory database, cache and platform) so tests exercise the
production provisioning path, never a parallel fake of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.mod_definition import ModDefinition
from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions
from kingdoms.core.services.state import StateService
from kingdoms.discord.channels_platform import DiscordChannelsPlatform
from kingdoms.discord.kingdom_persistent import KingdomsPanelWiring, register_kingdoms_panel_wiring
from tests.mocks.discord_mock import MockClient, MockGuild
from tests.mocks.state_mock import FakeClock, InMemoryStateStore

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


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


def make_channel_service(guild: MockGuild) -> tuple[ChannelService, ModRegistry]:
    """Build a real ChannelService provisioning the in-memory guild."""
    registry = ModRegistry(load_mod_definitions(REPO_CONFIG_DIR))
    service = ChannelService(
        database=FakeChannelsDatabase(),
        platform=DiscordChannelsPlatform(MockClient(guilds=[guild])),
        cache=StateService(store=InMemoryStateStore(clock=FakeClock())),
        registry=registry,
    )
    return service, registry


def provisioned_wiring(guild: MockGuild, bot_admins: tuple[str, ...] = ()) -> KingdomsPanelWiring:
    """Register a panel wiring whose real ChannelService provisions the guild.

    ``provision_structure`` reads the wiring at call time; registering it
    here is what tests do instead of the bot factory's runtime wiring.
    """
    service, registry = make_channel_service(guild)
    wiring = KingdomsPanelWiring(bot_admins=tuple(bot_admins), channel_service=service, registry=registry)
    register_kingdoms_panel_wiring(wiring)
    return wiring


def kingdoms_definition() -> ModDefinition:
    """The kingdoms mod declaration from the repository's real config."""
    definitions: dict[str, ModDefinition] = load_mod_definitions(REPO_CONFIG_DIR)  # type: ignore[assignment]
    return definitions["kingdoms"]


def expected_group_names() -> dict[str, str]:
    """Map group key -> display name, straight from the declaration."""
    return {group.key: group.display_name for group in kingdoms_definition().channel_groups}


def expected_category_slugs() -> set[str]:
    """The declared groups (Discord categories), slug-normalized."""
    from kingdoms.discord.kingdom_setup import _slug

    return {_slug(name) for name in expected_group_names().values()}


def expected_channel_slugs() -> set[str]:
    """The declared channels as 'group-slug/channel-slug' paths."""
    from kingdoms.discord.kingdom_setup import _slug

    groups = expected_group_names()
    return {
        f"{_slug(groups[category.group])}/{_slug(category.display_name)}"
        for category in kingdoms_definition().channel_categories
    }


def all_guild_channels(guild: MockGuild) -> list[Any]:
    """Every text and forum channel of the mock guild."""
    return [*guild.text_channels, *guild.forums]
