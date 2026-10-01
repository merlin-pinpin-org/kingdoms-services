"""Unit tests for the /kingdom salons-first bootstrap (kingdoms#138)."""

from __future__ import annotations

import discord
import pytest

from kingdoms.discord.kingdom_setup import (
    ADMIN_ONLY_CHANNELS,
    SALONS_FIRST_STRUCTURE,
    _slug,
    provision_structure,
    register_kingdom_command,
)
from tests.mocks.discord_mock import (
    MockGuild,
    MockInteraction,
    MockMember,
    MockTextChannel,
)


def _expected_categories() -> set[str]:
    return {name for name, _, _ in SALONS_FIRST_STRUCTURE}


def _expected_channels() -> set[str]:
    return {f"{category}/{channel}" for category, channels, _ in SALONS_FIRST_STRUCTURE for channel, _ in channels}


def _all_guild_channels(guild: MockGuild) -> list[MockTextChannel]:
    return [*guild.text_channels, *guild.forums]


@pytest.mark.asyncio
async def test_provision_creates_the_validated_structure() -> None:
    """A fresh guild gets every category and channel, in the designer's order."""
    guild = MockGuild(id=1)
    created, adopted = await provision_structure(guild)
    assert {c.name for c in guild.categories} == _expected_categories()
    assert {f"{c.category.name}/{c.name}" for c in _all_guild_channels(guild)} == _expected_channels()
    assert created and not adopted


@pytest.mark.asyncio
async def test_provision_creates_forums_and_announce_channels() -> None:
    """Règles/Suggestion/Support are forums; admin-written channels deny sends."""
    guild = MockGuild(id=1)
    await provision_structure(guild)
    forum_names = {c.name for c in guild.forums}
    assert {"Règles", "Suggestion", "Question", "Signaler un Bug"} <= forum_names
    annonce = next(c for c in guild.text_channels if c.name == "Annonce")
    overwrite = annonce.creation_overwrite_for(guild.default_role)
    assert overwrite is not None and overwrite.send_messages is False and overwrite.view_channel is True


@pytest.mark.asyncio
async def test_provision_orders_categories_by_structure() -> None:
    """Profils first, Général second, Support last."""
    guild = MockGuild(id=1)
    await provision_structure(guild)
    positions = {c.name: c.position for c in guild.categories}
    assert positions["Profils"] == 0
    assert positions["Général"] == 1
    assert max(positions.values()) == positions["Support"]


@pytest.mark.asyncio
async def test_provision_is_idempotent() -> None:
    """Re-running the bootstrap adopts the existing structure, no duplicates."""
    guild = MockGuild(id=1)
    await provision_structure(guild)
    created, adopted = await provision_structure(guild)
    assert not created
    assert set(adopted) == _expected_categories() | _expected_channels()
    assert {f"{c.category.name}/{c.name}" for c in _all_guild_channels(guild)} == _expected_channels()


@pytest.mark.asyncio
async def test_provision_never_duplicates_after_three_runs() -> None:
    """Regression: 'Âge sombre' (accent + space) used to be re-created on each run."""
    guild = MockGuild(id=1)
    for _ in range(3):
        await provision_structure(guild)
    names = [c.name for c in _all_guild_channels(guild)]
    assert names.count("Âge sombre") == 1
    for _category, channels, _ in SALONS_FIRST_STRUCTURE:
        for channel, _kind in channels:
            assert names.count(channel) == 1, f"{channel} duplicated"


def test_slug_normalizes_like_discord() -> None:
    assert _slug("Âge sombre") == "age-sombre"
    assert _slug("Époque") == "epoque"


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
    for channel in _all_guild_channels(guild):
        assert isinstance(channel, MockTextChannel)


def test_structure_includes_profils_general_and_support() -> None:
    """The validated v2 structure: Profils first, Général second, Support last."""
    names = [name for name, _, _ in SALONS_FIRST_STRUCTURE]
    assert names[0] == "Profils"
    assert names[1] == "Général"
    assert names[-1] == "Support"
    general = next(entry for entry in SALONS_FIRST_STRUCTURE if entry[0] == "Général")
    general_names = [channel for channel, _ in general[1]]
    assert general_names == [
        "Présentation",
        "Annonce",
        "Règles",
        "Paramètre Saison II",
        "Update",
        "Taverne",
        "Suggestion",
    ]
    kinds = dict(general[1])
    assert kinds["Règles"] == "forum"
    assert kinds["Update"] == "announce"
