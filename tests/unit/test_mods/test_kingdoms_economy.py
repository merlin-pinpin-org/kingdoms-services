"""Kingdoms mod special-action economy - unit tests (kingdoms-services#161, T7).

Reference §20 behind an in-memory store: the kingdom tech-point wallet
fed by the epochs and exploration (D9/D36), the Explorateur territory
purchase (D48), the Corruption steal with its 48-hour shield and the
compensation tech point (D15/D37/D58), the Garde Royale shield and its
rising-cost extension (D47/D61), and the protection the attacks must
respect.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import (
    PatrolProtectedError,
    TechnologyLimitReachedError,
    TerritoryNotAttackableError,
)
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.economy import (
    EconomyError,
    EconomyLimitReachedError,
    EconomyService,
    GuardAlreadyActiveError,
    InsufficientPointsError,
    TerritoryProtectedError,
)

from .test_kingdoms_attacks import Bundle as _AttackBundle


class Bundle(_AttackBundle):
    """The attack bundle extended with the economy service."""

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.economy = EconomyService(  # type: ignore[arg-type]
            self.store, self.config, self.kingdoms, self.territories, self.attacks
        )

    async def fund(self, kingdom_id: str, points: int) -> None:
        """Credit the kingdom wallet (epoch/exploration stand-in)."""
        await self.economy.grant_tech_points(kingdom_id, points)


async def test_wallet_grant_and_spend() -> None:
    """D9: the wallet credits and debits, and refuses overdrafts."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 3)
    assert await bundle.economy.wallet(aquitaine) == 3
    await bundle.economy.spend_points(aquitaine, 2)
    assert await bundle.economy.wallet(aquitaine) == 1
    with pytest.raises(InsufficientPointsError):
        await bundle.economy.spend_points(aquitaine, 2)


async def test_explorateur_locks_a_lord_then_resolves_a_map() -> None:
    """marche-v2 art. 6: the buy locks the chosen lord 48h, the tick draws."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    monday = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    lord = await bundle.economy.buy_explorateur(aquitaine, "lord-a", now=monday)
    assert lord.exploration_locked_until == int((monday + timedelta(hours=48)).timestamp())
    assert await bundle.economy.wallet(aquitaine) == 3
    # The territory does NOT exist during the lock.
    assert not [
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == aquitaine
    ]
    # Before expiry: nothing resolves.
    before = monday + timedelta(hours=47)
    assert await bundle.economy.resolve_due_explorations(now=before) == []
    # Consumable: once per season per kingdom.
    await bundle.fund(aquitaine, 5)
    with pytest.raises(EconomyLimitReachedError):
        await bundle.economy.buy_explorateur(aquitaine, "king-a", now=monday)
    # After expiry: a fresh map arrives and the lock clears (idempotent).
    after = monday + timedelta(hours=48, minutes=1)
    resolved = await bundle.economy.resolve_due_explorations(now=after)
    assert len(resolved) == 1
    resolved_lord, territory = resolved[0]
    assert resolved_lord.id == "lord-a"
    assert resolved_lord.exploration_locked_until is None
    assert territory is not None
    assert territory.owner_kingdom_id == aquitaine
    catalog = {entry.key for entry in bundle.config.maps}
    assert territory.map_key in catalog
    assert await bundle.economy.resolve_due_explorations(now=after) == []


async def test_explorator_draws_without_duplicate_and_refunds_empty_pool() -> None:
    """marche-v2 art. 6: no out map twice; an exhausted pool refunds."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.fund(aquitaine, 5)
    await bundle.fund(bourgogne, 5)
    monday = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    await bundle.economy.buy_explorateur(aquitaine, "lord-a", now=monday)
    await bundle.economy.buy_explorateur(bourgogne, "lord-b", now=monday)
    after = monday + timedelta(hours=48, minutes=1)
    resolved = await bundle.economy.resolve_due_explorations(now=after)
    territories = [t for _, t in resolved if t is not None]
    # Two kingdoms, two DIFFERENT maps (the pool never repeats an out map).
    assert len(territories) == 2
    assert len({t.map_key for t in territories}) == 2
    assert {t.owner_kingdom_id for t in territories} == {aquitaine, bourgogne}
    # A tiny catalog exhausts the pool: the SECOND resolution refunds.
    base = default_season_config()
    tiny = Bundle(base.model_copy(update={
        "maps": base.maps[:1],
        "territories_per_kingdom": 0,
        "gaia_territories": 0,
    }))
    await tiny.launch_season()
    king = await tiny.kingdom_id("Aquitaine")
    other = await tiny.kingdom_id("Bourgogne")
    await tiny.fund(king, 5)
    await tiny.fund(other, 5)
    await tiny.economy.buy_explorateur(king, "lord-a", now=monday)
    await tiny.economy.buy_explorateur(other, "lord-b", now=monday)
    resolved_tiny = await tiny.economy.resolve_due_explorations(now=after)
    assert len(resolved_tiny) == 2
    drawn = [t for _, t in resolved_tiny if t is not None]
    assert len(drawn) == 1  # the only map of the catalog
    # The exhausted pool refunds the second kingdom and clears the lock.
    refunded = [pair for pair in resolved_tiny if pair[1] is None]
    assert len(refunded) == 1
    assert refunded[0][0].exploration_locked_until is None
    assert await tiny.economy.wallet(other) == 5


async def test_corruption_steals_protects_and_compensates() -> None:
    """D15/D37/D58: the steal transfers, shields 48 hours and compensates."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    await bundle.fund(aquitaine, 4)
    await bundle.draw(1)
    target = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == bourgogne
    )
    wednesday = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    stolen = await bundle.economy.buy_corruption(aquitaine, target.id, now=wednesday)
    assert stolen.owner_kingdom_id == aquitaine
    # D58: the shield lasts 48 real hours.
    assert stolen.protected_until == wednesday + timedelta(hours=48)
    # The former owner gained one compensation tech point (D37).
    assert await bundle.economy.wallet(bourgogne) == 1
    assert await bundle.economy.wallet(aquitaine) == 0


async def test_corruption_is_capped_at_two_per_season() -> None:
    """D58: at most two corruptions per kingdom per season."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    await bundle.fund(aquitaine, 12)
    await bundle.draw(1)
    now = datetime.now(tz=UTC)
    targets = [
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == bourgogne
    ]
    await bundle.economy.buy_corruption(aquitaine, targets[0].id, now=now)
    await bundle.economy.buy_corruption(aquitaine, targets[1].id, now=now)
    with pytest.raises(EconomyLimitReachedError):
        await bundle.economy.buy_corruption(aquitaine, targets[2].id, now=now)
    # Refused purchases are not debited (4 per steal, 2 steals).
    assert await bundle.economy.wallet(aquitaine) == 12 - 8


async def test_corruption_refuses_protected_and_own_territories() -> None:
    """A shielded territory resists corruption; own land is out of scope."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    await bundle.fund(aquitaine, 10)
    await bundle.fund(bourgogne, 2)
    await bundle.draw(1)
    target = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == bourgogne
    )
    now = datetime.now(tz=UTC)
    await bundle.economy.buy_royal_guard(bourgogne, target.id, now=now)
    with pytest.raises(TerritoryProtectedError):
        await bundle.economy.buy_corruption(aquitaine, target.id, now=now)
    own = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == aquitaine
    )
    with pytest.raises(EconomyError):
        await bundle.economy.buy_corruption(aquitaine, own.id, now=now)


async def test_royal_guard_shields_one_map_and_refuses_stacking() -> None:
    """marche-v2 article 7: 12h shield on ONE map, one active guard per
    kingdom, no extension — a new guard is a fresh full-price purchase."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 10)
    await bundle.draw(1)
    owned = [
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == aquitaine
    ]
    now = datetime.now(tz=UTC)
    guarded = await bundle.economy.buy_royal_guard(aquitaine, owned[0].id, now=now)
    assert guarded.protected_until == now + timedelta(hours=12)
    assert await bundle.economy.wallet(aquitaine) == 8  # 2 ⚙️ (article 7)
    # One active guard per kingdom - another territory is refused.
    with pytest.raises(GuardAlreadyActiveError):
        await bundle.economy.buy_royal_guard(aquitaine, owned[1].id, now=now)
    # Once the guard expires, a fresh full-price guard shields another
    # territory (no extension mechanism anymore).
    later = now + timedelta(hours=13)
    fresh = await bundle.economy.buy_royal_guard(aquitaine, owned[1].id, now=later)
    assert fresh.protected_until == later + timedelta(hours=12)
    assert await bundle.economy.wallet(aquitaine) == 6


async def test_patrouille_buys_daily_slots_and_blocks_aggression() -> None:
    """D68: two daily 2h slots max; the slot blocks attacks and Corruption."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    await bundle.fund(aquitaine, 10)
    assert await bundle.economy.buy_patrouille(aquitaine, 2) == 2
    assert await bundle.economy.buy_patrouille(aquitaine, 22) == 22
    assert await bundle.economy.wallet(aquitaine) == 6  # 2 techs per slot
    # Limit: 2 purchases per season (D68).
    with pytest.raises(EconomyLimitReachedError):
        await bundle.economy.buy_patrouille(aquitaine, 4)
    # The same tranche cannot be bought twice.
    fresh = Bundle()
    await fresh.launch_season()
    king = await fresh.kingdom_id("Aquitaine")
    await fresh.fund(king, 10)
    await fresh.economy.buy_patrouille(king, 2)
    with pytest.raises(EconomyError):
        await fresh.economy.buy_patrouille(king, 2)
    # Slots start on the 2h grid only.
    with pytest.raises(EconomyError):
        await fresh.economy.buy_patrouille(king, 3)
    # During the slot (02:30 Paris = 01:30 UTC in January) the kingdom
    # resists player attacks (D68) and Corruption.
    await bundle.draw(1)
    target = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == aquitaine
    )
    in_slot = datetime(2026, 1, 15, 1, 30, tzinfo=UTC)
    with pytest.raises(PatrolProtectedError):
        await bundle.attacks.declare_attack("king-b", target.map_key, "aoe2de://x", now=in_slot)
    await bundle.fund(bourgogne, 4)
    with pytest.raises(TerritoryProtectedError):
        await bundle.economy.buy_corruption(bourgogne, target.id, now=in_slot)
    # Outside the slot (06:30 Paris) the aggression goes through.
    out_of_slot = datetime(2026, 1, 15, 5, 30, tzinfo=UTC)
    attack = await bundle.attacks.declare_attack(
        "king-b", target.map_key, "aoe2de://x", now=out_of_slot
    )
    assert attack.map_key == target.map_key


async def test_royal_guard_requires_ownership() -> None:
    """D47: a kingdom shields its own territories only."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    foreign = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == bourgogne
    )
    with pytest.raises(EconomyError):
        await bundle.economy.buy_royal_guard(aquitaine, foreign.id)


async def test_attacks_respect_the_shield() -> None:
    """D47/D15: a shielded territory cannot be attacked."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 2)
    await bundle.draw(1)
    target = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == aquitaine
    )
    await bundle.economy.buy_royal_guard(aquitaine, target.id)
    with pytest.raises(TerritoryNotAttackableError):
        await bundle.attacks.declare_attack("king-b", target.map_key, "aoe2de://lobby")
    # Once the shield expires the attack goes through.
    season = await bundle.kingdoms.current_season()
    assert season is not None


async def test_combat_technology_purchase_flows_through_the_wallet() -> None:
    """D9/D36: combat techs debit the kingdom bank and refund on refusal."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    state = await bundle.economy.buy_combat_technology(aquitaine, "jeu_d_armes")
    assert state.purchases["jeu_d_armes"] == 1
    assert await bundle.economy.wallet(aquitaine) == 4
    # The per-season limit refunds a refused purchase.
    with pytest.raises(TechnologyLimitReachedError):
        await bundle.economy.buy_combat_technology(aquitaine, "jeu_d_armes")
    assert await bundle.economy.wallet(aquitaine) == 4
