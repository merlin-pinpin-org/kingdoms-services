"""Kingdoms event scheduler loop - unit tests (kingdoms-services#244, D).

The heartbeat behind ``run_due``: ``start_kingdoms_event_scheduler``
ticks every few seconds, replays the due slots through the bot's
``kingdoms_events_service`` and announces every report in each guild's
Géopolitique salon (best effort). A double start never spawns a second
loop and ``stop_kingdoms_event_scheduler`` cancels the tick.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdom_events_loop import (
    start_kingdoms_event_scheduler,
    stop_kingdoms_event_scheduler,
)


class _FakeEvents:
    """A fake event service counting the run_due calls."""

    def __init__(self, reports: list[dict[str, object]]) -> None:
        self.reports = reports
        self.calls = 0

    async def run_due(self, now: Any) -> list[dict[str, object]]:
        self.calls += 1
        return list(self.reports)


class _FakeChannel:
    def __init__(self) -> None:
        self.name = "Géopolitique"
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


class _FakeGuild:
    def __init__(self, channel: _FakeChannel) -> None:
        self.id = "123"
        self.text_channels = [channel]
        self.voice_channels: list[Any] = []
        self.categories: list[Any] = []


class _FakeLogs:
    async def get_locale(self, guild_id: str) -> str:
        return "fr"


class _FakeBot:
    def __init__(self, guild: _FakeGuild, events: _FakeEvents) -> None:
        self.guilds = [guild]
        self.kingdoms_events_service = events
        self.logs_service = _FakeLogs()


@pytest.fixture(autouse=True)
async def _stopped_scheduler() -> Any:
    await stop_kingdoms_event_scheduler()
    yield
    await stop_kingdoms_event_scheduler()


async def test_scheduler_replays_due_slots_and_announces() -> None:
    channel = _FakeChannel()
    events = _FakeEvents(
        [
            {"kind": "cycle", "cycle": 1, "gaia_new_maps": ["arabia"]},
            {"kind": "age", "age": "feudal_age", "display_name": "Âge féodal", "tech_points": 1, "extra_marriages": 1},
        ]
    )
    bot = _FakeBot(_FakeGuild(channel), events)
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    for _ in range(20):
        await asyncio.sleep(0)
        if events.calls and channel.sent:
            break
    assert events.calls == 1
    assert len(channel.sent) == 2
    assert "Cycle 1" in channel.sent[0]
    assert "Âge féodal" in channel.sent[1]
    await stop_kingdoms_event_scheduler()


async def test_scheduler_never_starts_twice() -> None:
    channel = _FakeChannel()
    events = _FakeEvents([])
    bot = _FakeBot(_FakeGuild(channel), events)
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    await stop_kingdoms_event_scheduler()
    # After the stop a fresh start works again.
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    await stop_kingdoms_event_scheduler()


async def test_scheduler_survives_a_run_due_failure() -> None:
    channel = _FakeChannel()

    class _BrokenEvents(_FakeEvents):
        async def run_due(self, now: Any) -> list[dict[str, object]]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("store unavailable")
            return list(self.reports)

    events = _BrokenEvents([{"kind": "cycle", "cycle": 2, "gaia_new_maps": []}])
    bot = _FakeBot(_FakeGuild(channel), events)
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    for _ in range(20):
        await asyncio.sleep(0)
        if events.calls >= 2:
            break
    assert events.calls >= 2
    assert channel.sent and "**Cycle 2**" in channel.sent[0]
    await stop_kingdoms_event_scheduler()
