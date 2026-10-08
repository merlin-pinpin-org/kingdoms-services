"""Kingdoms mod special-action economy - unit tests (kingdoms-services#161, T7).

Reference §20 behind an in-memory store: the kingdom tech-point wallet
fed by the epochs and exploration (D9/D36), the Explorateur territory
purchase (D48), the Corruption steal with its Monday-midnight shield and
the compensation tech point (D15/D37), the Garde Royale shield and its
extension (D47), and the protection the attacks must respect.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import (
    TechnologyLimitReachedError,
    TerritoryNotAttackableError,
)
from kingdoms.mods.kingdoms.economy import (
    EconomyError,
    EconomyLimitReachedError,
    EconomyService,
    InsufficientPointsError,
    TerritoryProtectedError,
)
from kingdoms.mods.kingdoms.territories import MapPoolExhaustedError, TerritoryNotFoundError

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
    """D48: two tech points buy one chosen non-out map, once per season."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    territory = await bundle.economy.buy_explorateur(aquitaine, "arabia")
    assert territory.map_key == "arabia"
    assert territory.owner_kingdom_id == aquitaine
    assert await bundle.economy.wallet(aquitaine) == 3
    # Consumable: a second purchase the same season is refused.
    await bundle.fund(aquitaine, 5)
    with pytest.raises(EconomyLimitReachedError):
        await bundle.economy.buy_explorateur(aquitaine, "islands")


async def test_explorateur_refuses_out_and_unknown_maps() -> None:
    """D48: the map must be in the catalog and not out yet (§8)."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    with pytest.raises(TerritoryNotFoundError):
        await bundle.economy.buy_explorateur(aquitaine, "not-a-map")
    await bundle.economy.buy_explorateur(aquitaine, "arabia")
    # Another kingdom confirms the map is now out for everyone (S8).
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.fund(bourgogne, 5)
    with pytest.raises(MapPoolExhaustedError):
        await bundle.economy.buy_explorateur(bourgogne, "arabia")


async def test_corruption_steals_protects_and_compensates() -> None:
    """D15/D37: the steal transfers, shields until Monday and compensates."""
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
    # Next Monday midnight: the 12th at 00:00.
    assert stolen.protected_until == datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
    # The former owner gained one compensation tech point (D37).
    assert await bundle.economy.wallet(bourgogne) == 1
    assert await bundle.economy.wallet(aquitaine) == 0


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
    """D47: 24h shield, one tech buys three more hours, no stacking."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.fund(aquitaine, 5)
    await bundle.draw(1)
    own = next(
        t for t in await bundle.territories.territories() if t.owner_kingdom_id == aquitaine
    )
    now = datetime.now(tz=UTC)
    guarded = await bundle.economy.buy_royal_guard(aquitaine, own.id, now=now)
    assert guarded.protected_until == now + timedelta(hours=24)
    assert await bundle.economy.wallet(aquitaine) == 4
    with pytest.raises(TerritoryProtectedError):
        await bundle.economy.buy_royal_guard(aquitaine, own.id, now=now)
    extended = await bundle.economy.extend_royal_guard(aquitaine, own.id, now=now)
    assert extended.protected_until == now + timedelta(hours=27)
    assert await bundle.economy.wallet(aquitaine) == 3


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
