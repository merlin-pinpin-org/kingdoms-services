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
    """A fake event service counting the run_due calls and hook kinds."""

    def __init__(self, reports: list[dict[str, object]]) -> None:
        self.reports = reports
        self.calls = 0
        self.hooked: list[str] = []

    async def run_due(
        self, now: Any, *, snapshotter: Any = None
    ) -> list[dict[str, object]]:
        self.calls += 1
        if snapshotter is not None:
            await snapshotter("cycle")
            self.hooked.append("cycle")
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


class _FakeSeason:
    def __init__(self, frozen: bool = False, phase: str = "started") -> None:
        self.frozen = frozen
        self.phase = phase


class _FakeKingdoms:
    """A fake kingdoms service exposing one season (or None)."""

    def __init__(self, season: _FakeSeason | None) -> None:
        self._season = season
        self.config: Any = None

    async def current_season(self) -> Any:
        return self._season


class _FakeTime:
    """A fake time machine collecting the snapshot calls."""

    def __init__(self) -> None:
        self.snapshots: list[dict[str, object]] = []

    async def create_snapshot(self, label: str, *, moment: str | None = None) -> dict[str, object]:
        self.snapshots.append({"label": label, "moment": moment})
        return {"_id": f"tm-{len(self.snapshots)}", "label": label, "moment": moment}


class _FakeBot:
    def __init__(
        self,
        guild: _FakeGuild,
        events: _FakeEvents,
        kingdoms: _FakeKingdoms | None = None,
        time_service: _FakeTime | None = None,
    ) -> None:
        self.guilds = [guild]
        self.kingdoms_events_service = events
        self.kingdoms_service = kingdoms
        self.kingdoms_time_service = time_service
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
        async def run_due(self, now: Any, *, snapshotter: Any = None) -> list[dict[str, object]]:
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


def test_report_text_renders_the_season_end() -> None:
    """The end report announces the verdict (or the ShowMatch tie)."""
    from kingdoms.mods.kingdoms.kingdom_events_loop import _report_text, _strings

    fr = _strings("fr")
    end = {
        "kind": "end",
        "winner_name": "Aquitaine",
        "showmatch": False,
        "cancelled_attacks": 1,
    }
    assert "Fin de saison" in _report_text(fr, end)
    assert "Aquitaine" in _report_text(fr, end)
    tie = dict(end, winner_name=None, showmatch=True)
    assert "ShowMatch" in _report_text(fr, tie)


async def test_scheduler_skips_a_frozen_season() -> None:
    """Drasah's pause: a frozen season replays no slot at all."""
    channel = _FakeChannel()
    events = _FakeEvents([{"kind": "cycle", "cycle": 2, "gaia_new_maps": []}])
    bot = _FakeBot(_FakeGuild(channel), events, _FakeKingdoms(_FakeSeason(frozen=True)))
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    for _ in range(10):
        await asyncio.sleep(0)
    assert events.calls == 0
    assert channel.sent == []
    await stop_kingdoms_event_scheduler()


async def test_scheduler_skips_a_setup_season() -> None:
    """The setup phase replays no slot — no Gaia draw before the start."""
    channel = _FakeChannel()
    events = _FakeEvents([{"kind": "cycle", "cycle": 2, "gaia_new_maps": []}])
    bot = _FakeBot(_FakeGuild(channel), events, _FakeKingdoms(_FakeSeason(phase="setup")))
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    for _ in range(10):
        await asyncio.sleep(0)
    assert events.calls == 0
    await stop_kingdoms_event_scheduler()


async def test_scheduler_snapshots_each_replayed_slot() -> None:
    """The time-machine hook photographs the state before each slot."""
    channel = _FakeChannel()
    events = _FakeEvents([{"kind": "cycle", "cycle": 2, "gaia_new_maps": []}])
    time_service = _FakeTime()
    bot = _FakeBot(
        _FakeGuild(channel), events, _FakeKingdoms(_FakeSeason()), time_service
    )
    assert start_kingdoms_event_scheduler(bot, tick_seconds=0) is True
    for _ in range(20):
        await asyncio.sleep(0)
        if time_service.snapshots:
            break
    assert events.hooked == ["cycle"]
    assert time_service.snapshots == [{"label": "avant cycle", "moment": "cycle"}]
    await stop_kingdoms_event_scheduler()


# ----------------------------------------------------------------------
# restructuration-serveur-v1 c (Drasah 19/10): the FFA's own ephemeral
# salon — opens under « Déclarer une attaque », purged with the FFA.
# ----------------------------------------------------------------------


class _FfaService:
    """A fake FFA service recording the salon ref."""

    def __init__(self) -> None:
        self.recorded: list[tuple[Any, int]] = []

    async def record_salon(self, ffa: Any, channel_id: int) -> None:
        self.recorded.append((ffa, channel_id))


class _Ffa:
    def __init__(self, salon_id: int | None = None, finished: bool = False) -> None:
        self.salon_id = salon_id
        self.finished = finished


async def test_ffa_salon_opens_under_the_declare_channel() -> None:
    """_open_ffa_salon_safe creates the salon + base message + ref (c)."""
    import discord

    from kingdoms.mods.kingdoms.kingdom_events_loop import _open_ffa_salon_safe
    from tests.mocks.discord_mock import MockGuild

    guild = MockGuild()
    declare = await guild.create_text_channel("Déclarer une attaque")
    service = _FfaService()
    ffa = _Ffa()
    view = discord.ui.View(timeout=None)
    await _open_ffa_salon_safe(
        guild, service, ffa, {"ffa_salon_title": "🗺️ Exploration FFA"}, "go", view
    )
    salons = [c for c in guild.text_channels if c.name == "🗺️ Exploration FFA"]
    assert len(salons) == 1
    salon = salons[0]
    # the salon sits right under « Déclarer une attaque »
    assert salon.category == declare.category
    # the ref rides ffa.salon_id, and @everyone cannot write (Check 75)
    assert service.recorded == [(ffa, salon.id)]
    everyone_overwrite = salon.overwrites.get(guild.default_role)
    assert everyone_overwrite is not None
    assert everyone_overwrite.view_channel is True
    assert everyone_overwrite.send_messages is False
    me_overwrite = salon.overwrites.get(guild.me)
    assert me_overwrite is not None and me_overwrite.send_messages is True
    # the base message repeats the announcement with the join view
    assert len(salon.messages) == 1
    assert salon.messages[0].content == "go"
    assert salon.messages[0].view is view


async def test_ffa_salon_purges_a_leftover_before_reopening() -> None:
    """A stale same-slug salon from last Saturday never survives."""
    from kingdoms.mods.kingdoms.kingdom_events_loop import _open_ffa_salon_safe
    from tests.mocks.discord_mock import MockGuild

    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    leftover = await guild.create_text_channel("🗺️ Exploration FFA")
    await _open_ffa_salon_safe(
        guild, _FfaService(), _Ffa(), {"ffa_salon_title": "🗺️ Exploration FFA"}, "go", None
    )
    salons = [c for c in guild.text_channels if c.name == "🗺️ Exploration FFA"]
    assert len(salons) == 1
    assert salons[0] is not leftover  # the leftover was deleted, not adopted


async def test_ffa_salon_purge_is_idempotent_and_ref_driven() -> None:
    """The purge deletes the salon by its ref, then no-ops."""
    from kingdoms.mods.kingdoms.kingdom_events_loop import _ffa_salon_purge_safe
    from tests.mocks.discord_mock import MockGuild

    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    salon = await guild.create_text_channel("🗺️ Exploration FFA")
    ffa = _Ffa(salon_id=salon.id, finished=True)
    await _ffa_salon_purge_safe(guild, ffa)
    assert salon.id not in {c.id for c in guild.text_channels}
    # a second pass (dead ref) is a no-op, and no ref means no purge
    await _ffa_salon_purge_safe(guild, ffa)
    await _ffa_salon_purge_safe(guild, _Ffa(salon_id=None))
    await _ffa_salon_purge_safe(None, ffa)
