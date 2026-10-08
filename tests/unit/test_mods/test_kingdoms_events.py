"""Kingdoms mod weekly events - unit tests (kingdoms-services#159, T5).

Reference §15-§17 behind an in-memory store: the Sunday cycle switch
with the Lord's Day Gaia maps (D1/D31), the Wednesday age switch with
the epoch bonuses (D18/D51/D53), the Saturday exploration FFA rewards
(D29/D30), and the restart-safe catch-up of ``run_due`` (D1).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.config import (
    Epoch,
    KingdomsSeasonConfig,
    default_map_catalog,
)
from kingdoms.mods.kingdoms.events import EventService, SeasonExhaustedError

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
    """D1: a restart replays every missed cycle from the season state."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.rewind_season(2)
    reports = await bundle.events.run_due(datetime.now(tz=UTC))
    assert [r["cycle"] for r in reports] == [1, 2]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    # Catching up again is a no-op: the state is already current.
    assert await bundle.events.run_due(datetime.now(tz=UTC)) == []
