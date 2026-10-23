"""Kingdoms mod planification-attaque-v1 — unit tests (Drasah 23/10/2026).

The planning cycle behind an in-memory store: ``[plannification] →
[déclaration] → [T attaque] → [résolution]``. The spec's golden rules:
a plan reserves NOTHING (a third-party declaration on the active
choice ruptures it — auto-switch to the next choice, or break with a
player-facing event), the declaration fires at T - itinéraire through
``plan_events`` (the weekly budget is consumed THERE), and an engaged
lord (attacker or defender) is locked until his battle resolves: no
new declaration, no planning, no defense, no Embuscade. The Embuscade
is the EXCEPTIONAL attack: it consumes a bought ``embuscade`` instead
of the weekly budget and generates an exceptional defense (no weekly
defense point spent or needed).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import (
    AttackService,
    EmbuscadeError,
    LordEngagedError,
    NoBudgetError,
    PlanningError,
    PlanNotFoundError,
    TerritoryBusyError,
)
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import AttackState, LordRole, PlanState, TechnologyState
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService

from .test_kingdoms_attacks import Bundle as _AttackBundle
from .test_kingdoms_service import MemoryStore as _BaseStore

_SAFE_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
"""A Sunday 14:00 Paris moment — OUTSIDE the Lord's Day window."""

_TRAVEL = timedelta(minutes=360)
"""The default player itinéraire (6h) — the declaration moment is T - travel."""


@pytest.fixture(autouse=True)
def _freeze_planning_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _SAFE_NOW)


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


class Bundle(_AttackBundle):
    """Everything one planning test needs, rebuilt per test."""

    def __init__(self, config=None) -> None:
        self.store = MemoryStore()
        self.config = config or default_season_config()
        self.kingdoms = KingdomsService(self.store, self.config)  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]
        self.attacks = AttackService(self.store, self.config, self.kingdoms, self.territories)  # type: ignore[arg-type]


async def _prepared_bundle(seed: int = 5) -> tuple[Bundle, str, list[str]]:
    """A launched season with both kingdoms owning several territories."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(seed)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    return bundle, bourgogne, await bundle.owner_maps(bourgogne)


async def test_plan_attack_validates_the_wizard_rules() -> None:
    """The golden 24h horizon, the itinéraire floor and the choices."""
    bundle, bourgogne, maps = await _prepared_bundle()
    own = (await bundle.owner_maps(await bundle.kingdom_id("Aquitaine")))[0]
    # No choice / too many choices / duplicates.
    with pytest.raises(PlanningError):
        await bundle.attacks.plan_attack("king-a", [], _SAFE_NOW + _TRAVEL * 2)
    with pytest.raises(PlanningError):
        await bundle.attacks.plan_attack("king-a", maps[:4], _SAFE_NOW + _TRAVEL * 2)
    with pytest.raises(PlanningError):
        await bundle.attacks.plan_attack("king-a", [maps[0], maps[0]], _SAFE_NOW + _TRAVEL * 2)
    # T closer than one itinéraire → refused.
    with pytest.raises(PlanningError):
        await bundle.attacks.plan_attack("king-a", [maps[0]], _SAFE_NOW + _TRAVEL / 2)
    # T further than the 24h horizon → refused.
    with pytest.raises(PlanningError):
        await bundle.attacks.plan_attack("king-a", [maps[0]], _SAFE_NOW + timedelta(hours=25))
    # Own territory / unknown map → refused.
    with pytest.raises(PlanningError):
        await bundle.attacks.plan_attack("king-a", [own], _SAFE_NOW + _TRAVEL * 2)
    with pytest.raises(PlanningError):
        await bundle.attacks.plan_attack("king-a", ["no-such-map"], _SAFE_NOW + _TRAVEL * 2)
    # The nominal plan lands inside the window.
    plan = await bundle.attacks.plan_attack("king-a", [maps[0]], _SAFE_NOW + _TRAVEL * 2)
    assert plan.state is PlanState.PLANNED
    assert plan.choices == [maps[0]]
    assert plan.meeting_at == _SAFE_NOW + _TRAVEL * 2


async def test_plan_executes_at_t_minus_travel_and_consumes_the_budget_there() -> None:
    """The declaration fires at T - itinéraire; the budget pays THERE."""
    bundle, bourgogne, maps = await _prepared_bundle()
    meeting = _SAFE_NOW + _TRAVEL * 2
    plan = await bundle.attacks.plan_attack("king-a", [maps[0]], meeting)
    # Nothing consumed at the planning: the plan reserves nothing.
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "king-a")
    assert lord.attack_used == 0
    assert (await bundle.attacks.attacks()) == []
    # Before T - travel: the tick leaves the plan alone.
    assert await bundle.attacks.plan_events(now=_SAFE_NOW + _TRAVEL) == []
    # At T - travel the plan executes through the ordinary declare.
    changed = await bundle.attacks.plan_events(now=meeting - _TRAVEL)
    assert [p.id for p in changed] == [plan.id]
    assert changed[0].state is PlanState.EXECUTED
    assert changed[0].executed_attack_id is not None
    attack = next(
        a for a in await bundle.attacks.attacks() if str(a.id) == str(changed[0].executed_attack_id)
    )
    assert attack.map_key == maps[0]
    assert attack.expires_at == meeting
    assert attack.state is AttackState.DECLARED
    # The budget was consumed AT the declaration, never before.
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "king-a")
    assert lord.attack_used == 1


async def test_busy_territory_refuses_the_active_choice() -> None:
    """D42: a declared territory admits neither a plan nor a declaration."""
    bundle, bourgogne, maps = await _prepared_bundle()
    await bundle.attacks.declare_attack("king-a", maps[0], "")
    # The territory is occupied: a NEW plan on it is refused.
    with pytest.raises(TerritoryBusyError):
        await bundle.attacks.plan_attack("king-a", [maps[0]], _SAFE_NOW + _TRAVEL * 2)
    # And a second declaration on the SAME territory too.
    with pytest.raises(TerritoryBusyError):
        await bundle.attacks.declare_attack("lord-a", maps[0], "")


async def test_adverse_declaration_ruptures_the_plan_and_switches() -> None:
    """Declaration primes: the active choice ruptures → choice 2."""
    bundle, bourgogne, maps = await _prepared_bundle()
    meeting = _SAFE_NOW + _TRAVEL * 2
    plan = await bundle.attacks.plan_attack("king-a", maps[:3], meeting)
    assert plan.active_index == 0
    # A third-party declaration lands on the ACTIVE choice.
    await bundle.attacks.declare_attack("lord-a", maps[0], "")
    plans = await bundle.attacks.plans()
    assert len(plans) == 1
    after = plans[0]
    assert after.id == plan.id
    assert after.state is PlanState.PLANNED
    assert after.active_index == 1
    assert after.active_choice == maps[1]
    assert after.last_event == "switched"
    # The rupture event surfaces through the plan_events tick.
    changed = await bundle.attacks.plan_events(now=_SAFE_NOW)
    assert [p.id for p in changed] == [plan.id]


async def test_declaration_breaks_a_plan_without_remaining_choices() -> None:
    """No fallback choice left → the plan breaks, the DM event carries."""
    bundle, bourgogne, maps = await _prepared_bundle()
    meeting = _SAFE_NOW + _TRAVEL * 2
    plan = await bundle.attacks.plan_attack("king-a", [maps[0]], meeting)
    await bundle.attacks.declare_attack("lord-a", maps[0], "")
    after = (await bundle.attacks.plans())[0]
    assert after.id == plan.id
    assert after.state is PlanState.BROKEN
    assert after.last_event == "broken_by_declaration"


async def test_cancel_plan_is_free_and_final() -> None:
    """The planificateur withdraws his plan — nothing was consumed."""
    bundle, bourgogne, maps = await _prepared_bundle()
    plan = await bundle.attacks.plan_attack("king-a", [maps[0]], _SAFE_NOW + _TRAVEL * 2)
    # Only the planificateur may cancel.
    with pytest.raises(PlanNotFoundError):
        await bundle.attacks.cancel_plan(plan.id, "king-b")
    cancelled = await bundle.attacks.cancel_plan(plan.id, "king-a")
    assert cancelled.state is PlanState.CANCELLED
    # A cancelled plan never executes.
    changed = await bundle.attacks.plan_events(now=_SAFE_NOW + timedelta(hours=24))
    assert changed == []
    assert (await bundle.attacks.attacks()) == []


async def test_engaged_lord_is_locked_until_resolution() -> None:
    """The engaged lock: no declare, no plan, no defend, no ambush."""
    bundle, bourgogne, maps = await _prepared_bundle()
    aquitaine_maps = await bundle.owner_maps(await bundle.kingdom_id("Aquitaine"))
    attack = await bundle.attacks.declare_attack("king-a", maps[0], "")
    # The ATTACKER is engaged: declare, plan, embuscade all refuse.
    with pytest.raises(LordEngagedError):
        await bundle.attacks.declare_attack("king-a", maps[1], "")
    with pytest.raises(LordEngagedError):
        await bundle.attacks.plan_attack("king-a", [maps[1]], _SAFE_NOW + _TRAVEL * 2)
    with pytest.raises(LordEngagedError):
        await bundle.attacks.declare_embuscade("king-a", maps[1], "")
    # A DEFENDER answers — and becomes engaged too.
    defended = await bundle.attacks.respond_defense(attack.id, "lord-b")
    assert defended.state is AttackState.DEFENDED
    # The engaged defender defends nothing else: a second attack on
    # the defender kingdom exists, he cannot answer it.
    second = await bundle.attacks.declare_attack("king-b", aquitaine_maps[0], "")
    with pytest.raises(LordEngagedError):
        await bundle.attacks.respond_defense(second.id, "lord-b")
    with pytest.raises(LordEngagedError):
        await bundle.attacks.declare_attack("lord-b", aquitaine_maps[1], "")
    # Per LORD: the OTHER lords of the locked kingdoms stay free.
    third = await bundle.attacks.plan_attack("lord-a", [maps[2]], _SAFE_NOW + _TRAVEL * 2)
    assert third.state is PlanState.PLANNED
    fourth = await bundle.attacks.respond_defense(second.id, "lord-a")
    assert fourth.defender_lord_id == "lord-a"
    # The result announced: the attacker's lock lifts with it (the
    # weekly budget stays spent — but the PLAN is free again).
    await bundle.attacks.resolve(attack.id, await bundle.kingdom_id("Aquitaine"))
    fifth = await bundle.attacks.plan_attack("king-a", [maps[3]], _SAFE_NOW + _TRAVEL * 2)
    assert fifth.state is PlanState.PLANNED


async def test_embuscade_is_the_exceptional_attack_and_defense() -> None:
    """The Embuscade pays with a purchase, the defense stays free."""
    bundle, bourgogne, maps = await _prepared_bundle()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    state = TechnologyState(_id=aquitaine, season_id="s", tech_points=2)
    await bundle.store.upsert_technology(state.to_mongo())
    await bundle.attacks.buy_technology(aquitaine, "embuscade")
    bought = (await bundle.attacks.technology_state(aquitaine)).purchases["embuscade"]
    assert bought == 1
    # The Embuscade consumes the purchase, NOT the weekly budget.
    attack = await bundle.attacks.declare_embuscade("king-a", maps[0], "")
    assert attack.embuscade is True
    assert attack.expires_at == _SAFE_NOW + timedelta(minutes=60)
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "king-a")
    assert lord.attack_used == 0
    assert (await bundle.attacks.technology_state(aquitaine)).purchases["embuscade"] == 0
    # The defense is EXCEPTIONAL: no weekly defense point consumed.
    defended = await bundle.attacks.respond_defense(attack.id, "lord-b")
    assert defended.state is AttackState.DEFENDED
    defender = next(l for l in await bundle.kingdoms.lords() if l.id == "lord-b")
    assert defender.defense_used == 0
    # Without a purchase left, the Embuscade button leads nowhere.
    with pytest.raises(EmbuscadeError):
        await bundle.attacks.declare_embuscade("lord-a", maps[1], "")


async def test_engaged_plan_breaks_at_execution() -> None:
    """A plan whose lord got ENGAGED before T breaks — it never hangs."""
    bundle, bourgogne, maps = await _prepared_bundle()
    meeting = _SAFE_NOW + _TRAVEL * 2
    plan = await bundle.attacks.plan_attack("king-a", [maps[0]], meeting)
    # The lord declares another attack before T — he is now ENGAGED
    # and his plan cannot fire (the lock primes over everything).
    await bundle.attacks.declare_attack("king-a", maps[1], "")
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "king-a")
    assert lord.attack_used == 1
    changed = await bundle.attacks.plan_events(now=meeting)
    after = next(p for p in await bundle.attacks.plans() if p.id == plan.id)
    assert after.state is PlanState.BROKEN
    assert after.last_event == "broken_engaged"
    assert after.id in [p.id for p in changed]


async def test_no_budget_plan_breaks_on_the_budget_event() -> None:
    """A plan whose budget is gone at T breaks with the budget event."""
    bundle, bourgogne, maps = await _prepared_bundle()
    meeting = _SAFE_NOW + _TRAVEL * 2
    plan = await bundle.attacks.plan_attack("lord-a", [maps[0]], meeting)
    # The budget is per LORD: force lord-a's weekly attacks to spent
    # directly in the store (a plan consumes nothing up front).
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "lord-a")
    lord.attack_used = int(bundle.config.attacks.attacks_per_week)
    await bundle.store.upsert_lord(lord.to_mongo())
    changed = await bundle.attacks.plan_events(now=meeting)
    after = next(p for p in await bundle.attacks.plans() if p.id == plan.id)
    assert after.state is PlanState.BROKEN
    assert after.last_event == "broken_budget"
    assert after.id in [p.id for p in changed]
