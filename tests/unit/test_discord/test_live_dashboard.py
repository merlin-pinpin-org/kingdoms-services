"""Unit tests for the persistent live dashboard surface (#147).

Covers the logical-key lifecycle: the dashboard message is created once,
registered in the message registry, then edited in place on every
refresh — the channel never accumulates messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from kingdoms.core.models.registered_message import RegisteredMessageModel
from kingdoms.discord.live import (
    GAME_KEY,
    LIVE_MESSAGE_KEY,
    PLATFORM,
    ensure_live_dashboard,
)


@dataclass
class _FakeMessage:
    id: int
    content: str = ""
    edits: list[str] = field(default_factory=list)
    embeds: list[object] = field(default_factory=list)


class _FakePartialMessage:
    """PartialMessage stand-in recording in-place edits."""

    def __init__(self, store: _FakeChannel, message_id: int) -> None:
        self._store = store
        self._id = message_id

    async def edit(self, content: str = "", embed: object = None) -> None:
        message = self._store.messages.get(self._id)
        if message is None:
            raise RuntimeError("message gone")
        if content:
            message.content = content
        if embed is not None:
            message.embeds.append(embed)
        message.edits.append(content or getattr(embed, "description", ""))


class _FakeChannel:
    """TextChannel stand-in: id, sent messages, partial-message lookups."""

    def __init__(self, id: int) -> None:
        self.id = id
        self.name = "live-dashboard"
        self.messages: dict[int, _FakeMessage] = {}
        self.sent: list[_FakeMessage] = []

    def get_partial_message(self, message_id: int) -> _FakePartialMessage:
        return _FakePartialMessage(self, message_id)

    async def send(self, content: str = "", embed: object = None) -> _FakeMessage:
        message = _FakeMessage(id=1000 + len(self.sent), content=content)
        if embed is not None:
            message.embeds.append(embed)
        self.messages[message.id] = message
        self.sent.append(message)
        return message


class _FakeGuild:
    """Guild stand-in resolving the dashboard channel."""

    def __init__(self, id: int, channel: _FakeChannel) -> None:
        self.id = id
        self._channel = channel
        self.text_channels = [channel]

    async def create_text_channel(self, name: str, reason: str = "") -> _FakeChannel:
        raise AssertionError("the dashboard channel already exists — must not be recreated")


class _FakeBot:
    """Client stand-in resolving the guild."""

    def __init__(self, guild: _FakeGuild) -> None:
        self._guild = guild
        self.guilds = [guild]

    def get_guild(self, guild_id: int) -> _FakeGuild | None:
        return self._guild if guild_id == self._guild.id else None


class _FakeLiveClient:
    """LiveClient seam: a fixed snapshot per game key."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.watch_calls: list[str] = []

    async def watch(self, game_key: str) -> dict[str, Any]:
        self.watch_calls.append(game_key)
        return self.snapshot


class _FakeRegistry:
    """In-memory MessageRegistryService seam."""

    def __init__(self) -> None:
        self.messages: dict[str, RegisteredMessageModel] = {}
        self.registrations: list[RegisteredMessageModel] = []

    async def resolve(self, platform: str, message_key: str, entity_id: str) -> RegisteredMessageModel | None:
        return self.messages.get(f"{platform}:{message_key}:{entity_id}")

    async def register(
        self,
        *,
        platform: str,
        message_key: str,
        entity_id: str,
        channel_id: str,
        message_id: str,
        guild_id: str | None = None,
    ) -> None:
        message = RegisteredMessageModel(
            _id=f"{platform}:{message_key}:{entity_id}",
            platform=platform,
            message_key=message_key,
            entity_id=entity_id,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
        )
        self.messages[message.id] = message
        self.registrations.append(message)


def _snapshot(state: str) -> dict[str, Any]:
    return {
        "players": [{"user_id": "10", "profile_id": "A", "state": state, "match_ref": "", "since": 0}],
        "generated_at": 1,
        "degraded": False,
    }


@pytest.mark.asyncio
async def test_first_refresh_creates_and_registers_the_message() -> None:
    """Without a registered message, one is sent and registered by logical key."""
    channel = _FakeChannel(555)
    bot = _FakeBot(_FakeGuild(42, channel))
    client = _FakeLiveClient(_snapshot("offline"))
    registry = _FakeRegistry()

    created = await ensure_live_dashboard(bot, "42", client, registry)  # type: ignore[arg-type]

    assert created is True
    assert len(channel.sent) == 1, "exactly one dashboard message is created"
    assert client.watch_calls == [GAME_KEY]
    key = f"{PLATFORM}:{LIVE_MESSAGE_KEY}:42"
    assert key in registry.messages, "the message is registered by its logical key"


@pytest.mark.asyncio
async def test_refresh_edits_in_place_without_spam() -> None:
    """A registered message is edited in place — no new message is sent."""
    channel = _FakeChannel(555)
    bot = _FakeBot(_FakeGuild(42, channel))
    registry = _FakeRegistry()
    await ensure_live_dashboard(bot, "42", _FakeLiveClient(_snapshot("offline")), registry)  # type: ignore[arg-type]

    created = await ensure_live_dashboard(bot, "42", _FakeLiveClient(_snapshot("in_lobby")), registry)  # type: ignore[arg-type]

    assert created is False
    assert len(channel.sent) == 1, "the refresh never adds a message"
    assert channel.sent[0].edits, "the existing message is edited in place"
    assert "in_lobby" in channel.sent[0].edits[-1]


@pytest.mark.asyncio
async def test_deleted_message_is_recreated_and_reregistered() -> None:
    """A deleted registered message self-heals: recreated and re-registered."""
    channel = _FakeChannel(555)
    bot = _FakeBot(_FakeGuild(42, channel))
    registry = _FakeRegistry()
    await ensure_live_dashboard(bot, "42", _FakeLiveClient(_snapshot("offline")), registry)  # type: ignore[arg-type]
    channel.messages.clear()

    created = await ensure_live_dashboard(bot, "42", _FakeLiveClient(_snapshot("in_game")), registry)  # type: ignore[arg-type]

    assert created is True
    assert len(channel.sent) == 2, "a fresh message replaces the deleted one"
    assert len(registry.registrations) == 2, "the replacement is re-registered"


@pytest.mark.asyncio
async def test_provider_failure_degrades_to_offline_dashboard() -> None:
    """With the provider down, the dashboard still renders (degraded)."""

    class _BrokenClient:
        async def watch(self, game_key: str) -> dict[str, Any]:
            raise RuntimeError("core down")

    channel = _FakeChannel(555)
    bot = _FakeBot(_FakeGuild(42, channel))
    registry = _FakeRegistry()

    created = await ensure_live_dashboard(bot, "42", _BrokenClient(), registry)  # type: ignore[arg-type]

    assert created is True
    body = channel.sent[0].edits[-1] if channel.sent[0].edits else channel.sent[0].embeds[0].description
    assert "Providers unreachable" in body, "the degraded note is shown"
