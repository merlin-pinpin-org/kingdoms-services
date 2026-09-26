"""Unit tests for the /admin hierarchical panel (kingdoms-services#102, #117)."""

from __future__ import annotations

import discord
import pytest

from kingdoms.discord.admin import (
    BACK_BUTTON_ID,
    CHANNEL_MENU_ID,
    LOCALE_SELECT_ID,
    USER_LOCALE_SELECT_ID,
    VISIBILITY_SELECT_ID,
    build_channel_menu,
    build_dm_setup_view,
    build_main_menu,
    register_admin_command,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser


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
    assert interaction.response.sent is True
    assert interaction.response.ephemeral is True
    layout = interaction.response.message.layout
    assert isinstance(layout, discord.ui.LayoutView)
    customs = _custom_ids(layout)
    assert LOCALE_SELECT_ID in customs, "the guild language select is on the main menu"
    assert CHANNEL_MENU_ID in customs, "the managed-channel picker is on the main menu"
    assert logs.resolved_guilds == ["42"]
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
    assert interaction.response.sent is True
    layout = interaction.response.message.layout
    assert layout is not None
    assert USER_LOCALE_SELECT_ID in _custom_ids(layout)
    assert logs.user_locales_read == ["111111111"]
    await client.close()


@pytest.mark.asyncio
async def test_admin_command_dm_locale_change_persists() -> None:
    """Changing the DM locale persists the user setting and re-renders."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    logs = _FakeAdminLogsService(channel_id="555")
    register_admin_command(tree, bot_admins=("111111111",), logs_service=logs)
    command = next(c for c in tree.get_commands() if c.name == "admin")
    interaction = MockInteraction(user=MockUser(id=111111111), guild=None)
    interaction.guild_id = None
    await command._callback(interaction)  # type: ignore[union-attr]
    select = _find_select(interaction.response.message.layout, USER_LOCALE_SELECT_ID)
    assert select is not None
    await _choose(select, interaction, ["fr"])
    assert logs.user_locales_set == [("111111111", "fr")]
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


@pytest.mark.asyncio
async def test_main_menu_locale_change_persists() -> None:
    """The guild locale select updates the guild settings and re-renders."""
    logs = _FakeAdminLogsService(channel_id="555")
    view = await build_main_menu(logs, "42", by="111111111", bot_admins=("111111111",))
    select = _find_select(view, LOCALE_SELECT_ID)
    assert select is not None
    interaction = MockInteraction(user=MockUser(id=111111111), guild=MockGuild(id=42))
    interaction.guild_id = 42
    await _choose(select, interaction, ["fr"])
    assert logs.locales_set == [("42", "fr", "111111111")]


@pytest.mark.asyncio
async def test_main_menu_channel_pick_opens_channel_menu() -> None:
    """Picking a managed channel opens its secondary menu."""
    logs = _FakeAdminLogsService(channel_id="555")
    view = await build_main_menu(logs, "42", by="111111111", bot_admins=("111111111",))
    select = _find_select(view, CHANNEL_MENU_ID)
    assert select is not None
    interaction = MockInteraction(user=MockUser(id=111111111), guild=MockGuild(id=42))
    interaction.guild_id = 42
    await _choose(select, interaction, ["bot_logs"])
    message = interaction.response.message
    assert message is not None
    customs = _custom_ids(message.layout)
    assert BACK_BUTTON_ID in customs
    assert VISIBILITY_SELECT_ID in customs, "the logs channel menu manages the visibility"


@pytest.mark.asyncio
async def test_channel_menu_back_returns_to_main() -> None:
    """The back button returns to the main menu."""
    logs = _FakeAdminLogsService(channel_id="555")
    view = await build_channel_menu(logs, "42", "bot_logs", by="111111111", bot_admins=("111111111",))
    button = _find_button(view, BACK_BUTTON_ID)
    assert button is not None
    interaction = MockInteraction(user=MockUser(id=111111111), guild=MockGuild(id=42))
    interaction.guild_id = 42
    await button.callback(interaction)
    message = interaction.response.message
    assert message is not None
    customs = _custom_ids(message.layout)
    assert CHANNEL_MENU_ID in customs, "back lands on the main menu"


@pytest.mark.asyncio
async def test_channel_menu_visibility_change_persists() -> None:
    """The visibility select updates the logs policy."""
    logs = _FakeAdminLogsService(channel_id="555")
    view = await build_channel_menu(logs, "42", "bot_logs", by="111111111", bot_admins=("111111111",))
    select = _find_select(view, VISIBILITY_SELECT_ID)
    assert select is not None
    interaction = MockInteraction(user=MockUser(id=111111111), guild=MockGuild(id=42))
    interaction.guild_id = 42
    await _choose(select, interaction, ["public"])
    assert logs.visibilities == [("42", True, "111111111")]


@pytest.mark.asyncio
async def test_channel_menu_denies_non_admin_at_click_time() -> None:
    """A stranger clicking a component is denied (click-time guard)."""
    logs = _FakeAdminLogsService(channel_id="555")
    view = await build_channel_menu(logs, "42", "bot_logs", by="111111111", bot_admins=("111111111",))
    select = _find_select(view, VISIBILITY_SELECT_ID)
    assert select is not None
    stranger = MockUser(id=999999999)
    interaction = MockInteraction(user=stranger, guild=MockGuild(id=42))
    interaction.guild_id = 42
    await _choose(select, interaction, ["public"])
    assert logs.visibilities == [], "the visibility must not change for a stranger"
    assert interaction.response.sent is True
    assert "not allowed" in (interaction.response.message or "").content


@pytest.mark.asyncio
async def test_dm_setup_view_without_service_degrades() -> None:
    """Without a LogService, the DM setup still renders (en fallback)."""
    view = await build_dm_setup_view(None, "111111111")
    assert isinstance(view, discord.ui.LayoutView)
    assert USER_LOCALE_SELECT_ID in _custom_ids(view)


def _custom_ids(view: discord.ui.LayoutView) -> set[str]:
    ids: set[str] = set()
    for child in view.walk_children():
        custom_id = getattr(child, "custom_id", None)
        if isinstance(custom_id, str):
            ids.add(custom_id)
    return ids


def _find_select(view: discord.ui.LayoutView, custom_id: str) -> discord.ui.Select | None:
    for child in view.walk_children():
        if isinstance(child, discord.ui.Select) and child.custom_id == custom_id:
            return child
    return None


def _find_button(view: discord.ui.LayoutView, custom_id: str) -> discord.ui.Button | None:
    for child in view.walk_children():
        if isinstance(child, discord.ui.Button) and child.custom_id == custom_id:
            return child
    return None


async def _choose(select: discord.ui.Select, interaction: MockInteraction, values: list[str]) -> None:
    interaction.data = {"values": values}
    interaction.custom_id = select.custom_id
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
