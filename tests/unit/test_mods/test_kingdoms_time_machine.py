"""Kingdoms time machine — unit tests (chantier E, 2026-10-11).

The game clock behind the Machine à temps salon: the status line
(cycle, age, the upcoming key moments), the reversible fast-forward
(+1/+3/+7 days replay the real ``run_due`` orchestrator at a future
date), the jumps to the key moments, and the true rewind through
snapshots — a restore replays back the exact stored state and is
itself restorable.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import AttackService
from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, default_map_catalog
from kingdoms.mods.kingdoms.events import EventService
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService
from kingdoms.mods.kingdoms.time_machine import (
    TimeMachineError,
    TimeMachineService,
)

from .test_kingdoms_events import Bundle as _EventsBundle
from .test_kingdoms_events import MemoryStore as _EventsStore
from .test_kingdoms_events import _config


class WipeStore(_EventsStore):
    """The events store with the Mongo-grade wipe (D38): a restore must
    clear territories/attacks/technologies/showmatches too, or the
    replay would inherit the fast-forward's documents."""

    async def wipe_season_data(self) -> None:
        await super().wipe_season_data()
        self.territories.clear()
        self.attacks.clear()
        self.technologies.clear()
        self.showmatches.clear()


class Bundle(_EventsBundle):
    """The events bundle rebuilt over the full-wipe store plus the clock."""

    def __init__(self, config: KingdomsSeasonConfig | None = None) -> None:
        cfg = config or _config()
        super().__init__(cfg)
        self.store = WipeStore()
        self.kingdoms = KingdomsService(self.store, cfg)  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, cfg, self.kingdoms)  # type: ignore[arg-type]
        self.attacks = AttackService(self.store, cfg, self.kingdoms, self.territories)  # type: ignore[arg-type]
        self.events = EventService(self.store, cfg, self.kingdoms, self.territories, self.attacks)  # type: ignore[arg-type]
        self.time = TimeMachineService(self.store, cfg, self.kingdoms, self.events)


def _full_catalog_weeks(weeks: int) -> KingdomsSeasonConfig:
    """The full 26-map catalog (a fast-forward must not exhaust the pool)."""
    return _config().model_copy(update={"weeks": weeks, "maps": default_map_catalog()})


MONDAY = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


async def test_status_reports_the_game_clock() -> None:
    """The status shows cycle 1, the current age and the next moments."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(MONDAY)
    status = await bundle.time.status(now=datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
    assert status["season"] is not None
    assert status["cycle"] == 1
    assert status["weeks"] == 4
    assert status["age"] == "dark_age"
    assert status["phase"] == "setup"
    assert status["finished"] is False
    assert status["next_cycle_at"] == datetime(2026, 10, 11, 21, 30, tzinfo=UTC)
    assert status["next_age_at"] == datetime(2026, 10, 13, 22, 0, tzinfo=UTC)
    # The verdict: the LAST cycle slot (Sunday 01/11 23:30 Paris) + 29 min.
    assert status["season_end_at"] == datetime(2026, 11, 1, 22, 59, tzinfo=UTC)
    assert status["snapshot_count"] == 0


async def test_status_without_season_is_empty_but_alive() -> None:
    """No season: the clock reports it without raising."""
    bundle = Bundle()
    status = await bundle.time.status(now=MONDAY)
    assert status["season"] is None
    assert status["snapshot_count"] == 0
    with pytest.raises(TimeMachineError):
        await bundle.time.jump_to("next_cycle", now=MONDAY)


async def test_snapshot_restore_round_trip_is_exact() -> None:
    """A restore replays back the exact stored state, verbatim."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", now=datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    )
    snapshot = await bundle.time.create_snapshot("s1", now=MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    season_before = season.model_dump()
    attacks_before = sorted(bundle.store.attacks)
    territories_before = sorted(bundle.store.territories)
    # Mutate: a second attack and a territory transfer.
    await bundle.attacks.declare_attack(
        "lord-a", (await bundle.owner_maps(bourgogne))[1], "aoe2de://lobby2",
        now=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    )
    result = await bundle.time.restore(snapshot["_id"], now=datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    assert result["restored"] == snapshot["_id"]
    assert sorted(bundle.store.attacks) == attacks_before
    assert sorted(bundle.store.territories) == territories_before
    season_after = await bundle.kingdoms.current_season()
    assert season_after is not None
    assert season_after.model_dump() == season_before
    # The restore auto-snapshotted the pre-restore state: reversible twice.
    assert result["backup"] in [doc["_id"] for doc in await bundle.time.snapshots()]


async def test_advance_fast_forwards_the_calendar() -> None:
    """+7 days replay the real slots: the age switches, the cycle recharges."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    result = await bundle.time.advance(7, now=MONDAY)
    assert [r["kind"] for r in result["reports"]] == ["age", "cycle"]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    assert season.current_age_key == "feudal_age"
    gaia_id = next(k.id for k in await bundle.kingdoms.kingdoms() if k.is_gaia)
    gaia_maps = [
        t.map_key
        for t in await bundle.territories.territories()
        if t.owner_kingdom_id == gaia_id
    ]
    # 8 maps drawn at the initial distribution + 8 at the switch.
    assert len(gaia_maps) == 16


async def test_advance_past_the_end_finishes_the_season() -> None:
    """Fast-forwarding beyond the last Sunday applies the 23:59 verdict."""
    bundle = Bundle(_full_catalog_weeks(2))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    result = await bundle.time.advance(14, now=MONDAY)
    assert [r["kind"] for r in result["reports"]] == ["age", "cycle", "age", "end"]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.finished is True
    assert season.phase == "ended"
    assert await bundle.events.run_due(MONDAY + timedelta(days=20)) == []


async def test_restore_reverts_a_fast_forward() -> None:
    """The auto-snapshot taken before a jump restores the pre-jump clock."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    territories_before = sorted(bundle.store.territories)
    result = await bundle.time.advance(7, now=MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    await bundle.time.restore(result["backup"], now=MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 1
    assert season.current_age_key == "dark_age"
    assert sorted(bundle.store.territories) == territories_before


async def test_jump_to_the_key_moments() -> None:
    """The three jumps land exactly on cycle, age and season end."""
    bundle = Bundle(_full_catalog_weeks(2))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    jumped = await bundle.time.jump_to("next_age", now=MONDAY)
    assert [r["kind"] for r in jumped["reports"]] == ["age"]
    assert jumped["target"] == datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
    jumped = await bundle.time.jump_to("next_cycle", now=jumped["target"])
    assert [r["kind"] for r in jumped["reports"]] == ["cycle"]
    assert jumped["target"] == datetime(2026, 10, 11, 21, 30, tzinfo=UTC)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    # The last jump crosses the end Sunday: age switch then the 23:59 verdict.
    jumped = await bundle.time.jump_to("season_end", now=jumped["target"])
    assert [r["kind"] for r in jumped["reports"]] == ["age", "end"]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.finished is True
    # Once finished, the same jump is refused (the moment passed).
    with pytest.raises(TimeMachineError):
        await bundle.time.jump_to("season_end", now=datetime(2026, 10, 20, 12, 0, tzinfo=UTC))


async def test_jump_to_unknown_moment_or_past_raises() -> None:
    """Bad moment names and non-positive advances are refused loudly."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.jump_to("next_birthday", now=MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.advance(0, now=MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.advance(-3, now=MONDAY)


async def test_restore_unknown_snapshot_raises() -> None:
    """An unknown snapshot id is a loud error, not a silent wipe."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(TimeMachineError):
        await bundle.time.restore("tm-nope", now=MONDAY)
