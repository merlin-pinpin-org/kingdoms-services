"""Unit tests for the /admin hierarchical panel (kingdoms-services#102, #117)."""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord.admin import register_admin_command
from kingdoms.discord.admin_tree import _NS as TREE_NS
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser


def _custom_ids(layout: discord.ui.LayoutView) -> list[str]:
    """Collect every interactive child's custom id of a layout."""
    return [c.custom_id for c in layout.walk_children() if getattr(c, "custom_id", None)]




@pytest.mark.asyncio
async def test_register_admin_command_adds_command_to_tree() -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_admin_command(tree)
    assert "admin" in {command.name for command in tree.get_commands()}
    await client.close()


@pytest.mark.asyncio
async def test_admin_command_denies_non_operator() -> None:
    """A user outside BOT_ADMINS gets an ephemeral access-denied message."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    logs = _FakeAdminLogsService(channel_id="555")
    register_admin_command(tree, bot_admins=("111111111",), logs_service=logs)
    command = next(c for c in tree.get_commands() if c.name == "admin")

    stranger = MockUser(id=222222222)
    interaction = MockInteraction(user=stranger, guild=MockGuild(id=42))
    interaction.guild_id = 42
    await command._callback(interaction)  # type: ignore[union-attr]

    assert interaction.response.sent is True
    assert interaction.response.ephemeral is True
    assert "not allowed" in (interaction.response.message or "").content
    await client.close()


@pytest.mark.asyncio
async def test_admin_command_guild_main_menu_for_operator() -> None:
    """In a guild, an admin gets the main menu (language + channel picker)."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    logs = _FakeAdminLogsService(channel_id="555")
    register_admin_command(tree, bot_admins=("111111111",), logs_service=logs)
    command = next(c for c in tree.get_commands() if c.name == "admin")
    interaction = MockInteraction(user=MockUser(id=111111111), guild=MockGuild(id=42))
    interaction.guild_id = 42
    await command._callback(interaction)  # type: ignore[union-attr]
    assert interaction.response.deferred is True, "the command answers immediately (defer)"
    assert interaction.followup.messages, "the panel arrives through the followup"
    layout = interaction.followup.messages[-1].layout
    assert isinstance(layout, discord.ui.LayoutView)
    customs = _custom_ids(layout)
    # the language button is gone: the locale follows the server's
    # native Discord language (Server Settings), nothing to manage.
    assert f"{TREE_NS}:open:games" in customs, "the games admin button is on the main menu"
    assert f"{TREE_NS}:open:mods" in customs, "the mods admin button is on the main menu"
    assert any(c.startswith(f"{TREE_NS}:ensure:") for c in customs), "the ensure button is on the main menu"
    # the channels/roles management views are gone: the tree is declared
    # and provisioned, the homes explain it, the admins carry the ensure.
    await client.close()


@pytest.mark.asyncio
async def test_admin_command_dm_shows_user_locale_setup() -> None:
    """Outside a guild, /admin manages the user's personal DM locale."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    logs = _FakeAdminLogsService(channel_id="555")
    register_admin_command(tree, bot_admins=("111111111",), logs_service=logs)
    command = next(c for c in tree.get_commands() if c.name == "admin")
    interaction = MockInteraction(user=MockUser(id=111111111), guild=None)
    interaction.guild_id = None
    await command._callback(interaction)  # type: ignore[union-attr]
    assert interaction.response.deferred is True, "the DM panel defers before its reads"
    assert interaction.followup.messages, "the DM panel arrives through the followup"
    layouts = [m.layout for m in interaction.followup.messages]
    assert all(layout is not None for layout in layouts)
    # the frozen DM menu: the locale select lives behind the language
    # opener (admin:dm:open:language), never inline on the menu itself
    assert any(
        any(cid.startswith("admin:dm:open:") for cid in _custom_ids(layout))
        for layout in layouts
        if layout is not None
    ), "the DM menu carries its openers"
    await client.close()



@pytest.mark.asyncio
async def test_admin_command_restricts_discord_permissions_by_default() -> None:
    """The command requires administrator guild permissions by default."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_admin_command(tree, bot_admins=())
    command = next(c for c in tree.get_commands() if c.name == "admin")
    assert command.default_permissions is not None
    assert command.default_permissions.administrator is True
    await client.close()


















async def _choose(select: discord.ui.Select, interaction: MockInteraction, values: list[str]) -> None:
    interaction.data = {"values": values}
    interaction.custom_id = select.custom_id
    await select.callback(interaction)


async def _choose_channel(
    select: discord.ui.ChannelSelect[Any], interaction: MockInteraction, channel_ids: list[str]
) -> None:
    """Click a ChannelSelect: the framework resolves channels server-side.

    A real interaction carries the resolved channel objects in
    ``data['resolved']`` and discord.py exposes them through the
    ``selected_values`` context var; the unit test seeds that var
    directly with plain channel stand-ins.
    """
    from discord.ui.select import selected_values

    class _Channel:
        def __init__(self, channel_id: str) -> None:
            self.id = int(channel_id)

    interaction.data = {"values": channel_ids, "resolved": {}}
    interaction.custom_id = select.custom_id
    selected_values.set({select.custom_id: [_Channel(cid) for cid in channel_ids]})
    await select.callback(interaction)


class _FakeAdminLogsService:
    """LogService stand-in for /admin panel tests."""

    def __init__(self, channel_id: str | None = "555") -> None:
        self.channel_id = channel_id
        self.resolved_guilds: list[str] = []
        self.locales_set: list[tuple[str, str, str]] = []
        self.user_locales_read: list[str] = []
        self.user_locales_set: list[tuple[str, str]] = []
        self.visibilities: list[tuple[str, bool, str]] = []
        self.routed: list[tuple[str, str, str]] = []

    async def resolve_channel(self, guild_id: str) -> str | None:
        self.resolved_guilds.append(guild_id)
        return self.channel_id

    async def get_access_policy(self, guild_id: str) -> dict[str, object]:
        return {"default": "admin_only", "roles_with_view": []}

    async def get_locale(self, guild_id: str) -> str:
        return "en"

    async def set_locale(self, guild_id: str, locale: str, by: str) -> None:
        self.locales_set.append((guild_id, locale, by))

    async def get_user_locale(self, user_id: str) -> str:
        self.user_locales_read.append(user_id)
        return "en"

    async def set_user_locale(self, user_id: str, locale: str) -> None:
        self.user_locales_set.append((user_id, locale))

    async def set_visibility(self, guild_id: str, public: bool, by: str) -> None:
        self.visibilities.append((guild_id, public, by))

    async def set_channel(self, guild_id: str, channel_id: str, by: str) -> None:
        self.routed.append((guild_id, channel_id, by))


class _RoutingFailureLogsService(_FakeAdminLogsService):
    """A routing attempt that always fails (the platform rejects the target)."""

    async def set_channel(self, guild_id: str, channel_id: str, by: str) -> None:
        raise ValueError(f"channel {channel_id} does not exist in guild {guild_id}")


@pytest.mark.asyncio
async def test_admin_command_shows_the_mods_select_when_sections_exist() -> None:
    """With a registered mod section, /admin answers the new admin menu
    (language/channels/roles) — the mod routing left the main panel."""
    from kingdoms.discord.admin_panel_mods import (
        AdminModSection,
        mod_section_route_id,
        register_admin_mod_section,
        unregister_admin_mod_section,
    )

    async def _entry(interaction: discord.Interaction) -> discord.ui.LayoutView:
        return discord.ui.LayoutView(timeout=None)

    register_admin_mod_section(AdminModSection(mod="testmod", label="Test", entry=_entry))
    client = discord.Client(intents=discord.Intents.none())
    try:
        tree = discord.app_commands.CommandTree(client)
        logs = _FakeAdminLogsService(channel_id="555")
        register_admin_command(tree, bot_admins=("111111111",), logs_service=logs)
        command = next(c for c in tree.get_commands() if c.name == "admin")
        interaction = MockInteraction(user=MockUser(id=111111111), guild=MockGuild(id=42))
        interaction.guild_id = 42
        await command._callback(interaction)  # type: ignore[union-attr]
        assert interaction.followup.messages, "the panel arrives through the followup"
        customs = _custom_ids(interaction.followup.messages[-1].layout)
        assert mod_section_route_id("mods") not in customs, "the mods select left the main panel"
    finally:
        unregister_admin_mod_section("testmod")
        await client.close()
