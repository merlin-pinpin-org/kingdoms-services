"""Tests for the pinned home menu lifecycle (no rebuild while it lives).

The pinned home message id is registered (message registry or in-memory
fallback): ``ensure_pinned_home_menu`` must not recreate the menu while
the registered message exists (it merely re-pins it), and must rebuild
and re-register only when the registered message is gone.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.discord.static_pins import reset_static_pins

from kingdoms.discord.home import ensure_pinned_home_menu


@pytest.fixture(autouse=True)
def _clean_static_pins() -> None:
    """Isolate the static-pin registry per test."""
    reset_static_pins()


class _FakeMessage:
    """Message stand-in: fetchable while alive, pin-tracked."""

    def __init__(self, message_id: str, view: Any, channel: _FakeChannel) -> None:
        self.id = message_id
        self.view = view
        self.channel = channel
        self.pin_calls = 0

    async def pin(self, reason: str = "") -> None:
        self.pin_calls += 1

    async def edit(self, view: Any = None, **kwargs: Any) -> None:
        """In-place edit (the refresh path of the static-pin cycle)."""
        if view is not None:
            self.view = view


class _FakeChannel:
    """discord.TextChannel stand-in: send + fetch of live messages."""

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


class _FakeHomeChannelService:
    """Resolves the guild's home channel id."""

    def __init__(self, channel_id: str) -> None:
        self._channel_id = channel_id

    async def resolve_channel(self, guild_id: str) -> str | None:
        del guild_id
        return self._channel_id


class _FakeHomeService:
    """Buttons provider: one button is enough for the layout."""

    def buttons(self) -> list[Any]:
        from kingdoms.core.services.home import HomeButton

        return [HomeButton(view="status", label="Status", emoji="\U0001f9ed")]


class _FakeGuild:
    """Guild stand-in resolving channels by id."""

    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int) -> _FakeChannel | None:
        return self._channel if str(channel_id) == self._channel.id else None


class _FakeChannelWithId(_FakeChannel):
    def __init__(self) -> None:
        super().__init__()
        self.id = "1234"


class _FakeRegistry:
    """MessageRegistryService stand-in: durable registered message ids."""

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


class _FakeBot:
    """discord.Client stand-in: guild lookup + service wiring."""

    def __init__(self, channel: _FakeChannel, registry: Any | None = None) -> None:
        self.home_channel_service = _FakeHomeChannelService(channel.id)
        self.home_service = _FakeHomeService()
        self.message_registry = registry
        self.guilds = []
        self.get_guild = lambda guild_id: _FakeGuild(channel) if str(guild_id) == "42" else None


@pytest.mark.asyncio
async def test_first_ensure_creates_and_registers_the_menu() -> None:
    channel = _FakeChannelWithId()
    registry = _FakeRegistry()
    bot = _FakeBot(channel, registry)

    created = await ensure_pinned_home_menu(bot, "42")

    assert created is True
    assert len(channel.sent) == 1
    assert registry.registered["42"] == channel.sent[0].id


@pytest.mark.asyncio
async def test_second_ensure_does_not_recreate_while_the_message_lives() -> None:
    channel = _FakeChannelWithId()
    registry = _FakeRegistry()
    bot = _FakeBot(channel, registry)
    await ensure_pinned_home_menu(bot, "42")
    first_message = channel.sent[0]

    created = await ensure_pinned_home_menu(bot, "42")

    assert created is False
    assert len(channel.sent) == 1  # no new message
    assert channel.sent[0] is first_message
    assert first_message.pin_calls >= 1  # merely re-pinned (idempotent on Discord)


@pytest.mark.asyncio
async def test_deleted_menu_is_rebuilt_and_re_registered() -> None:
    channel = _FakeChannelWithId()
    registry = _FakeRegistry()
    bot = _FakeBot(channel, registry)
    await ensure_pinned_home_menu(bot, "42")
    channel.sent.clear()  # the registered message is gone

    created = await ensure_pinned_home_menu(bot, "42")

    assert created is True
    assert len(channel.sent) == 1
    assert registry.registered["42"] == channel.sent[0].id


@pytest.mark.asyncio
async def test_memory_fallback_without_registry() -> None:
    channel = _FakeChannelWithId()
    bot = _FakeBot(channel, registry=None)

    assert await ensure_pinned_home_menu(bot, "42") is True
    assert await ensure_pinned_home_menu(bot, "42") is False
    assert len(channel.sent) == 1
