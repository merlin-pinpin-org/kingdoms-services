"""Unit tests for the /kingdom salons-first bootstrap (kingdoms#138)."""

from __future__ import annotations

import discord
import pytest

from kingdoms.discord.kingdom_setup import (
    ADMIN_ONLY_CHANNELS,
    SALONS_FIRST_STRUCTURE,
    provision_structure,
    register_kingdom_command,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockMember, MockTextChannel


def _expected_categories() -> set[str]:
    return {name for name, _, _ in SALONS_FIRST_STRUCTURE}


def _expected_channels() -> set[str]:
    return {f"{category}/{channel}" for category, channels, _ in SALONS_FIRST_STRUCTURE for channel in channels}


@pytest.mark.asyncio
async def test_provision_creates_the_validated_structure() -> None:
    """A fresh guild gets every category and channel, in the designer's order."""
    guild = MockGuild(id=1)
    created, adopted = await provision_structure(guild)

    assert {c.name for c in guild.categories} == _expected_categories()
    assert {f"{c.category.name}/{c.name}" for c in guild.text_channels} == _expected_channels()
    assert created and not adopted


@pytest.mark.asyncio
async def test_provision_is_idempotent() -> None:
    """Re-running the bootstrap adopts the existing structure, no duplicates."""
    guild = MockGuild(id=1)
    await provision_structure(guild)
    created, adopted = await provision_structure(guild)

    assert not created
    assert set(adopted) == _expected_categories() | _expected_channels()
    assert {f"{c.category.name}/{c.name}" for c in guild.text_channels} == _expected_channels()


@pytest.mark.asyncio
async def test_admin_only_channels_deny_everyone() -> None:
    """Candidatures and the Admin category deny @everyone at creation."""
    guild = MockGuild(id=1)
    await provision_structure(guild)

    candidatures = next(c for c in guild.text_channels if c.name == "Candidatures")
    assert candidatures.creation_overwrite_for(guild.default_role) is not None
    admin_channels = [c for c in guild.text_channels if c.category.name == "Admin"]
    assert {c.name for c in admin_channels} == {"Paramètres", "Demandes"}
    for channel in admin_channels:
        assert channel.creation_overwrite_for(guild.default_role) is not None


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
    member = MockMember(id=111111111, name="op", guild=guild)
    interaction = MockInteraction(user=member, guild=guild)
    interaction.guild_id = 42
    await command._callback(interaction)  # type: ignore[union-attr]

    assert {c.name for c in guild.categories} == _expected_categories()
    assert interaction.followup.messages, "the bootstrap report must be answered"
    await client.close()


@pytest.mark.parametrize(
    "channel_name",
    sorted(ADMIN_ONLY_CHANNELS),
)
def test_only_candidatures_is_admin_only_channel(channel_name: str) -> None:
    assert ADMIN_ONLY_CHANNELS == {"Candidatures"}


@pytest.mark.asyncio
async def test_provision_passes_dict_overwrites_to_discord() -> None:
    """Regression: discord.py raises TypeError when overwrites is None."""
    guild = MockGuild(id=77)
    created, _ = await provision_structure(guild)
    assert created, "the bootstrap must provision channels"
    for channel in guild.text_channels:
        assert isinstance(channel, MockTextChannel)


def test_structure_includes_profils_category_and_admin_demandes() -> None:
    """The validated v2 structure adds Profils and the admin Demandes channel."""
    names = {name for name, _, _ in SALONS_FIRST_STRUCTURE}
    assert "Profils" in names
    admin = next(entry for entry in SALONS_FIRST_STRUCTURE if entry[0] == "Admin")
    assert "Demandes" in admin[1]
