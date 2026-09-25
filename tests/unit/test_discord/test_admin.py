"""Unit tests for the /admin layout view command (kingdoms-services#102)."""

from __future__ import annotations

import discord
import pytest

from kingdoms.discord.admin import (
    PING_BUTTON_ID,
    AdminLayout,
    build_admin_layout,
    register_admin_command,
)
from tests.mocks.discord_mock import MockInteraction, MockUser


@pytest.mark.asyncio
async def test_admin_layout_structure() -> None:
    layout = build_admin_layout()
    assert isinstance(layout, discord.ui.LayoutView)
    texts: list[str] = []
    for child in layout.walk_children():
        if isinstance(child, discord.ui.TextDisplay):
            texts.append(child.content)
    assert any("Admin" in text for text in texts)
    assert any("Ping" in text or "ping" in text.lower() for text in texts)


def test_admin_layout_button_has_conventional_custom_id() -> None:
    layout = build_admin_layout()
    buttons = [c for c in layout.walk_children() if isinstance(c, discord.ui.Button)]
    assert len(buttons) == 1
    button = buttons[0]
    assert button.custom_id == PING_BUTTON_ID
    assert button.custom_id.startswith("admin:")
    assert button.label == "Ping"


@pytest.mark.asyncio
async def test_ping_button_answers_pong_ephemeral() -> None:
    layout = build_admin_layout()
    button = next(c for c in layout.walk_children() if isinstance(c, discord.ui.Button))
    interaction = MockInteraction(custom_id=PING_BUTTON_ID)
    callback = button.callback
    assert callback is not None
    await callback(interaction)
    assert interaction.response.sent is True
    assert interaction.response.ephemeral is True
    assert interaction.response.message is not None
    assert interaction.response.message.content == "pong"


@pytest.mark.asyncio
async def test_register_admin_command_adds_command_to_tree() -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_admin_command(tree)
    assert "admin" in {command.name for command in tree.get_commands()}


@pytest.mark.asyncio
async def test_admin_command_sends_layout_view() -> None:
    """A BOT_ADMINS operator receives the Components V2 layout."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_admin_command(tree, bot_admins=("111111111",))
    command = next(c for c in tree.get_commands() if c.name == "admin")
    interaction = MockInteraction(user=MockUser(id=111111111))
    await command._callback(interaction)  # type: ignore[union-attr]
    assert interaction.response.sent is True
    assert interaction.response.ephemeral is True
    message = interaction.response.message
    assert message is not None
    assert isinstance(message.layout, discord.ui.LayoutView)
    button = next(c for c in message.layout.walk_children() if isinstance(c, discord.ui.Button))
    assert button.custom_id == PING_BUTTON_ID


def test_admin_layout_is_a_layoutview_instance() -> None:
    assert isinstance(build_admin_layout(), AdminLayout)


@pytest.mark.asyncio
async def test_admin_command_denies_non_operator() -> None:
    """A user outside BOT_ADMINS gets an ephemeral access-denied message."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_admin_command(tree, bot_admins=("111111111",))
    command = next(c for c in tree.get_commands() if c.name == "admin")

    stranger = MockUser(id=222222222)
    interaction = MockInteraction(user=stranger)
    await command._callback(interaction)  # type: ignore[arg-arg]

    assert interaction.response.sent is True
    assert interaction.response.ephemeral is True
    assert "not a bot operator" in (interaction.response.message or "").content
    await client.close()


@pytest.mark.asyncio
async def test_admin_command_allows_operator() -> None:
    """A BOT_ADMINS member gets the admin layout view."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_admin_command(tree, bot_admins=("111111111",))
    command = next(c for c in tree.get_commands() if c.name == "admin")

    operator = MockUser(id=111111111)
    interaction = MockInteraction(user=operator)
    await command._callback(interaction)  # type: ignore[arg-arg]

    assert interaction.response.sent is True
    assert interaction.response.ephemeral is True
    assert "not a bot operator" not in (interaction.response.message or "").content
    await client.close()


def test_admin_command_restricts_discord_permissions_by_default() -> None:
    """The command requires administrator guild permissions by default."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_admin_command(tree, bot_admins=())
    command = next(c for c in tree.get_commands() if c.name == "admin")
    assert command.default_permissions is not None
    assert command.default_permissions.administrator is True
