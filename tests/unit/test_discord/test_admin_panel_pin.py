"""Unit tests for the pinned admin menu (admin_panel_pin).

The admin channel hosts a permanent pinned menu — the same builder as
/admin, guarded at click time. These tests prove the ensure contract:
creation when absent, silence when present, and the click-time denial
for a non-admin (seeing the channel never grants the rights).
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.admin_panel_pin import ensure_pinned_admin_menu
from tests.mocks.discord_mock import MockUser


class _FakeChannel(discord.TextChannel):
    """A TextChannel stand-in overriding the discord.py surface used here."""

    def __init__(self, channel_id: str) -> None:
        self.id = int(channel_id)
        self.sent: list[object] = []
        self.pins_list: list[Any] = []

    async def pins(self) -> list[Any]:
        return self.pins_list

    async def send(self, *, view: Any = None, content: str = "") -> Any:
        message = _FakeMessage(view=view, channel=self)
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int) -> Any:
        for message in self.sent:
            if message.id == message_id:
                return message
        raise LookupError(f"message {message_id} not found")


class _FakeMessage:
    def __init__(self, view: Any = None, channel: _FakeChannel | None = None) -> None:
        self.view = view
        self.channel = channel
        self.pinned = False
        self.id = 9001
        self.components: list[Any] = []

    async def pin(self, reason: str = "") -> None:
        self.pinned = True
        if self.channel is not None:
            self.channel.pins_list.append(self)


class _FakeAdminChannelService:
    """AdminChannelService stand-in: resolve + deliver (real send)."""

    def __init__(self, channel: _FakeChannel) -> None:
        self.channel = channel

    async def resolve_channel(self, guild_id: str, admin_ids: tuple[str, ...] = ()) -> str | None:
        return str(self.channel.id)

    async def deliver(self, guild_id: str, layout: Any, admin_ids: tuple[str, ...] = ()) -> str | None:
        message = await self.channel.send(view=layout)
        return str(message.id)


class _FakeBot:
    """Client stand-in resolving the guild/channel."""

    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def get_guild(self, guild_id: int) -> Any:
        return type("G", (), {"get_channel": lambda _, cid: self._channel, "id": guild_id})()


class _FakeLogsService:
    """LogService stand-in for the menu builder."""

    async def get_locale(self, guild_id: str) -> str:
        return "en"

    async def get_access_policy(self, guild_id: str) -> dict[str, object]:
        return {"default": "admin_only", "roles_with_view": []}

    async def resolve_channel(self, guild_id: str) -> str | None:
        return "555"


@pytest.mark.asyncio
async def test_ensure_creates_and_pins_when_absent() -> None:
    """A guild without a pinned menu gets one: delivered + pinned."""
    channel = _FakeChannel("555")
    bot = _FakeBot(channel)
    service = _FakeAdminChannelService(channel)
    created = await ensure_pinned_admin_menu(
        bot,  # type: ignore[arg-type]
        "42",
        _FakeLogsService(),  # type: ignore[arg-type]
        None,
        service,  # type: ignore[arg-type]
        ("111111111",),
        None,
    )
    assert created is True
    assert channel.sent, "the menu was delivered"
    assert channel.pins_list, "the menu was pinned"


@pytest.mark.asyncio
async def test_ensure_stays_silent_when_present() -> None:
    """An existing pinned menu is left alone — no duplicate."""
    channel = _FakeChannel("555")
    existing = _FakeMessage()
    existing.components = [type("C", (), {"custom_id": "admin:select:channel", "children": []})()]
    channel.pins_list = [existing]
    bot = _FakeBot(channel)
    service = _FakeAdminChannelService(channel)
    created = await ensure_pinned_admin_menu(
        bot,  # type: ignore[arg-type]
        "42",
        _FakeLogsService(),  # type: ignore[arg-type]
        None,
        service,  # type: ignore[arg-type]
        ("111111111",),
        None,
    )
    assert created is False
    assert channel.sent == [], "no duplicate menu"


@pytest.mark.asyncio
async def test_ensure_without_admin_service_degrades() -> None:
    """Without an AdminChannelService there is no pinned surface."""
    created = await ensure_pinned_admin_menu(
        _FakeBot(_FakeChannel("555")),  # type: ignore[arg-type]
        "42",
        _FakeLogsService(),  # type: ignore[arg-type]
        None,
        None,
        ("111111111",),
        None,
    )
    assert created is False


@pytest.mark.asyncio
async def test_pinned_menu_denies_non_admin_at_click_time() -> None:
    """The pinned menu reuses the /admin guards: a stranger is denied."""
    from kingdoms.discord.admin import build_main_menu
    from tests.mocks.discord_mock import MockGuild, MockInteraction

    logs = _FakeLogsService()
    view = await build_main_menu(
        logs,  # type: ignore[arg-type]
        "42",
        "system",
        ("111111111",),
        None,
        None,
        None,
    )
    assert isinstance(view, discord.ui.LayoutView)
    select = None
    for child in view.walk_children():
        custom_id = getattr(child, "custom_id", None)
        if custom_id and str(custom_id).startswith("admin:select"):
            select = child
            break
    assert select is not None, "the pinned menu carries interactive components"
    stranger = MockUser(id=999999999)
    interaction = MockInteraction(user=stranger, guild=MockGuild(id=42))
    interaction.guild_id = 42
    interaction.data = {"values": ["fr"]}
    interaction.custom_id = getattr(select, "custom_id", "")
    assert MessageCatalog is not None
    await select.callback(interaction)  # type: ignore[union-attr]
    assert interaction.response.sent is True
    assert "not allowed" in (interaction.response.message or "").content, (
        "the click is denied with the ephemeral reason"
    )
