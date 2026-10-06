"""Unit tests for the /kingdom bootstrap (kingdoms#138).

These tests run the real core provisioning path (declaration ->
ChannelService -> platform) against the in-memory mock guild, and
assert what the declaration says, never a parallel hardcoded copy of
it.
"""

from __future__ import annotations

import discord
import pytest

from kingdoms.discord.kingdom_setup import (
    MOD_NAME,
    _slug,
    provision_structure,
    register_kingdom_command,
)
from tests.mocks.discord_mock import (
    MockGuild,
    MockInteraction,
    MockMember,
)
from tests.mocks.provision import (
    all_guild_channels,
    expected_channel_slugs,
    kingdoms_definition,
    provisioned_wiring,
)


def test_declaration_lists_the_channels() -> None:
    """The declaration carries the mod's channels."""
    categories = list(kingdoms_definition().channel_categories)
    keys = {category.key for category in categories}
    assert "presentation" in keys
    assert "announce" in keys
    assert "rules" in keys


def test_slug_normalizes_like_discord() -> None:
    assert _slug("Âge sombre") == "age-sombre"
    assert _slug("Époque") == "epoque"


@pytest.mark.asyncio
async def test_provision_creates_the_declared_channels() -> None:
    """A fresh guild gets every declared channel, nothing else."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    created, _ = await provision_structure(guild)
    names = {_slug(channel.name) for channel in all_guild_channels(guild)}
    assert expected_channel_slugs() <= names
    assert created


@pytest.mark.asyncio
async def test_provision_is_idempotent() -> None:
    """Re-running the bootstrap adopts the existing channels, no duplicates."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    await provision_structure(guild)
    names = [channel.name for channel in all_guild_channels(guild)]
    for category in kingdoms_definition().channel_categories:
        assert names.count(_slug(category.display_name)) == 1, f"{category.display_name} duplicated"


@pytest.mark.asyncio
async def test_provision_never_duplicates_after_three_runs() -> None:
    """Regression: 'Âge sombre' (accent + space) must not be re-created."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    for _ in range(3):
        await provision_structure(guild)
    names = [channel.name for channel in all_guild_channels(guild)]
    assert names.count("age-sombre") == 1
    for category in kingdoms_definition().channel_categories:
        assert names.count(_slug(category.display_name)) == 1, f"{category.display_name} duplicated"


@pytest.mark.asyncio
async def test_provision_without_channel_service_fails_loudly() -> None:
    """No wired ChannelService means an explicit error, never silent success."""
    from kingdoms.discord.kingdom_persistent import KingdomsPanelWiring, register_kingdoms_panel_wiring

    register_kingdoms_panel_wiring(KingdomsPanelWiring())
    guild = MockGuild(id=1)
    with pytest.raises(RuntimeError, match="ChannelService is not wired"):
        await provision_structure(guild)


@pytest.mark.asyncio
async def test_register_kingdom_command_adds_command_to_tree() -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_kingdom_command(tree)
    assert "kingdom" in {command.name for command in tree.get_commands()}
    await client.close()


@pytest.mark.asyncio
async def test_kingdom_command_denies_non_admin() -> None:
    """A non-admin outside BOT_ADMINS gets an ephemeral denial."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_kingdom_command(tree, bot_admins=("111111111",))
    command = next(c for c in tree.get_commands() if c.name == "kingdom")
    stranger = MockMember(name="stranger", guild=MockGuild(id=42))
    interaction = MockInteraction(user=stranger, guild=MockGuild(id=42))
    interaction.guild_id = 42
    await command._callback(interaction)  # type: ignore[union-attr]
    assert interaction.response.sent is True
    assert interaction.response.ephemeral is True
    await client.close()


@pytest.mark.asyncio
async def test_kingdom_command_bootstraps_for_bot_admin() -> None:
    """A BOT_ADMINS member gets the provisioning report."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_kingdom_command(tree, bot_admins=("111111111",))
    command = next(c for c in tree.get_commands() if c.name == "kingdom")
    guild = MockGuild(id=42)
    provisioned_wiring(guild, bot_admins=("111111111",))
    member = MockMember(id=111111111, name="op", guild=guild)
    interaction = MockInteraction(user=member, guild=guild)
    interaction.guild_id = 42
    await command._callback(interaction)  # type: ignore[union-attr]
    names = {_slug(channel.name) for channel in all_guild_channels(guild)}
    assert expected_channel_slugs() <= names
    assert interaction.followup.messages, "the bootstrap report must be answered"
    await client.close()


def test_mod_name_matches_the_declaration() -> None:
    assert MOD_NAME == kingdoms_definition().name
