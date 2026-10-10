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
from kingdoms.mods.kingdoms.territories import MapPoolExhaustedError

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


async def test_explorateur_buys_an_immediate_territory_once() -> None:
    """D48: two tech points buy one random non-out map, once per season."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    territory = await bundle.economy.buy_explorateur(aquitaine)
    catalog = {entry.key for entry in bundle.config.maps}
    assert territory.map_key in catalog
    assert territory.owner_kingdom_id == aquitaine
    assert await bundle.economy.wallet(aquitaine) == 3
    # Consumable: a second purchase the same season is refused.
    await bundle.fund(aquitaine, 5)
    with pytest.raises(EconomyLimitReachedError):
        await bundle.economy.buy_explorateur(aquitaine)


async def test_explorateur_draws_without_duplicate_and_refuses_empty_pool() -> None:
    """D48: the draw never repeats an out map; an empty pool refunds."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    first = await bundle.economy.buy_explorateur(aquitaine)
    # The map just drawn is out for everyone (S8): another kingdom
    # cannot receive it again.
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.fund(bourgogne, 5)
    second = await bundle.economy.buy_explorateur(bourgogne)
    assert second.map_key != first.map_key
    # A tiny catalog exhausts the pool: the purchase refunds and fails.
    base = default_season_config()
    tiny = Bundle(base.model_copy(update={
        "maps": base.maps[:1],
        "territories_per_kingdom": 0,
        "gaia_territories": 0,
    }))
    await tiny.launch_season()
    king = await tiny.kingdom_id("Aquitaine")
    await tiny.fund(king, 5)
    await tiny.economy.buy_explorateur(king)
    other = await tiny.kingdom_id("Bourgogne")
    await tiny.fund(other, 5)
    with pytest.raises(MapPoolExhaustedError):
        await tiny.economy.buy_explorateur(other)
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
    await bundle.fund(bourgogne, 1)
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


async def test_royal_guard_shields_extends_and_refuses_stacking() -> None:
    """D47/D61: 24h shield, one active guard, rising-cost extensions."""
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
    assert guarded.protected_until == now + timedelta(hours=24)
    assert await bundle.economy.wallet(aquitaine) == 9
    # D61: one active guard per kingdom - another territory is refused.
    with pytest.raises(GuardAlreadyActiveError):
        await bundle.economy.buy_royal_guard(aquitaine, owned[1].id, now=now)
    # Extensions cost 1, then 2 (D61) and add three hours each.
    extended = await bundle.economy.extend_royal_guard(aquitaine, owned[0].id, now=now)
    assert extended.protected_until == now + timedelta(hours=27)
    assert await bundle.economy.wallet(aquitaine) == 8
    extended = await bundle.economy.extend_royal_guard(aquitaine, owned[0].id, now=now)
    assert extended.protected_until == now + timedelta(hours=30)
    assert await bundle.economy.wallet(aquitaine) == 6
    # Only the territory carrying the active guard can be extended.
    with pytest.raises(TerritoryProtectedError):
        await bundle.economy.extend_royal_guard(aquitaine, owned[1].id, now=now)
    # Once the guard expires, a fresh guard may shield another territory.
    later = now + timedelta(hours=31)
    fresh = await bundle.economy.buy_royal_guard(aquitaine, owned[1].id, now=later)
    assert fresh.protected_until == later + timedelta(hours=24)


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
    await bundle.fund(aquitaine, 1)
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
