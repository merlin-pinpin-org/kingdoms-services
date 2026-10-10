"""Kingdoms mod diplomacy & marriages - unit tests (kingdoms-services#160, T6).

Reference §18-§19 behind an in-memory store: the civilization
conditions engine (map keys, map types, unlock chains, the kingdom
name rule), the marriages securing a civilization (D34/D45/D53), and
the combat-defeat marriage loss at the next recalculation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import LordLockedError
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
    MarriageExclusivityError,
    UnknownCivilizationError,
)
from kingdoms.mods.kingdoms.models import LordRole

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

    async def draw_one(self, kingdom_id: str, map_key: str) -> None:
        """Give one territory to a kingdom (catalog-validated)."""
        await self.territories.draw_map_for(kingdom_id, map_key)


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


async def test_marry_refuses_a_civilization_claimed_elsewhere() -> None:
    """D74: an active marriage claims its civilization for everyone."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.diplomacy.marry("king-a", "celtes")
    with pytest.raises(MarriageExclusivityError):
        await bundle.diplomacy.marry("king-b", "celtes")
    with pytest.raises(MarriageExclusivityError):
        await bundle.diplomacy.arranged_marriage("king-b", "celtes")


async def test_arranged_marriage_bypasses_the_stock_and_locks_six_hours() -> None:
    """D60/D74: no capacity consumed, instant exclusivity, 6h combat lock."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    kingdom.marriage_capacity = 0  # an empty stock never blocks the arranged path
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert await bundle.diplomacy.arranged_marriage("king-a", "celtes", now=now) == "celtes"
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.marriage_locked_until == int(
        (now + timedelta(hours=6)).timestamp()
    )
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert "celtes" in kingdom.secured_civilizations


async def test_standard_marriage_locks_twenty_four_hours() -> None:
    """D59: a standard marriage locks the lord for 24 real hours."""
    bundle = Bundle()
    await bundle.launch_season()
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    await bundle.diplomacy.marry("king-a", "celtes", now=now)
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.marriage_locked_until == int(
        (now + timedelta(hours=24)).timestamp()
    )


async def test_arranged_marriage_never_spends_on_refusal() -> None:
    """D60: every check runs before the payment - a refusal never debits."""
    bundle = Bundle()
    await bundle.launch_season()
    spent: list[tuple[str, int]] = []

    async def spend(player_id: str, cost: int) -> None:
        spent.append((player_id, cost))

    await bundle.diplomacy.arranged_marriage("king-a", "celtes", spend_points=spend)
    assert spent == [("king-a", 3)]
    # A second weds refused: one marriage per lord (D45), no debit.
    with pytest.raises(AlreadyMarriedError):
        await bundle.diplomacy.arranged_marriage("king-a", "shu", spend_points=spend)
    assert spent == [("king-a", 3)]


async def test_marriage_lock_blocks_attack_and_defense() -> None:
    """D59/D60: a freshly wed lord can neither attack nor defend."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.diplomacy.arranged_marriage("king-a", "celtes")
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    aquitaine = await bundle.kingdom_id("Aquitaine")
    territories = await bundle.territories.territories()
    target = next(t for t in territories if t.owner_kingdom_id == bourgogne)
    with pytest.raises(LordLockedError):
        await bundle.attacks.declare_attack("king-a", target.map_key, "aoe2de://x")
    # The defense side is locked too: an Aquitaine lord weds, then
    # cannot answer the attack on his kingdom.
    home = next(t.map_key for t in territories if t.owner_kingdom_id == aquitaine)
    attack = await bundle.attacks.declare_attack("king-b", home, "aoe2de://y")
    await bundle.diplomacy.arranged_marriage("lord-a", "shu")
    with pytest.raises(LordLockedError):
        await bundle.attacks.respond_defense(attack.id, "lord-a")


async def test_defeat_drops_the_marriage_at_the_next_recalculation() -> None:
    """D34: a combat defeat flags the kingdom; the marriage drops next pass."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.diplomacy.marry("king-a", "celtes")
    await bundle.diplomacy.record_defeat(aquitaine)
    reports = await bundle.diplomacy.recalculate()
    assert "celtes" not in reports["Aquitaine"]
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.married_civilization is None
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.pending_marriage_losses == []


async def test_starting_draft_draws_random_civs_without_duplicates() -> None:
    """The starting draft: every new kingdom draws civs at random and
    no civilization is ever shared between two kingdoms."""
    config = _config().model_copy(
        update={
            "starting_civilizations": 2,
            "civilizations": (
                *_config().civilizations,
                CivilizationCondition(key="francs", display_name="Francs"),
                CivilizationCondition(key="britanniques", display_name="Britanniques"),
                CivilizationCondition(key="azteques", display_name="Aztèques"),
                CivilizationCondition(key="perses", display_name="Perses"),
            ),
        }
    )
    bundle = Bundle(config=config)
    await bundle.launch_season()
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    assert len(kingdoms) == 2
    drawn: list[str] = []
    for kingdom in kingdoms:
        assert len(kingdom.civilizations) == 2
        drawn.extend(kingdom.civilizations)
    assert len(set(drawn)) == len(drawn)  # no duplicates between kingdoms
    gaia = next(k for k in await bundle.kingdoms.kingdoms() if k.is_gaia)
    assert gaia.civilizations == []  # Gaïa never draws


async def test_imposed_launch_drafts_the_kingdoms_civs() -> None:
    """The imposed mode drafts at launch too: 8 civs each, all distinct."""
    catalog = tuple(
        CivilizationCondition(key=f"civ-{index}", display_name=f"Civ {index}")
        for index in range(20)
    )
    config = _config().model_copy(
        update={"starting_civilizations": 8, "civilizations": catalog}
    )
    bundle = Bundle(config=config)
    await bundle.kingdoms.launch(imposed_names=["Aquitaine", "Bourgogne"])
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    assert len(kingdoms) == 2
    drawn = [civ for kingdom in kingdoms for civ in kingdom.civilizations]
    assert len(drawn) == 16
    assert len(set(drawn)) == 16  # no duplicates between kingdoms


async def test_recalculate_keeps_the_unconditioned_draft() -> None:
    """A drafted civilization with no acquisition condition yet survives
    the Lord's Day recalculation (conditions come later)."""
    config = _config().model_copy(
        update={
            "starting_civilizations": 2,
            "civilizations": (
                CivilizationCondition(key="celtes", display_name="Celtes", requires_map_keys=("black-forest",)),
                CivilizationCondition(key="francs", display_name="Francs"),
                CivilizationCondition(key="britanniques", display_name="Britanniques"),
                CivilizationCondition(key="azteques", display_name="Aztèques"),
            ),
        }
    )
    bundle = Bundle(config=config)
    await bundle.launch_season()
    before = {
        k.id: list(k.civilizations)
        for k in await bundle.kingdoms.kingdoms()
        if not k.is_gaia
    }
    assert all(civs for civs in before.values())
    reports = await bundle.diplomacy.recalculate()
    unconditioned = {"francs", "britanniques", "azteques"}
    for kingdom in [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]:
        kept = set(before[kingdom.id]) & unconditioned
        assert kept <= set(reports[kingdom.name])  # the draft without conditions persists
        dropped = set(before[kingdom.id]) - unconditioned
        assert not dropped & set(reports[kingdom.name])  # a conditioned civ follows its condition
