"""Kingdoms mod Paroisse (church) — unit tests (D64/D59/D60, 2026-10-11).

The three-palier ladder (Chapelle → Église → Cathédrale): the upgrade
purchase, the standard marriage consuming ONE point de mariage and
crediting the palier's Offrande, the arranged-marriage tokens sold at
the Marché, the Lord's Day point grant, and the Gaïa refusal.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kingdoms.mods.kingdoms.diplomacy import (
    DiplomacyService,
    NoMarriagePointsError,
)
from kingdoms.mods.kingdoms.economy import EconomyService, InsufficientPointsError
from kingdoms.mods.kingdoms.events import EventService
from kingdoms.mods.kingdoms.service import (
    ParishError,
    ParishInsufficientTechError,
    ParishMaxTierError,
)

from .test_kingdoms_attacks import Bundle as _AttackBundle
from .test_kingdoms_diplomacy import _config as _diplomacy_config


class Bundle(_AttackBundle):
    """The attack bundle extended with the Paroisse services."""

    def __init__(self, config=None) -> None:
        super().__init__(config or _diplomacy_config())
        self.economy = EconomyService(  # type: ignore[arg-type]
            self.store, self.config, self.kingdoms, self.territories, self.attacks
        )
        self.diplomacy = DiplomacyService(  # type: ignore[arg-type]
            self.store, self.config, self.kingdoms, self.territories
        )
        self.events = EventService(  # type: ignore[arg-type]
            self.store, self.config, self.kingdoms, self.territories, self.attacks
        )

    async def kingdom_of(self, name: str):
        """Return the fresh kingdom record by display name."""
        kid = await self.kingdom_id(name)
        return next(k for k in await self.kingdoms.kingdoms() if k.id == kid)


async def test_marry_consumes_one_point_and_refuses_at_zero() -> None:
    """D59: the points de mariage are a currency — one per marriage."""
    bundle = Bundle()
    await bundle.launch_season()
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.marriage_points = 0
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    with pytest.raises(NoMarriagePointsError):
        await bundle.diplomacy.marry("king-a", "celtes")
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.marriage_points = 1
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    assert await bundle.diplomacy.marry("king-a", "celtes") == "celtes"
    assert (await bundle.kingdom_of("Aquitaine")).marriage_points == 0


async def test_palier_drives_the_lock_and_the_offrande() -> None:
    """D64: Église locks 12h + Offrande +1, Cathédrale locks 6h + Offrande +2."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.economy.grant_tech_points(aquitaine, 6)

    await bundle.kingdoms.upgrade_parish(aquitaine)  # → Église, -2 🔬
    now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    await bundle.diplomacy.marry("king-a", "celtes", now=now)
    kingdom = await bundle.kingdom_of("Aquitaine")
    assert kingdom.parish_tier == 2
    assert kingdom.tech_points_bank == 6 - 2 + 1  # the Offrande is credited
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.marriage_locked_until == int(
        datetime(2026, 10, 11, 12, 0, tzinfo=UTC).timestamp() + 12 * 3600
    )

    await bundle.kingdoms.upgrade_parish(aquitaine)  # → Cathédrale, -4 🔬
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.marriage_points = 1
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    await bundle.diplomacy.marry("lord-a", "shu", now=now)
    kingdom = await bundle.kingdom_of("Aquitaine")
    assert kingdom.parish_tier == 3
    assert kingdom.tech_points_bank == 5 - 4 + 2  # the Cathédrale Offrande
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-a")
    assert lord.marriage_locked_until == int(
        datetime(2026, 10, 11, 12, 0, tzinfo=UTC).timestamp() + 6 * 3600
    )


async def test_market_sells_arranged_marriage_tokens() -> None:
    """D60: the Marché sells the token (3 🔬), the stock grows by one."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    with pytest.raises(InsufficientPointsError):
        await bundle.economy.buy_arranged_marriage_token(aquitaine)
    await bundle.economy.grant_tech_points(aquitaine, 3)
    kingdom = await bundle.economy.buy_arranged_marriage_token(aquitaine)
    assert kingdom.arranged_marriage_stock == 1
    assert await bundle.economy.wallet(aquitaine) == 0


async def test_upgrade_refuses_short_treasury_and_top_palier() -> None:
    """D64: the direct buy fails loud on a short treasury or at the top."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    with pytest.raises(ParishInsufficientTechError):
        await bundle.kingdoms.upgrade_parish(aquitaine)  # 2 🔬 needed, 0 owned
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.parish_tier = 3
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    with pytest.raises(ParishMaxTierError):
        await bundle.kingdoms.upgrade_parish(aquitaine)


async def test_gaia_never_upgrades_a_paroisse() -> None:
    """Gaïa owns no Paroisse — the purchase is refused."""
    bundle = Bundle()
    await bundle.launch_season()
    gaia = next(k.id for k in await bundle.kingdoms.kingdoms() if k.is_gaia)
    with pytest.raises(ParishError):
        await bundle.kingdoms.upgrade_parish(gaia)


async def test_lords_day_grants_one_marriage_point_per_kingdom() -> None:
    """D64: the Jour du Seigneur recharges one point de mariage."""
    bundle = Bundle()
    await bundle.launch_season()
    report = await bundle.events.run_cycle_switch(seed=1)
    assert report["marriage_points"] == 2  # two player kingdoms
    kingdoms = [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia]
    assert all(k.marriage_points == 2 for k in kingdoms)  # 1 launch + 1 Lord's Day
