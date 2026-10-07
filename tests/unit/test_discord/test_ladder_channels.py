"""Unit tests for the ladder salons (kingdoms-services#221)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.discord import ladder_channels
from kingdoms.discord.ladder_channels import (
    DASHBOARD_CHANNEL_NAME,
    HISTORY_CHANNEL_NAME,
    LEADERBOARD_CHANNEL_NAME,
)


class FakeMessage:
    def __init__(self, content: str = "") -> None:
        self.content = content
        self.edited = 0

    async def edit(self, content: str) -> None:
        self.content = content
        self.edited += 1


class FakeChannel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.id = hash(name) & 0xFFFF
        self.sent: list[FakeMessage] = []

    async def send(self, content: str) -> FakeMessage:
        message = FakeMessage(content)
        message.id = hash((self.name, len(self.sent))) & 0xFFFF
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        for message in self.sent:
            if int(message.id) == message_id:
                return message
        raise RuntimeError("message gone")


class FakeCategory:
    def __init__(self) -> None:
        self.id = 1


class FakePlatform:
    def __init__(self, channels: dict[str, FakeChannel]) -> None:
        self.channels = channels

    async def ensure_category(self, guild_id: str, name: str) -> str:
        return "1"

    async def ensure_channel(self, guild_id: str, name: str, category_id: str | None = None) -> str:
        return name


class FakeRegistry:
    def __init__(self) -> None:
        self.messages: dict[str, Any] = {}

    async def resolve(self, platform: str, key: str, entity_id: str) -> Any:
        return self.messages.get(key)

    async def register(self, message: Any) -> None:
        self.messages[message.message_key] = message

    async def forget(self, platform: str, key: str, entity_id: str) -> bool:
        return self.messages.pop(key, None) is not None


class FakeGuild:
    def __init__(self, channels: dict[str, FakeChannel]) -> None:
        self.id = 42
        self.channels = list(channels.values())

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        for channel in self.channels:
            if channel.name == channel_id:
                return channel
        return None


class FakeRow:
    def __init__(self, display_name: str, rating: int, wins: int, losses: int, wait_seconds: int = 0) -> None:
        self.display_name = display_name
        self.rating = rating
        self.wins = wins
        self.losses = losses
        self.wait_seconds = wait_seconds
        self.rank = 0


class FakeSurface:
    def __init__(self) -> None:
        self.queue_rows: list[Any] = []
        self.leaderboard_rows: list[Any] = []

    async def queue_view(self, ladder_id: str, now: int, page: int = 0, page_size: int = 10) -> list[Any]:
        return self.queue_rows

    async def leaderboard_view(self, ladder_id: str, page: int = 0, page_size: int = 10) -> list[Any]:
        return self.leaderboard_rows


class FakeDB:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = docs or []

    async def find_ladder_matches(self, ladder_id: str, statuses: list[str]) -> list[dict[str, Any]]:
        return self.docs


@pytest.mark.asyncio
async def test_dashboard_renders_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = FakeChannel(DASHBOARD_CHANNEL_NAME)
    guild = FakeGuild({DASHBOARD_CHANNEL_NAME: channel})
    registry = FakeRegistry()
    monkeypatch.setattr(ladder_channels, "_ladder_registry", lambda: registry)
    surface = FakeSurface()
    surface.queue_rows = [FakeRow("Alice", 1500, 3, 1, wait_seconds=120)]

    await ladder_channels._sync_dashboard(guild, surface, "l1")
    assert len(channel.sent) == 1
    assert "Alice" in channel.sent[0].content
    assert "1500" in channel.sent[0].content


@pytest.mark.asyncio
async def test_leaderboard_renders_rankings(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = FakeChannel(LEADERBOARD_CHANNEL_NAME)
    guild = FakeGuild({LEADERBOARD_CHANNEL_NAME: channel})
    registry = FakeRegistry()
    monkeypatch.setattr(ladder_channels, "_ladder_registry", lambda: registry)
    surface = FakeSurface()
    surface.leaderboard_rows = [FakeRow("Alice", 1500, 3, 1), FakeRow("Bob", 1400, 1, 3)]
    surface.leaderboard_rows[0].rank = 1
    surface.leaderboard_rows[1].rank = 2

    await ladder_channels._sync_leaderboard(guild, surface, "l1")
    content = channel.sent[0].content
    assert "#1" in content and "Alice" in content
    assert "#2" in content and "Bob" in content


@pytest.mark.asyncio
async def test_history_renders_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = FakeChannel(HISTORY_CHANNEL_NAME)
    guild = FakeGuild({HISTORY_CHANNEL_NAME: channel})
    registry = FakeRegistry()
    monkeypatch.setattr(ladder_channels, "_ladder_registry", lambda: registry)

    wiring = type("W", (), {})()
    wiring.service = type("S", (), {})()
    wiring.service._db = FakeDB([{"winner_user_id": "alice", "loser_user_id": "bob"}])

    await ladder_channels._sync_history(guild, wiring, "l1")
    assert "alice" in channel.sent[0].content
    assert "bob" in channel.sent[0].content


@pytest.mark.asyncio
async def test_salons_self_heal_deleted_message(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = FakeChannel(DASHBOARD_CHANNEL_NAME)
    guild = FakeGuild({DASHBOARD_CHANNEL_NAME: channel})
    registry = FakeRegistry()
    monkeypatch.setattr(ladder_channels, "_ladder_registry", lambda: registry)
    surface = FakeSurface()

    await ladder_channels._sync_dashboard(guild, surface, "l1")
    assert len(channel.sent) == 1
    first = channel.sent[0]

    await ladder_channels._sync_dashboard(guild, surface, "l1")
    assert channel.sent[0] is first
    assert first.edited == 1
