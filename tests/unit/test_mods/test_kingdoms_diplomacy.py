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
    CivilizationNotInHandError,
    DiplomacyService,
    MarriageExclusivityError,
    NoArrangedTokenError,
    NoMarriagePointsError,
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

    async def patch_kingdom_of(self, player_id: str, **fields: int) -> None:
        """Patch the kingdom of a lord in the store (Paroisse stocks)."""
        lord = next(item for item in await self.kingdoms.lords() if item.id == player_id)
        kingdom = next(
            k for k in await self.kingdoms.kingdoms() if k.id == lord.kingdom_id
        )
        for key, value in fields.items():
            setattr(kingdom, key, value)
        await self.store.upsert_kingdom(kingdom.to_mongo())

    async def kingdom_by_id_of(self, player_id: str):
        """Return the fresh kingdom record of a lord."""
        lord = next(item for item in await self.kingdoms.lords() if item.id == player_id)
        return next(k for k in await self.kingdoms.kingdoms() if k.id == lord.kingdom_id)

    async def give(self, kingdom_name: str, *civs: str) -> None:
        """Hand civilizations to a kingdom (Drasah 2026-10-11 rule)."""
        kingdom = next(k for k in await self.kingdoms.kingdoms() if k.name == kingdom_name)
        kingdom.civilizations = [*kingdom.civilizations, *civs]
        await self.store.upsert_kingdom(kingdom.to_mongo())


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
    assert await bundle.diplomacy.marry("lord-a", "celtes") == "celtes"
    with pytest.raises(AlreadyMarriedError):
        await bundle.diplomacy.marry("lord-a", "shu")
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
    await bundle.give("Aquitaine", "celtes", "shu")
    await bundle.give("Bourgogne", "shu")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    kingdom.marriage_points = 1
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    await bundle.diplomacy.marry("lord-a", "celtes")
    # D59: one marriage consumes ONE point de mariage (a currency).
    await bundle.patch_kingdom_of("king-b", marriage_points=0)
    with pytest.raises(NoMarriagePointsError):
        await bundle.diplomacy.marry("lord-b", "shu")


async def test_marry_refuses_unknown_civilizations() -> None:
    """An unlisted civilization never marries."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(UnknownCivilizationError):
        await bundle.diplomacy.marry("lord-a", "mayas")


async def test_marry_requires_the_civilization_in_hand() -> None:
    """Drasah 2026-10-11: a standard marriage weds ONLY a civilization
    the kingdom holds in Alliances; the arranged path ignores the rule."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(CivilizationNotInHandError):
        await bundle.diplomacy.marry("lord-a", "chinois")  # not in the hand
    # The arranged marriage carries no hand constraint (D60): any
    # catalog civilization weds against one bought token.
    await bundle.patch_kingdom_of("king-b", arranged_marriage_stock=1)
    assert await bundle.diplomacy.arranged_marriage("lord-b", "chinois") == "chinois"


async def test_arranged_marriage_consumes_one_token() -> None:
    """D60 (2026-10-11): the Paroisse consumes one bought token."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.patch_kingdom_of("king-a", arranged_marriage_stock=1)
    result = await bundle.diplomacy.arranged_marriage("lord-a", "celtes")
    assert result == "celtes"
    kingdom = await bundle.kingdom_by_id_of("king-a")
    assert kingdom.arranged_marriage_stock == 0  # the token was consumed
    # The marriage points are never touched on the arranged path (D74).
    assert kingdom.marriage_points == 1


async def test_marry_refuses_a_civilization_claimed_elsewhere() -> None:
    """D74: an active marriage claims its civilization for everyone."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.give("Aquitaine", "celtes")
    await bundle.give("Bourgogne", "celtes")
    await bundle.diplomacy.marry("lord-a", "celtes")
    await bundle.patch_kingdom_of("king-b", arranged_marriage_stock=1)
    with pytest.raises(MarriageExclusivityError):
        await bundle.diplomacy.marry("lord-b", "celtes")
    with pytest.raises(MarriageExclusivityError):
        await bundle.diplomacy.arranged_marriage("lord-b", "celtes")


async def test_arranged_marriage_bypasses_the_stock_and_locks_six_hours() -> None:
    """D60/D74: no capacity consumed, instant exclusivity, 6h combat lock."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    kingdom.arranged_marriage_stock = 1  # one bought token (Marché, D60)
    kingdom.marriage_points = 0  # the points never bind the arranged path
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert await bundle.diplomacy.arranged_marriage("lord-a", "celtes", now=now) == "celtes"
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-a")
    assert lord.marriage_locked_until == int(
        (now + timedelta(hours=6)).timestamp()
    )
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert "celtes" in kingdom.secured_civilizations


async def test_standard_marriage_locks_twenty_four_hours() -> None:
    """D59: a standard marriage locks the lord for 24 real hours."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.give("Aquitaine", "celtes")
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    await bundle.diplomacy.marry("lord-a", "celtes", now=now)
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-a")
    assert lord.marriage_locked_until == int(
        (now + timedelta(hours=24)).timestamp()
    )


async def test_arranged_marriage_never_spends_a_token_on_refusal() -> None:
    """D60: every check runs before the token - a refusal never debits."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.patch_kingdom_of("king-a", arranged_marriage_stock=1)
    await bundle.diplomacy.arranged_marriage("lord-a", "celtes")
    kingdom = await bundle.kingdom_by_id_of("king-a")
    assert kingdom.arranged_marriage_stock == 0
    # A second weds refused: one marriage per lord (D45), no token spent.
    with pytest.raises(AlreadyMarriedError):
        await bundle.diplomacy.arranged_marriage("lord-a", "shu")
    assert (
        await bundle.kingdom_by_id_of("king-a")
    ).arranged_marriage_stock == 0


async def test_arranged_marriage_refuses_without_a_token() -> None:
    """D60: no token in stock - the Paroisse refuses the marriage."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(NoArrangedTokenError):
        await bundle.diplomacy.arranged_marriage("lord-a", "celtes")
    # Nothing was locked nor secured on the refusal.
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.married_civilization is None


async def test_marriage_lock_blocks_attack_and_defense() -> None:
    """D59/D60: a freshly wed lord can neither attack nor defend."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.patch_kingdom_of("king-a", arranged_marriage_stock=2)
    # king-a weds a QUEEN here: a royal arranged marriage consumes the
    # TWO tokens stocked above (Drasah's Reine rule) and locks 12h.
    await bundle.diplomacy.arranged_marriage("king-a", "celtes")
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    aquitaine = await bundle.kingdom_id("Aquitaine")
    territories = await bundle.territories.territories()
    target = next(t for t in territories if t.owner_kingdom_id == bourgogne)
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    with pytest.raises(LordLockedError):
        await bundle.attacks.declare_attack("king-a", target.map_key, "aoe2de://x", now=now)
    # The defense side is locked too: an Aquitaine lord weds, then
    # cannot answer the attack on his kingdom.
    home = next(t.map_key for t in territories if t.owner_kingdom_id == aquitaine)
    attack = await bundle.attacks.declare_attack("king-b", home, "aoe2de://y", now=now)
    await bundle.patch_kingdom_of("lord-a", arranged_marriage_stock=1)
    await bundle.diplomacy.arranged_marriage("lord-a", "shu")
    with pytest.raises(LordLockedError):
        await bundle.attacks.respond_defense(attack.id, "lord-a")


async def test_defeat_drops_the_marriage_at_the_next_recalculation() -> None:
    """D34: a combat defeat flags the kingdom; the marriage drops next pass."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.give("Aquitaine", "celtes")
    await bundle.diplomacy.marry("lord-a", "celtes")
    await bundle.diplomacy.record_defeat(aquitaine)
    reports = await bundle.diplomacy.recalculate()
    assert "celtes" not in reports["Aquitaine"]
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-a")
    assert lord.married_civilization is None
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.pending_marriage_losses == []


async def test_starting_draft_draws_random_civs_without_duplicates() -> None:
    """The starting draft happens at the season START (Drasah's phase
    rule): nothing is drawn during setup, then every kingdom draws at
    random and no civilization is ever shared between two kingdoms."""
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
    # setup phase: the draft is secret — NOTHING is drawn at creation
    for kingdom in kingdoms:
        assert kingdom.civilizations == []
    await bundle.kingdoms.start_season()  # the admin starts the game
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    drawn: list[str] = []
    for kingdom in kingdoms:
        assert len(kingdom.civilizations) == 2
        drawn.extend(kingdom.civilizations)
    assert len(set(drawn)) == len(drawn)  # no duplicates between kingdoms
    gaia = next(k for k in await bundle.kingdoms.kingdoms() if k.is_gaia)
    assert gaia.civilizations == []  # Gaïa never draws


async def test_imposed_launch_drafts_the_kingdoms_civs() -> None:
    """The imposed mode reveals its draft at the season start too: 8
    civs each, all distinct — never at the launch itself (setup)."""
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
    assert all(not k.civilizations for k in kingdoms)  # setup: nothing revealed
    await bundle.kingdoms.start_season()
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    drawn = [civ for kingdom in kingdoms for civ in kingdom.civilizations]
    assert len(drawn) == 16
    assert len(set(drawn)) == 16  # no duplicates between kingdoms


async def test_lords_day_drops_the_un_wed_draft() -> None:
    """Drasah 2026-10-11: the Lord's Day recalculation drops every
    drafted civilization EXCEPT the ones bound to an active marriage."""
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
    await bundle.kingdoms.start_season()  # the draft only exists from here
    before = {
        k.name: list(k.civilizations)
        for k in await bundle.kingdoms.kingdoms()
        if not k.is_gaia
    }
    assert all(civs for civs in before.values())
    # Aquitaine weds its first drafted civilization: the marriage
    # secures it through the Lord's Day; the rest of the hand drops.
    wed = before["Aquitaine"][0]
    await bundle.give("Aquitaine", wed)  # ensure the wed civ is in hand
    await bundle.diplomacy.marry("lord-a", wed)
    reports = await bundle.diplomacy.recalculate()
    assert reports["Aquitaine"] == [wed]  # only the marriage survives
    for name, _civs in before.items():
        if name == "Aquitaine":
            continue
        assert reports[name] == []  # no marriage: the whole hand drops
