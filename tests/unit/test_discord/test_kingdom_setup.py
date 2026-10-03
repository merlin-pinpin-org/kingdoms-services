"""Unit tests for the /kingdom salons-first bootstrap (kingdoms#138).

kingdoms-services#175 — the structure is data: these tests run the real
core provisioning path (declaration -> ChannelService -> platform) against
the in-memory mock guild, and assert what the declaration says, never a
parallel hardcoded copy of it.
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
    expected_category_slugs,
    expected_channel_slugs,
    expected_group_names,
    kingdoms_definition,
    provisioned_wiring,
)


def test_declaration_is_the_validated_structure() -> None:
    """The declaration carries the validated v2 structure and kinds."""
    groups = list(kingdoms_definition().channel_groups)
    keys = [group.key for group in groups]
    assert keys[0] == "profiles"
    assert keys[1] == "general"
    assert keys[-1] == "support"
    admin = next(group for group in groups if group.key == "admin")
    assert admin.admin_only is True
    kinds = {category.key: category.kind for category in kingdoms_definition().channel_categories}
    assert kinds["rules"] == "forum"
    assert kinds["update"] == "announce"
    assert kinds["presentation"] == "announce"


def test_slug_normalizes_like_discord() -> None:
    assert _slug("Âge sombre") == "age-sombre"
    assert _slug("Époque") == "epoque"


@pytest.mark.asyncio
async def test_provision_creates_the_declared_structure() -> None:
    """A fresh guild gets every declared group and channel, nothing else."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    created, adopted = await provision_structure(guild)
    assert {_slug(category.name) for category in guild.categories} == expected_category_slugs()
    paths = {
        f"{_slug(channel.category.name)}/{_slug(channel.name)}" for channel in all_guild_channels(guild)
    }
    assert paths == expected_channel_slugs()
    assert created and not adopted


@pytest.mark.asyncio
async def test_provision_creates_forums_and_announce_channels() -> None:
    """Forums are forums; announce channels deny sends for @everyone."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    forum_names = {_slug(channel.name) for channel in guild.forums}
    assert {"regles", "suggestion", "question", "signaler-un-bug"} <= forum_names
    annonce = next(channel for channel in guild.text_channels if channel.name == "annonce")
    overwrite = annonce.creation_overwrite_for(guild.default_role)
    assert overwrite is not None and overwrite.send_messages is False and overwrite.view_channel is True


@pytest.mark.asyncio
async def test_provision_orders_categories_by_declaration() -> None:
    """Profils first, Général second, Support last — declaration order."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    positions = {category.name: category.position for category in guild.categories}
    assert positions["profils"] == 0
    assert positions["general"] == 1
    assert max(positions.values()) == positions["support"]


@pytest.mark.asyncio
async def test_provision_is_idempotent() -> None:
    """Re-running the bootstrap adopts the existing structure, no duplicates."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    created, adopted = await provision_structure(guild)
    assert not created
    assert set(adopted) == set(expected_group_names().values()) | {
        f"{expected_group_names()[category.group]}/{category.display_name}"
        for category in kingdoms_definition().channel_categories
    }
    paths = {
        f"{_slug(channel.category.name)}/{_slug(channel.name)}" for channel in all_guild_channels(guild)
    }
    assert paths == expected_channel_slugs()


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
async def test_admin_only_channels_deny_everyone() -> None:
    """Candidatures and the Admin group (category + channels) deny @everyone."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    candidatures = next(channel for channel in guild.text_channels if channel.name == "candidatures")
    assert candidatures.creation_overwrite_for(guild.default_role) is not None
    admin_category = next(category for category in guild.categories if category.name == "admin")
    assert guild.default_role in admin_category._overwrites
    admin_channels = [channel for channel in guild.text_channels if channel.category is admin_category]
    assert {channel.name for channel in admin_channels} == {"parametres", "demandes"}
    for channel in admin_channels:
        assert channel.creation_overwrite_for(guild.default_role) is not None


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
    assert {_slug(category.name) for category in guild.categories} == expected_category_slugs()
    assert interaction.followup.messages, "the bootstrap report must be answered"
    await client.close()


def test_mod_name_matches_the_declaration() -> None:
    assert MOD_NAME == kingdoms_definition().name


@pytest.mark.asyncio
async def test_epoch_channel_is_read_only_for_everyone() -> None:
    """The epoch channel (adopt: group_single) is read-only for @everyone."""
    from kingdoms.discord.kingdom_setup import EPOCH_CATEGORY, EPOCH_CHANNEL_KEY

    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    created, _ = await provision_structure(guild)
    assert created
    epoch_group = next(c for c in guild.categories if c.name == _slug(EPOCH_CATEGORY))
    epoch = next(c for c in guild.text_channels if c.category is epoch_group)
    overwrite = epoch.permission_overwrite_for(guild.default_role)
    assert overwrite is not None and overwrite.view_channel is True and overwrite.send_messages is False
    mine = epoch.permission_overwrite_for(guild.me)
    assert mine is not None and mine.send_messages is True
    assert EPOCH_CHANNEL_KEY == "epoch"


@pytest.mark.asyncio
async def test_epoch_channel_adopted_by_group_after_rename() -> None:
    """A renamed epoch channel is adopted back by its group, never duplicated."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    epoch = next(c for c in guild.text_channels if c.name == "age-sombre")
    epoch.name = "Âge féodal"  # renamed at an age switch

    created, adopted = await provision_structure(guild)

    assert not created
    assert "Époque/Âge sombre" in adopted
    names = [c.name for c in guild.text_channels]
    assert names.count("age-sombre") == 0
    assert names.count("age-feodal") == 1
