"""Kingdoms mod diplomacy & marriages - unit tests (kingdoms-services#160, T6).

Reference §18-§19 behind an in-memory store: the civilization
conditions engine (map keys, map types, unlock chains, the kingdom
name rule), the marriages securing a civilization (D34/D45/D53), and
the combat-defeat marriage loss at the next recalculation.
"""
from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.config import (
    CivilizationCondition,
    Epoch,
    KingdomsSeasonConfig,
    default_map_catalog,
)
from kingdoms.mods.kingdoms.diplomacy import (
    AlreadyMarriedError,
    DiplomacyService,
    MarriageCapacityError,
    UnknownCivilizationError,
)
from kingdoms.mods.kingdoms.models import LordRole
from kingdoms.mods.kingdoms.territories import MapPoolExhaustedError

from .test_kingdoms_attacks import Bundle as _AttackBundle


def _config() -> KingdomsSeasonConfig:
    """A config with the CIVILIZATIONS.md reference conditions."""
    return KingdomsSeasonConfig(
        maps=default_map_catalog(),
        ages=(
            Epoch(key="dark_age", display_name="Âge sombre", gaia_ai_level=2, tech_points=0, extra_marriages=1),
        ),
        civilizations=(
            CivilizationCondition(key="celtes", display_name="Celtes", requires_map_keys=("black-forest",)),
            CivilizationCondition(key="chinois", display_name="Chinois", requires_map_types=("water",)),
            CivilizationCondition(key="shu", display_name="Shu", requires_civilization="chinois"),
            CivilizationCondition(key="wei", display_name="Wei", requires_civilization="chinois"),
            CivilizationCondition(key="wu", display_name="Wu", requires_civilization="chinois"),
            CivilizationCondition(key="hongrois", display_name="Hongrois", requires_kingdom_name_pattern="(?i).*hen.*"),
        ),
    )


class Bundle(_AttackBundle):
    """The attack bundle extended with the diplomacy service."""

    def __init__(self, config=None) -> None:
        super().__init__(config or _config())
        self.diplomacy = DiplomacyService(self.store, self.config, self.kingdoms, self.territories)  # type: ignore[arg-type]

    async def renounce_map(self, kingdom_id: str, map_key: str) -> None:
        """Move any owned copy of the map away from the kingdom (isolation helper)."""
        for territory in await self.territories.territories():
            if territory.map_key == map_key and territory.owner_kingdom_id == kingdom_id:
                await self.territories.transfer(territory.id, "gaia")

    async def draw_one(self, kingdom_id: str, map_key: str) -> None:
        """Give one territory to a kingdom (catalog-validated).

        The launch draw may already own the map: free-founding mode
        means ownership, not freshness, unlocks the civilization.
        """
        try:
            await self.territories.draw_map_for(kingdom_id, map_key)
        except MapPoolExhaustedError:
            territory = next(
                t for t in await self.territories.territories() if t.map_key == map_key
            )
            if territory.owner_kingdom_id != kingdom_id:
                await self.territories.transfer(territory.id, kingdom_id)


async def test_map_key_condition_unlocks_celtes() -> None:
    """D32: owning the Black Forest unlocks the Celts."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.draw_one(aquitaine, "black-forest")
    reports = await bundle.diplomacy.recalculate()
    assert "celtes" in reports["Aquitaine"]
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert "celtes" in kingdom.civilizations


async def test_losing_the_territory_drops_the_civilization() -> None:
    """D32: a lost territory drops its civilization at the next pass."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    await bundle.draw_one(aquitaine, "black-forest")
    await bundle.diplomacy.recalculate()
    territory = next(
        t for t in await bundle.territories.territories() if t.map_key == "black-forest"
    )
    await bundle.territories.transfer(territory.id, bourgogne)
    reports = await bundle.diplomacy.recalculate()
    assert "celtes" not in reports["Aquitaine"]
    assert "celtes" in reports["Bourgogne"]


async def test_water_maps_unlock_chinese_and_its_chain() -> None:
    """D33: a water map unlocks the Chinese and the Shu/Wei/Wu chain."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.draw_one(aquitaine, "islands")
    reports = await bundle.diplomacy.recalculate()
    assert {"chinois", "shu", "wei", "wu"} <= set(reports["Aquitaine"])


async def test_kingdom_name_pattern_unlocks_hongrois() -> None:
    """The referential name rule: a kingdom named like the pattern unlocks it."""
    bundle = Bundle()
    await bundle.kingdoms.launch(imposed_names=["Kuchenland", "Bourgogne"])
    await bundle.kingdoms.enroll("king-h", "King H", LordRole.LORD, kingdom_name="Kuchenland")
    await bundle.diplomacy.recalculate()
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.name == "Kuchenland")
    assert "hongrois" in kingdom.civilizations


async def test_marry_secures_a_civilization_once_per_lord() -> None:
    """D34: one marriage per lord; the civ joins the kingdom's list."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.draw_one(aquitaine, "black-forest")
    await bundle.diplomacy.recalculate()
    assert await bundle.diplomacy.marry("king-a", "celtes") == "celtes"
    with pytest.raises(AlreadyMarriedError):
        await bundle.diplomacy.marry("king-a", "shu")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert "celtes" in kingdom.secured_civilizations
    # The marriage survives losing the territory (it is secured).
    territory = next(
        t for t in await bundle.territories.territories() if t.map_key == "black-forest"
    )
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.territories.transfer(territory.id, bourgogne)
    await bundle.diplomacy.recalculate()
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert "celtes" in kingdom.civilizations


async def test_marry_respects_the_capacity() -> None:
    """D53: the kingdom's marriage capacity bounds the marriages."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    kingdom.marriage_capacity = 1
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    await bundle.diplomacy.marry("king-a", "celtes")
    with pytest.raises(MarriageCapacityError):
        await bundle.diplomacy.marry("lord-a", "shu")


async def test_marry_refuses_unknown_civilizations() -> None:
    """An unlisted civilization never marries."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(UnknownCivilizationError):
        await bundle.diplomacy.marry("king-a", "mayas")


async def test_arranged_marriage_pays_through_the_seam() -> None:
    """D45: the arranged marriage spends through the seam and weds."""
    bundle = Bundle()
    await bundle.launch_season()
    spent: list[tuple[str, int]] = []

    async def spend(player_id: str, cost: int) -> None:
        spent.append((player_id, cost))

    result = await bundle.diplomacy.arranged_marriage("king-a", "celtes", spend_points=spend)
    assert result == "celtes"
    assert spent == [("king-a", bundle.config.technologies.mariage_arrange)]


async def test_defeat_drops_the_marriage_at_the_next_recalculation() -> None:
    """D34: a combat defeat flags the kingdom; the marriage drops next pass."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.renounce_map(aquitaine, "black-forest")
    await bundle.diplomacy.marry("king-a", "celtes")
    await bundle.diplomacy.record_defeat(aquitaine)
    reports = await bundle.diplomacy.recalculate()
    assert "celtes" not in reports["Aquitaine"]
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.married_civilization is None
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.pending_marriage_losses == []


async def test_starting_civilizations_seed_the_kingdoms() -> None:
    """D32: the admin parameter seeds the first playable lists."""
    config = _config()
    config = config.model_copy(update={"starting_civilizations": 2})
    bundle = Bundle(config=config)
    await bundle.launch_season()
    assigned = await bundle.diplomacy.assign_starting_civilizations()
    assert assigned
    assert all(len(civs) == 2 for civs in assigned.values())
