"""Tests for the pinned ladder-admin menu lifecycle (no rebuild while it lives).

Same contract as the home pin: ``ensure_pinned_ladder_admin_menu`` must not
recreate the menu while the registered message lives (it merely re-pins it),
and must rebuild and re-register only when the registered message is gone.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.discord.ladder_admin_channel import ensure_pinned_ladder_admin_menu


async def _async_layout(bot: Any, guild: str) -> object:
    return object()


class _FakeMessage:
    def __init__(self, message_id: str, view: Any, channel: _FakeChannel) -> None:
        self.id = message_id
        self.view = view
        self.channel = channel
        self.pin_calls = 0

    async def pin(self, reason: str = "") -> None:
        self.pin_calls += 1


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[_FakeMessage] = []

    async def send(self, view: Any = None, **kwargs: Any) -> _FakeMessage:
        message = _FakeMessage(str(9000 + len(self.sent)), view, self)
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        for message in self.sent:
            if message.id == str(message_id):
                return message
        raise LookupError(str(message_id))


class _FakeChannelWithId(_FakeChannel):
    def __init__(self) -> None:
        super().__init__()
        self.id = "1234"


class _FakeLadderAdminChannelService:
    def __init__(self, channel_id: str) -> None:
        self._channel_id = channel_id

    async def resolve_channel(self, guild_id: str) -> str | None:
        del guild_id
        return self._channel_id


class _FakeRegistry:
    def __init__(self) -> None:
        self.registered: dict[str, str] = {}

    async def resolve(self, platform: str, message_key: str, entity_id: str) -> Any:
        message_id = self.registered.get(entity_id)
        return _FakeRegistered(message_id) if message_id else None

    async def register(self, **kwargs: Any) -> None:
        self.registered[str(kwargs["entity_id"])] = str(kwargs["message_id"])


class _FakeRegistered:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


class _FakeGuild:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int) -> _FakeChannel | None:
        return self._channel if str(channel_id) == self._channel.id else None


class _FakeBot:
    def __init__(self, channel: _FakeChannel, registry: Any | None = None) -> None:
        self.ladder_admin_channel_service = _FakeLadderAdminChannelService(channel.id)
        self.message_registry = registry
        self.guilds = []
        self.get_guild = lambda guild_id: _FakeGuild(channel) if str(guild_id) == "42" else None


@pytest.mark.asyncio
async def test_first_ensure_creates_and_registers_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _FakeChannelWithId()
    registry = _FakeRegistry()
    bot = _FakeBot(channel, registry)
    monkeypatch.setattr(
        "kingdoms.discord.ladder_admin_channel._build_layout",
        _async_layout,
    )
    created = await ensure_pinned_ladder_admin_menu(bot, "42")
    assert created is True
    assert len(channel.sent) == 1
    assert registry.registered["42"] == channel.sent[0].id


@pytest.mark.asyncio
async def test_second_ensure_does_not_recreate_while_the_message_lives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _FakeChannelWithId()
    registry = _FakeRegistry()
    bot = _FakeBot(channel, registry)
    monkeypatch.setattr(
        "kingdoms.discord.ladder_admin_channel._build_layout",
        _async_layout,
    )
    await ensure_pinned_ladder_admin_menu(bot, "42")
    first_message = channel.sent[0]
    created = await ensure_pinned_ladder_admin_menu(bot, "42")
    assert created is False
    assert len(channel.sent) == 1
    assert channel.sent[0] is first_message
    assert first_message.pin_calls >= 1


@pytest.mark.asyncio
async def test_ensure_rebuilds_when_the_registered_message_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _FakeChannelWithId()
    registry = _FakeRegistry()
    bot = _FakeBot(channel, registry)
    monkeypatch.setattr(
        "kingdoms.discord.ladder_admin_channel._build_layout",
        _async_layout,
    )
    await ensure_pinned_ladder_admin_menu(bot, "42")
    registry.registered["42"] = "999999"
    created = await ensure_pinned_ladder_admin_menu(bot, "42")
    assert created is True
    assert len(channel.sent) == 2
    assert registry.registered["42"] == channel.sent[1].id
