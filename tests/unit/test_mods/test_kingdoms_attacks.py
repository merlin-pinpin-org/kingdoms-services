"""Kingdoms mod attacks & defenses - unit tests (kingdoms-services#158, T4).

Reference §11-§13 behind an in-memory store: the attack state
machine (declaration, defense, expiry, resolution with the idempotent
territory transfer), the weekly budgets recharged at the cycle switch
(D1), the Gaïa free-for-all (D6/D42, §13.2), and the six combat
technologies (D9-D12/D18/D28/D36/D43).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from typing import Any

import pytest

from kingdoms.mods.kingdoms.attacks import (
    AttackNotFoundError,
    AttackService,
    GaiaAttackFullError,
    InsufficientTechPointsError,
    NoBudgetError,
    SabotageError,
    TechnologyLimitReachedError,
    TerritoryBusyError,
    TerritoryNotAttackableError,
)
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    AttackKind,
    AttackState,
    LordRole,
    TechnologyState,
)
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService

from .test_kingdoms_service import MemoryStore as _BaseStore


class _FakeCoreSeasons:
    """In-memory core season registry (the SeasonService seam)."""

    def __init__(self) -> None:
        self.seasons: list[Any] = []

    async def list_seasons(self, scope: str) -> list[Any]:
        del scope
        return list(self.seasons)

    async def create_season(self, scope: str, name: str, pool: Any, start: int) -> Any:
        from types import SimpleNamespace

        index = max((s.index for s in self.seasons), default=0) + 1
        season = SimpleNamespace(id=f"{scope}-{index}", index=index, name=name)
        self.seasons.append(season)
        return season

class MemoryStore(_BaseStore):
    """The enrollment store extended with the T3/T4 collections."""

    def __init__(self) -> None:
        super().__init__()
        self.territories: dict[str, dict] = {}
        self.attacks: dict[str, dict] = {}
        self.technologies: dict[str, dict] = {}

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


class Bundle:
    """Everything one attack test needs, rebuilt per test."""

    def __init__(self, config=None) -> None:
        self.store = MemoryStore()
        self.config = config or default_season_config()
        self.kingdoms = KingdomsService(self.store, self.config, core_seasons=_FakeCoreSeasons(), guild_id="123")  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]
        self.attacks = AttackService(self.store, self.config, self.kingdoms, self.territories)  # type: ignore[arg-type]

    async def launch_season(self) -> tuple[str, str, str]:
        """Launch a free-mode season with two kingdoms and four lords."""
        await self.kingdoms.launch()
        await self.kingdoms.enroll("king-a", "King A", LordRole.KING, proposed_name="Aquitaine")
        await self.kingdoms.enroll("lord-a", "Lord A", LordRole.LORD, kingdom_name="Aquitaine")
        await self.kingdoms.enroll("king-b", "King B", LordRole.KING, proposed_name="Bourgogne")
        await self.kingdoms.enroll("lord-b", "Lord B", LordRole.LORD, kingdom_name="Bourgogne")
        gaia = next(k.id for k in await self.kingdoms.kingdoms() if k.name == GAIA_KINGDOM_KEY)
        return "Aquitaine", "Bourgogne", gaia

    async def kingdom_id(self, name: str) -> str:
        """Resolve a kingdom id by display name."""
        return next(k.id for k in await self.kingdoms.kingdoms() if k.name == name)

    async def owner_maps(self, kingdom_id: str) -> list[str]:
        """All map keys owned by a kingdom (requires a prior draw)."""
        return [
            t.map_key
            for t in await self.territories.territories()
            if t.owner_kingdom_id == kingdom_id
        ]

    async def draw(self, seed: int) -> None:
        """Draw the initial territories deterministically."""
        await self.territories.draw_initial(seed=seed)


async def test_declare_defend_resolve_captures_the_territory() -> None:
    """Nominal 1v1: declare, defend, resolve for the attacker, transfer."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://1.2.3.4/lobby")
    assert attack.state is AttackState.DECLARED
    assert attack.defender_kingdom_id == bourgogne
    # Any lord of the defending kingdom defends (D7).
    defended = await bundle.attacks.respond_defense(attack.id, "lord-b")
    assert defended.state is AttackState.DEFENDED
    assert defended.defender_lord_id == "lord-b"
    resolved = await bundle.attacks.resolve(attack.id, aquitaine)
    assert resolved.state is AttackState.RESOLVED
    # Capture: the territory moved; resolving twice is idempotent.
    again = await bundle.attacks.resolve(attack.id, aquitaine)
    assert again.state is AttackState.RESOLVED
    assert target in await bundle.owner_maps(aquitaine)


async def test_defense_keeps_the_territory_on_defender_win() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(2)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby")
    await bundle.attacks.respond_defense(attack.id, "king-b")
    resolved = await bundle.attacks.resolve(attack.id, bourgogne)
    assert resolved.winner_kingdom_id == bourgogne
    assert target in await bundle.owner_maps(bourgogne)


async def test_declare_rejects_own_and_gaia_territories() -> None:
    bundle = Bundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(3)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    own = (await bundle.owner_maps(aquitaine))[0]
    with pytest.raises(TerritoryNotAttackableError):
        await bundle.attacks.declare_attack("king-a", own, "aoe2de://x")
    gaia_map = (await bundle.owner_maps(gaia))[0]
    with pytest.raises(TerritoryNotAttackableError):
        await bundle.attacks.declare_attack("king-a", gaia_map, "aoe2de://x")


async def test_double_target_is_refused_d42() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(4)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    with pytest.raises(TerritoryBusyError):
        await bundle.attacks.declare_attack("lord-a", target, "aoe2de://y")


async def test_weekly_budgets_and_recharge_d1() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(5)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    maps_b = await bundle.owner_maps(bourgogne)
    await bundle.attacks.declare_attack("king-a", maps_b[0], "aoe2de://x")
    with pytest.raises(NoBudgetError):
        await bundle.attacks.declare_attack("king-a", maps_b[1], "aoe2de://y")
    await bundle.attacks.recharge_weekly_budgets()
    await bundle.attacks.declare_attack("king-a", maps_b[1], "aoe2de://z")  # budget back


async def test_no_defense_auto_victory_captures_d8_default() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(6)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    later = attack.expires_at + timedelta(minutes=1)
    expired = await bundle.attacks.expire_stale(now=later)
    assert len(expired) == 1
    assert expired[0].state is AttackState.RESOLVED
    assert expired[0].winner_kingdom_id == aquitaine
    assert target in await bundle.owner_maps(aquitaine)


async def test_no_defense_vs_ai_waits_for_the_result_d8() -> None:
    config = default_season_config().model_copy(deep=True)
    assert config.attacks.no_defense_outcome == "auto_victory"
    config.attacks.no_defense_outcome = "vs_ai"
    bundle = Bundle(config)
    await bundle.launch_season()
    await bundle.draw(7)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    expired = await bundle.attacks.expire_stale(now=attack.expires_at + timedelta(minutes=1))
    assert expired[0].state is AttackState.EXPIRED
    # The result still comes through the game contract (D20).
    resolved = await bundle.attacks.resolve(attack.id, bourgogne)
    assert resolved.winner_kingdom_id == bourgogne
    assert target in await bundle.owner_maps(bourgogne)


async def test_attacker_absence_consume_and_admin_restitution_d17() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(8)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.attack_used == 1  # consumed
    restituted = await bundle.attacks.restitute(attack.id)
    assert restituted.restituted is True
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.attack_used == 0  # admin exception


async def test_gaia_free_for_all_one_per_kingdom_and_winner_d6_d42() -> None:
    bundle = Bundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(9)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    gaia_map = (await bundle.owner_maps(gaia))[0]
    first = await bundle.attacks.declare_gaia_attack("king-a", gaia_map, "aoe2de://ffa")
    assert first.participants == ["king-a"]
    # Same kingdom twice: refused (D42).
    with pytest.raises(GaiaAttackFullError):
        await bundle.attacks.declare_gaia_attack("lord-a", gaia_map, "aoe2de://ffa2")
    second = await bundle.attacks.declare_gaia_attack("king-b", gaia_map, "aoe2de://ffb")
    assert second.participants == ["king-a", "king-b"]
    resolved = await bundle.attacks.resolve(second.id, aquitaine)
    assert resolved.winner_kingdom_id == aquitaine
    assert gaia_map in await bundle.owner_maps(aquitaine)


async def test_gaia_free_for_all_capacity_d6() -> None:
    config = default_season_config().model_copy(deep=True)
    config.attacks.gaia_max_participants = 2
    bundle = Bundle(config)
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(10)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    await bundle.attacks.declare_gaia_attack("king-a", gaia_map, "aoe2de://1")
    await bundle.attacks.declare_gaia_attack("king-b", gaia_map, "aoe2de://2")
    with pytest.raises(GaiaAttackFullError):
        await bundle.attacks.declare_gaia_attack("lord-b", gaia_map, "aoe2de://3")


async def test_technology_purchase_costs_and_limits_d9_d36() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(11)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    state = TechnologyState(_id=aquitaine, season_id="s", tech_points=2)
    await bundle.store.upsert_technology(state.to_mongo())
    # Jeu d'armes costs 1, limit 1 per season (default reference).
    await bundle.attacks.buy_technology(aquitaine, "jeu_d_armes")
    with pytest.raises(TechnologyLimitReachedError):
        await bundle.attacks.buy_technology(aquitaine, "jeu_d_armes")
    # 1 point left: embuscade (2) is unaffordable.
    with pytest.raises(InsufficientTechPointsError):
        await bundle.attacks.buy_technology(aquitaine, "embuscade")


async def test_ai_level_floor_and_ceiling_d18() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(12)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    state = TechnologyState(
        _id=aquitaine,
        season_id="s",
        tech_points=10,
        purchases={"jeu_d_armes": 1},
    )
    await bundle.store.upsert_technology(state.to_mongo())
    # Base 5 + 1 permanent would exceed the ceiling of 5.
    assert await bundle.attacks.effective_gaia_ai_level(aquitaine, 5) == 5
    # An engaged Traquenard lowers it by one; it expires with the combat.
    await bundle.attacks.buy_technology(aquitaine, "traquenard")
    await bundle.attacks.engage_traquenard(aquitaine)
    assert await bundle.attacks.effective_gaia_ai_level(aquitaine, 5) == 4
    await bundle.attacks.expire_traquenard(aquitaine)
    assert await bundle.attacks.effective_gaia_ai_level(aquitaine, 5) == 5


async def test_sabotage_and_counter_espionage_d12_d28() -> None:
    bundle = Bundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(13)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    sabotaged = await bundle.attacks.sabotage_civilizations(attack.id, aquitaine, ["Britons", "Franks"])
    assert sabotaged.effects["sabotage"] == ["Britons", "Franks"]
    with pytest.raises(SabotageError):  # max 2 per game
        await bundle.attacks.sabotage_civilizations(attack.id, aquitaine, ["Celts"])
    countered = await bundle.attacks.counter_espionage(attack.id, bourgogne)
    assert "sabotage" not in countered.effects
    # Gaïa free-for-alls cannot be sabotaged (kingdom-vs-kingdom only).
    gaia_map = (await bundle.owner_maps(gaia))[0]
    gaia_attack = await bundle.attacks.declare_gaia_attack("lord-a", gaia_map, "aoe2de://g")
    with pytest.raises(SabotageError):
        await bundle.attacks.sabotage_civilizations(gaia_attack.id, aquitaine, ["Britons"])
    # Only the defending kingdom counters.
    with pytest.raises(SabotageError):
        await bundle.attacks.counter_espionage(attack.id, aquitaine)


async def test_resolve_rejects_outside_kingdoms() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(14)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    with pytest.raises(AttackNotFoundError):
        await bundle.attacks.resolve("no-such-attack", bourgogne)
    # An unknown winner kingdom is refused before the state check.
    await bundle.attacks.respond_defense(attack.id, "king-b")
    from kingdoms.mods.kingdoms.service import KingdomNotFoundError

    with pytest.raises(KingdomNotFoundError):
        await bundle.attacks.resolve(attack.id, "no-such-kingdom")


def test_attack_states_are_final_states() -> None:
    from kingdoms.mods.kingdoms.models import AttackModel

    attack = AttackModel(
        _id="a",
        season_id="s",
        kind=AttackKind.PLAYER,
        territory_id="t",
        map_key="arabia",
        defender_kingdom_id="k-2",
        attacker_lord_id="p1",
        attacker_kingdom_id="k-1",
        declared_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    assert attack.state is AttackState.DECLARED
    assert attack.is_over is False
    attack.state = AttackState.RESOLVED
    assert attack.is_over is True
