"""Kingdoms Lieu Sacrée / Cathédrale - unit tests (kingdoms-services#244, lieu-sacree-v1).

The King's consecration behind an in-memory store: the 3 ⚙️ price and
its guards (King only, Cathédrale palier, own territory, single Lieu
Sacrée), the 72h chantier technology immunity (Embuscade, Corruption
refused; CLASSIC attacks allowed with unlimited attempts), the verdict
(survival → SACRED forever, immune to everything), and the destruction
path (attacker win → only the chantier is lost: territory kept by the
defender, 2 ⚙️ to the attacker, auto-rebuild on a bonus-free territory
or cathedral lost).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kingdoms.mods.kingdoms.attacks import (
    AttackService,
    EmbuscadeError,
    TerritoryNotAttackableError,
)
from kingdoms.mods.kingdoms.cathedrale import (
    CathedralActiveError,
    CathedralInsufficientTechError,
    CathedralKingOnlyError,
    CathedralNotFoundError,
    CathedralService,
    CathedralTierError,
)
from kingdoms.mods.kingdoms.economy import EconomyService, TerritoryProtectedError
from kingdoms.mods.kingdoms.events import EventService
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService

from .test_kingdoms_attacks import Bundle as _AttackBundle
from .test_kingdoms_events import MemoryStore as _EventsStore

# resolve() reads the REAL clock (resolved_at): anchor NOW to it so the
# 72h chantier window always contains "now".
NOW = datetime.now(tz=UTC).replace(microsecond=0)


class MemoryStore(_EventsStore):
    """The events store (no extra collection — the chantier rides the territory)."""


class Bundle(_AttackBundle):
    """The attack bundle extended with the cathedral service."""

    def __init__(self) -> None:
        super().__init__()
        # Rebuild over the events store (technologies + showmatches).
        self.store = MemoryStore()  # type: ignore[assignment]
        self.kingdoms = KingdomsService(self.store, self.config)  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]
        self.attacks = AttackService(self.store, self.config, self.kingdoms, self.territories)  # type: ignore[arg-type]
        self.economy = EconomyService(self.store, self.config, self.kingdoms, self.territories, self.attacks)  # type: ignore[arg-type]
        self.events = EventService(self.store, self.config, self.kingdoms, self.territories, self.attacks)  # type: ignore[arg-type]
        self.cathedrals = CathedralService(self.store, self.config, self.kingdoms, self.territories)  # type: ignore[arg-type]

    async def territory(self, map_key: str) -> Any:
        return next(
            t for t in await self.territories.territories() if t.map_key == map_key
        )

    async def kingdom_by_name(self, name: str) -> Any:
        return next(k for k in await self.kingdoms.kingdoms() if k.name == name)

    async def grant(self, kingdom: Any, points: int) -> None:
        kingdom.tech_points_bank += points
        await self.store.upsert_kingdom(kingdom.to_mongo())

    async def arm(self, attack: Any) -> None:
        """Extend the defense deadline: respond_defense reads the REAL clock."""
        attack_doc = attack.to_mongo()
        attack_doc["expires_at"] = datetime.now(tz=UTC) + timedelta(days=1)
        await self.store.upsert_attack(attack_doc)


async def _cathedral_season() -> tuple[Bundle, Any, Any, str]:
    """Launch a season, both kingdoms at the Cathédrale palier."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.kingdoms.start_season()
    aquitaine = await bundle.kingdom_by_name("Aquitaine")
    bourgogne = await bundle.kingdom_by_name("Bourgogne")
    for kingdom in (aquitaine, bourgogne):
        kingdom.parish_tier = 3
        await bundle.grant(kingdom, 10)
    return bundle, aquitaine, bourgogne, (await bundle.owner_maps(aquitaine.id))[0]


async def test_consecration_guards_and_payment() -> None:
    """King only, Cathédrale palier, own territory, single Lieu, 3 ⚙️."""
    bundle, aquitaine, _, first_map = await _cathedral_season()
    # A lord cannot consecrate.
    with pytest.raises(CathedralKingOnlyError):
        await bundle.cathedrals.consecrate("lord-a", first_map, now=NOW)
    # A foreign territory cannot be consecrated.
    foreign = (await bundle.owner_maps((await bundle.kingdom_by_name("Bourgogne")).id))[0]
    with pytest.raises(CathedralNotFoundError):
        await bundle.cathedrals.consecrate("king-a", foreign, now=NOW)
    # The Chapelle palier refuses.
    aquitaine.parish_tier = 1
    await bundle.store.upsert_kingdom(aquitaine.to_mongo())
    with pytest.raises(CathedralTierError):
        await bundle.cathedrals.consecrate("king-a", first_map, now=NOW)
    aquitaine.parish_tier = 3
    await bundle.store.upsert_kingdom(aquitaine.to_mongo())
    # An empty treasury refuses.
    aquitaine.tech_points_bank = 0
    await bundle.store.upsert_kingdom(aquitaine.to_mongo())
    with pytest.raises(CathedralInsufficientTechError):
        await bundle.cathedrals.consecrate("king-a", first_map, now=NOW)
    await bundle.grant(aquitaine, 10)
    territory = await bundle.cathedrals.consecrate("king-a", first_map, now=NOW)
    assert territory.cathedral_until == NOW + timedelta(hours=72)
    assert not territory.sacred
    kingdom = await bundle.kingdom_by_name("Aquitaine")
    assert kingdom.tech_points_bank == 10 - bundle.config.parish.cathedral_cost
    # A second Lieu Sacrée on the same territory refuses.
    with pytest.raises(CathedralActiveError):
        await bundle.cathedrals.consecrate("king-a", first_map, now=NOW + timedelta(hours=1))


async def test_chantier_resists_technology_but_not_classic_attacks() -> None:
    """Embuscade/Corruption refused on the chantier; classic attacks flow."""
    bundle, aquitaine, bourgogne, first_map = await _cathedral_season()
    await bundle.cathedrals.consecrate("king-a", first_map, now=NOW)
    after = NOW + timedelta(hours=1)
    # The Corruption (technology) resists the chantier territory.
    state = await bundle.attacks.technology_state(aquitaine.id)
    state.purchases["corruption"] = 1
    await bundle.store.upsert_technology(state.to_mongo())
    with pytest.raises(TerritoryProtectedError):
        await bundle.economy.buy_corruption(bourgogne.id, (await bundle.territory(first_map)).id, now=after)
    # The Embuscade (technology) refuses on the chantier territory too.
    state_b = await bundle.attacks.technology_state(bourgogne.id)
    state_b.purchases["embuscade"] = 1
    await bundle.store.upsert_technology(state_b.to_mongo())
    with pytest.raises(EmbuscadeError):
        await bundle.attacks.declare_embuscade("king-b", first_map, "aoe2de://x", now=after)
    # The CLASSIC attack, though, engages normally (unlimited attempts).
    attack = await bundle.attacks.declare_attack("king-b", first_map, "aoe2de://x", now=after)
    assert attack.map_key == first_map


async def test_defense_win_keeps_the_chantier_alive() -> None:
    """A successful defense during the chantier changes nothing."""
    bundle, _, _, first_map = await _cathedral_season()
    await bundle.cathedrals.consecrate("king-a", first_map, now=NOW)
    attack = await bundle.attacks.declare_attack(
        "king-b", first_map, "aoe2de://x", now=NOW + timedelta(hours=1)
    )
    await bundle.arm(attack)
    defended = await bundle.attacks.respond_defense(attack.id, "lord-a")
    assert defended.state is not None
    # The defender wins: the territory AND the chantier stay.
    resolved = await bundle.attacks.resolve(attack.id, (await bundle.kingdom_by_name("Aquitaine")).id)
    assert resolved.winner_kingdom_id != attack.attacker_kingdom_id
    territory = await bundle.territory(first_map)
    assert territory.owner_kingdom_id == (await bundle.kingdom_by_name("Aquitaine")).id
    assert territory.is_chantier_at(NOW + timedelta(hours=2))
    # A second attempt (unlimited) is accepted.
    again = await bundle.attacks.declare_attack(
        "lord-b", first_map, "aoe2de://x", now=NOW + timedelta(hours=2)
    )
    assert again.map_key == first_map


async def test_attacker_win_destroys_only_the_chantier() -> None:
    """Attacker win: no conquest, +2 ⚙️, auto-rebuild on a bonus-free territory."""
    bundle, aquitaine, bourgogne, first_map = await _cathedral_season()
    await bundle.cathedrals.consecrate("king-a", first_map, now=NOW)
    bank_before = bourgogne.tech_points_bank
    attack = await bundle.attacks.declare_attack(
        "king-b", first_map, "aoe2de://x", now=NOW + timedelta(hours=1)
    )
    await bundle.arm(attack)
    await bundle.attacks.respond_defense(attack.id, "lord-a")
    resolved = await bundle.attacks.resolve(attack.id, bourgogne.id)
    assert resolved.winner_kingdom_id == bourgogne.id
    territory = await bundle.territory(first_map)
    # Drasah's correction: the territory STAYS with the defender.
    assert territory.owner_kingdom_id == aquitaine.id
    assert territory.cathedral_until is None
    assert not territory.sacred
    # The attacker banks the destroy reward.
    kingdom_b = await bundle.kingdom_by_name("Bourgogne")
    assert kingdom_b.tech_points_bank == bank_before + bundle.config.parish.cathedral_destroy_reward
    # The cathedral auto-rebuilt on another bonus-free territory.
    chantiers = [
        t
        for t in await bundle.territories.territories()
        if t.owner_kingdom_id == aquitaine.id and t.cathedral_until is not None
    ]
    assert len(chantiers) == 1
    assert chantiers[0].map_key != first_map
    # Replaying the resolution is idempotent (no double reward).
    replay = await bundle.attacks.resolve(attack.id, bourgogne.id)
    assert replay.state is not None
    kingdom_b2 = await bundle.kingdom_by_name("Bourgogne")
    assert kingdom_b2.tech_points_bank == kingdom_b.tech_points_bank


async def test_chantier_survival_makes_the_territory_sacred_forever() -> None:
    """Past the 72h without a victorious attack: SACRED, immune to all."""
    bundle, _, bourgogne, first_map = await _cathedral_season()
    await bundle.cathedrals.consecrate("king-a", first_map, now=NOW)
    late = NOW + timedelta(hours=73)
    sacred = await bundle.cathedrals.resolve_due(now=late)
    assert [t.map_key for t in sacred] == [first_map]
    territory = await bundle.territory(first_map)
    assert territory.sacred and territory.cathedral_until is None
    # Replaying the tick changes nothing.
    assert await bundle.cathedrals.resolve_due(now=late + timedelta(hours=1)) == []
    # A SACRED territory refuses every attack and every technology.
    with pytest.raises(TerritoryNotAttackableError):
        await bundle.attacks.declare_attack("king-b", first_map, "aoe2de://x", now=late)
    state_b = await bundle.attacks.technology_state(bourgogne.id)
    state_b.purchases["embuscade"] = 1
    await bundle.store.upsert_technology(state_b.to_mongo())
    with pytest.raises(TerritoryNotAttackableError):
        await bundle.attacks.declare_embuscade("king-b", first_map, "aoe2de://x", now=late)
    state_b2 = await bundle.attacks.technology_state(bourgogne.id)
    state_b2.purchases["corruption"] = 1
    await bundle.store.upsert_technology(state_b2.to_mongo())
    with pytest.raises(TerritoryProtectedError):
        await bundle.economy.buy_corruption(
            bourgogne.id, (await bundle.territory(first_map)).id, now=late
        )


async def test_destroyed_cathedral_without_free_territory_is_lost() -> None:
    """No bonus-free territory to rebuild on → the cathedral is LOST."""
    bundle, aquitaine, bourgogne, _ = await _cathedral_season()
    # Aquitaine keeps exactly ONE territory: the consecrated one.
    other_maps = (await bundle.owner_maps(aquitaine.id))[1:]
    for map_key in other_maps:
        territory = await bundle.territory(map_key)
        territory.owner_kingdom_id = bourgogne.id
        await bundle.store.upsert_territory(territory.to_mongo())
    only_map = (await bundle.owner_maps(aquitaine.id))[0]
    await bundle.cathedrals.consecrate("king-a", only_map, now=NOW)
    attack = await bundle.attacks.declare_attack(
        "king-b", only_map, "aoe2de://x", now=NOW + timedelta(hours=1)
    )
    await bundle.arm(attack)
    await bundle.attacks.respond_defense(attack.id, "king-a")
    await bundle.attacks.resolve(attack.id, bourgogne.id)
    chantiers = [
        t
        for t in await bundle.territories.territories()
        if t.cathedral_until is not None
    ]
    assert chantiers == []
    territory = await bundle.territory(only_map)
    assert territory.owner_kingdom_id == aquitaine.id
    assert territory.cathedral_until is None and not territory.sacred
