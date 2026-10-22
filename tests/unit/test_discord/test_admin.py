"""Unit tests for the /admin hierarchical panel (kingdoms-services#102, #117)."""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord.admin import register_admin_command
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser


def _declare_views() -> None:
    from kingdoms.discord.views_declared import declare_views_from_config
    from kingdoms.discord.views_registry import clear_registry

    clear_registry()
    declare_views_from_config("config")


@pytest.fixture(autouse=True)
def _views_declared() -> None:
    """The /admin and /home commands route through the views registry."""
    _declare_views()


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
    # the declared router defers then follows up with the menu layout
    assert interaction.followup.messages, "the declared menu arrives through the followup"
    layout = interaction.followup.messages[-1].layout
    assert isinstance(layout, discord.ui.LayoutView)
    customs = _custom_ids(layout)
    # the language button is gone: the locale follows the server's
    # native Discord language (Server Settings), nothing to manage.
    assert "view:open:menu:games:admin" in customs, "the games admin button is on the menu"
    assert "view:open:menu:mods:admin" in customs, "the mods admin button is on the menu"
    assert "view:open:panel:status" in customs, "the status button is on the menu"
    assert any(":ensure:" in c for c in customs), "the sync button is on the menu"
    # the menus are declared (config/views.yaml): the tree provisions from
    # the same source and the sync button recreates the missing channels.
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


