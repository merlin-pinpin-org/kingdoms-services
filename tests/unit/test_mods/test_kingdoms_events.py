"""Kingdoms mod weekly events - unit tests (kingdoms-services#159, T5).

Reference §15-§17 behind an in-memory store: the Sunday cycle switch
with the Lord's Day Gaia maps (D1/D31), the Wednesday age switch with
the epoch bonuses (D18/D51/D53), the Saturday exploration FFA rewards
(D29/D30), and the restart-safe catch-up of ``run_due`` (D1).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from kingdoms.mods.kingdoms.config import (
    Epoch,
    KingdomsSeasonConfig,
    default_map_catalog,
)
from kingdoms.mods.kingdoms.events import EventService, SeasonExhaustedError, lords_day_window

from .test_kingdoms_attacks import Bundle as _AttackBundle
from .test_kingdoms_service import MemoryStore as _BaseStore


class MemoryStore(_BaseStore):
    """The enrollment store extended with the T3/T4 collections."""

    def __init__(self) -> None:
        super().__init__()
        self.territories: dict[str, dict] = {}
        self.attacks: dict[str, dict] = {}
        self.technologies: dict[str, dict] = {}
        self.showmatches: dict[str, dict] = {}

    async def upsert_territory(self, document: dict) -> None:
        self.territories[document["_id"]] = document

    async def find_territories(self) -> list[dict]:
        return list(self.territories.values())

    async def delete_territory(self, territory_id: str) -> None:
        self.territories.pop(territory_id, None)

    async def upsert_attack(self, document: dict) -> None:
        self.attacks[document["_id"]] = document

    async def find_attacks(self) -> list[dict]:
        return list(self.attacks.values())

    async def delete_attack(self, attack_id: str) -> None:
        self.attacks.pop(attack_id, None)

    async def upsert_technology(self, document: dict) -> None:
        self.technologies[document["_id"]] = document

    async def find_technologies(self) -> list[dict]:
        return list(self.technologies.values())

    async def upsert_showmatch(self, document: dict) -> None:
        self.showmatches[document["_id"]] = document

    async def find_showmatches(self) -> list[dict]:
        return list(self.showmatches.values())


def _config() -> KingdomsSeasonConfig:
    """A small-catalog config: fast to exhaust, epochs 2-3-5-5."""
    return KingdomsSeasonConfig(
        maps=default_map_catalog()[:18],
        ages=(
            Epoch(key="dark_age", display_name="Âge sombre", gaia_ai_level=2, tech_points=0, extra_marriages=1),
            Epoch(key="feudal_age", display_name="Âge féodal", gaia_ai_level=3, tech_points=1, extra_marriages=1),
            Epoch(key="castle_age", display_name="Âge des châteaux", gaia_ai_level=5, tech_points=2, extra_marriages=1),
            Epoch(key="imperial_age", display_name="Âge impérial", gaia_ai_level=5, tech_points=2, extra_marriages=1),
        ),
    )


class Bundle(_AttackBundle):
    """The attack bundle extended with the event service."""

    def __init__(self, config=None) -> None:
        super().__init__(config or _config())
        self.events = EventService(self.store, self.config, self.kingdoms, self.territories, self.attacks)  # type: ignore[arg-type]

    async def set_started_at(self, started_at: datetime) -> None:
        """Pin the season start (calendar cadence tests)."""
        season = await self.kingdoms.current_season()
        assert season is not None
        season.started_at = started_at
        await self.store.upsert_season(season.to_mongo())

    async def rewind_season(self, weeks: int) -> None:
        """Move the season start ``weeks`` weeks back (restart simulation)."""
        season = await self.kingdoms.current_season()
        assert season is not None
        season.started_at = season.started_at - timedelta(weeks=weeks)
        await self.store.upsert_season(season.to_mongo())


async def test_cycle_switch_recharges_budgets_and_adds_gaia_maps() -> None:
    """D1/D31: the switch recharges the budgets and draws 8 fresh Gaia maps."""
    bundle = Bundle()
    await bundle.launch_season()
    gaia_id = next(k.id for k in await bundle.kingdoms.kingdoms() if k.is_gaia)
    report = await bundle.events.run_cycle_switch(seed=1)
    assert report["cycle"] == 1
    gaia_maps = [
        t.map_key
        for t in await bundle.territories.territories()
        if t.owner_kingdom_id == gaia_id
    ]
    assert len(gaia_maps) == 8
    assert report["gaia_new_maps"] == gaia_maps
    # A consumed budget is back.
    lords = await bundle.kingdoms.lords()
    assert all(lord.attack_used == 0 and lord.defense_used == 0 for lord in lords)


async def test_cycle_switch_exhausts_the_pool_loudly() -> None:
    """D31: a too-small catalog raises instead of drawing duplicates."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(SeasonExhaustedError):
        for _ in range(10):
            await bundle.events.run_cycle_switch(seed=1)


async def test_age_switch_progresses_and_distributes_bonuses() -> None:
    """D18/D51/D53: the ages scale 2-3-5-5, tech points and capacity grow."""
    bundle = Bundle()
    await bundle.launch_season()
    reports = [await bundle.events.run_age_switch() for _ in range(5)]
    assert [r["gaia_ai_level"] for r in reports[:3]] == [3, 5, 5]
    assert reports[3]["age"] == "imperial_age"  # the last age is a no-op
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    # 1 (launch) + 1 (feudal) + 2 (castle) + 2 (imperial) tech points.
    assert all(k.tech_points_bank == 5 for k in kingdoms)
    assert all(k.marriage_capacity == 4 for k in kingdoms)


async def test_exploration_rewards_and_map_to_the_winner() -> None:
    """D29: 1 tech to the first kingdom and the drawn map as territory."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    report = await bundle.events.run_exploration([aquitaine, bourgogne], seed=1)
    settings = bundle.config.events
    assert report["rewards"] == {
        aquitaine: settings.exploration_first_tech,
        bourgogne: settings.exploration_second_tech,
    }
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert kingdom.tech_points_bank == settings.exploration_first_tech
    assert report["map"] in await bundle.owner_maps(aquitaine)


async def test_exploration_without_participant_leaves_the_map_to_gaia() -> None:
    """D30: nobody plays - the map goes to Gaia and no one gains tech."""
    bundle = Bundle()
    await bundle.launch_season()
    gaia_id = next(k.id for k in await bundle.kingdoms.kingdoms() if k.is_gaia)
    report = await bundle.events.run_exploration([], seed=1)
    assert report["winners"] == [gaia_id]
    assert report["map"] in [
        t.map_key for t in await bundle.territories.territories() if t.owner_kingdom_id == gaia_id
    ]
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    assert all(k.tech_points_bank == 0 for k in kingdoms)


async def test_run_due_catches_up_after_a_restart() -> None:
    """D1: a restart replays every missed slot in calendar order.

    Started Monday 2026-10-05 12:00 UTC, "now" Monday 2026-10-19
    13:00 UTC: the missed slots are the age switches Wed 7 and Wed 14
    (00:00 Paris = Tue 22:00 UTC) and the cycle switches Sun 11 and
    Sun 18 (23:30 Paris = 21:30 UTC) — interleaved chronologically.
    """
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(datetime(2026, 10, 5, 12, 0, tzinfo=UTC))
    reports = await bundle.events.run_due(datetime(2026, 10, 19, 13, 0, tzinfo=UTC))
    assert [r["kind"] for r in reports] == ["age", "cycle", "age", "cycle"]
    assert [r["cycle"] for r in reports if r["kind"] == "cycle"] == [1, 2]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    assert season.current_age_key == "castle_age"
    # Catching up again is a no-op: the state is already current.
    assert await bundle.events.run_due(datetime(2026, 10, 19, 13, 0, tzinfo=UTC)) == []


async def test_first_cycle_is_the_first_sunday_after_start() -> None:
    """Drasah's cadence: cycle 1 = the first Sunday 23:30 after the start."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(datetime(2026, 10, 10, 12, 0, tzinfo=UTC))  # Saturday
    reports = await bundle.events.run_due(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))
    assert [r["kind"] for r in reports] == ["cycle"]
    assert reports[0]["cycle"] == 1


async def test_first_age_is_the_first_wednesday_after_start() -> None:
    """The age switch lands on the first Tuesday→Wednesday midnight."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(datetime(2026, 10, 6, 12, 0, tzinfo=UTC))  # Tuesday
    reports = await bundle.events.run_due(datetime(2026, 10, 7, 23, 0, tzinfo=UTC))
    assert [r["kind"] for r in reports] == ["age"]
    assert reports[0]["age"] == "feudal_age"


async def test_cycle_switch_recomputes_the_alliances() -> None:
    """D51: every cycle switch recomputes the diplomacy alliances."""
    bundle = Bundle()
    await bundle.launch_season()

    class _CountingDiplomacy:
        calls: ClassVar[int] = 0

        async def recalculate(self) -> dict[str, list[str]]:
            _CountingDiplomacy.calls += 1
            return {}

    bundle.events = EventService(  # type: ignore[arg-type]
        bundle.store,
        bundle.config,
        bundle.kingdoms,
        bundle.territories,
        bundle.attacks,
        _CountingDiplomacy(),
    )
    report = await bundle.events.run_cycle_switch(seed=1)
    assert report["alliances_recomputed"] is True
    assert _CountingDiplomacy.calls == 1


async def test_lords_day_window_is_the_cycle_slot_to_monday_morning() -> None:
    """The window opens Sunday 23:30 Paris and closes Monday 10:00."""
    config = _config()
    start = datetime(2026, 10, 11, 21, 30, tzinfo=UTC)  # Sun 23:30 Paris (CEST)
    end = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)  # Mon 10:00 Paris
    assert lords_day_window(config, start) == (start, end)
    inside = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    assert lords_day_window(config, inside) == (start, end)
    # After Monday 10:00 Paris the window is closed.
    after = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    assert lords_day_window(config, after) is None
    # A plain Tuesday afternoon is outside the window too.
    assert lords_day_window(config, datetime(2026, 10, 13, 12, 0, tzinfo=UTC)) is None


async def test_rollback_age_removes_the_last_epoch_bonuses() -> None:
    """The manual age-back seam undoes the tech and marriage bonuses."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.events.run_age_switch()  # dark_age -> feudal_age
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    assert all(k.marriage_capacity == 2 for k in kingdoms)  # 1 + 1 extra
    report = await bundle.events.rollback_age()
    assert report["age"] == "dark_age"
    assert report["tech_points"] == -1
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_age_key == "dark_age"
    rolled = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    assert all(k.marriage_capacity == 1 for k in rolled)
    assert all(k.tech_points_bank == 0 for k in rolled)


async def test_rollback_age_refuses_the_first_age() -> None:
    """The first age cannot be rolled back further."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(ValueError, match="first age"):
        await bundle.events.rollback_age()


async def test_enter_lords_day_phase_runs_the_switch_and_forces_the_window() -> None:
    """The manual phase runs the cycle switch and forces the window open."""
    bundle = Bundle()
    await bundle.launch_season()
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)  # a Saturday, no calendar window
    report = await bundle.events.enter_lords_day_phase(now=now, seed=1)
    assert report["cycle"] == 1
    assert report["alliances_recomputed"] is False  # no diplomacy service wired
    assert report["lords_day_forced"] is True
    # The forced window has the same shape as the real one: 10.5 hours.
    assert report["lords_day_forced_until"] == now + timedelta(hours=10, minutes=30)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.lords_day_forced_start == now
    assert season.lords_day_forced_end == now + timedelta(hours=10, minutes=30)
    # Exiting the phase closes the window.
    await bundle.events.exit_lords_day_phase()
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.lords_day_forced_start is None
    assert season.lords_day_forced_end is None
