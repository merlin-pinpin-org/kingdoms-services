"""Kingdoms mod taverne-v1 tranche 1 — unit tests (Drasah 24/10/2026).

The purse-and-drinks economy behind an in-memory store: every lord
opens the season with ``purse_start`` écus (default 10), a pour debits
the purse and poses the combat lock + the GLOBAL 24h cooldown, and a
drinking lord is locked out of combat exactly like an engaged one —
no declaration, no planning, no defense, no Embuscade — until the
drink's lock expires.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import LordTavernLockedError
from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, TavernSettings, default_season_config
from kingdoms.mods.kingdoms.models import LordRole
from kingdoms.mods.kingdoms.tavern import (
    TavernCooldownError,
    TavernDrinkNotFoundError,
    TavernInsufficientPurseError,
    TavernService,
)

from .test_kingdoms_attacks import Bundle as _AttackBundle

_T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
"""A fixed pour moment (Saturday — outside the Lord's Day window)."""


class Bundle(_AttackBundle):
    """The attack bundle extended with the Taverne service."""

    def __init__(self, config: KingdomsSeasonConfig | None = None) -> None:
        super().__init__(config)
        self.tavern = TavernService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]


async def _launched() -> Bundle:
    bundle = Bundle()
    await bundle.kingdoms.launch()
    await bundle.kingdoms.enroll("king-a", "King A", LordRole.KING, proposed_name="Aquitaine")
    await bundle.kingdoms.enroll("lord-a", "Lord A", LordRole.LORD, kingdom_name="Aquitaine")
    await bundle.kingdoms.enroll("king-b", "King B", LordRole.KING, proposed_name="Bourgogne")
    await bundle.kingdoms.enroll("lord-b", "Lord B", LordRole.LORD, kingdom_name="Bourgogne")
    return bundle


async def test_every_lord_opens_the_season_with_the_purse_start() -> None:
    bundle = await _launched()
    assert await bundle.tavern.purse("lord-a") == 10
    assert await bundle.tavern.purse("king-b") == 10


async def test_purse_start_follows_the_config() -> None:
    config = default_season_config()
    config.tavern.purse_start = 15
    bundle = Bundle(config)
    await bundle.kingdoms.launch()
    await bundle.kingdoms.enroll("king-a", "King A", LordRole.KING, proposed_name="Aquitaine")
    assert await bundle.tavern.purse("king-a") == 15


async def test_a_pour_debits_and_poses_both_locks() -> None:
    bundle = await _launched()
    lord, drink = await bundle.tavern.buy_drink("lord-a", "verre", now=_T0)
    assert drink.price == 2
    assert lord.purse == 8
    assert lord.tavern_locked_until == int((_T0 + timedelta(hours=1)).timestamp())
    # The GLOBAL 24h anti-spam cooldown, counted from the pour.
    assert lord.tavern_next_drink_at == int((_T0 + timedelta(hours=24)).timestamp())
    # The heavy drinks lock longer and cost more (catalog data).
    lord, drink = await bundle.tavern.buy_drink("king-b", "alcool", now=_T0)
    assert drink.price == 8
    assert drink.lock_minutes == 360
    assert lord.purse == 2
    assert lord.tavern_locked_until == int((_T0 + timedelta(hours=6)).timestamp())


async def test_the_global_cooldown_blocks_any_second_drink() -> None:
    bundle = await _launched()
    await bundle.tavern.buy_drink("lord-a", "verre", now=_T0)
    with pytest.raises(TavernCooldownError):
        await bundle.tavern.buy_drink("lord-a", "alcool", now=_T0 + timedelta(hours=23))
    # Past the cooldown the pour passes again — whatever the drink.
    lord, _ = await bundle.tavern.buy_drink(
        "lord-a", "vin", now=_T0 + timedelta(hours=24, minutes=1)
    )
    assert lord.purse == 4


async def test_a_cold_purse_refuses_the_drink() -> None:
    bundle = await _launched()
    lord = await bundle.tavern.lord("lord-b")
    lord.purse = 1
    await bundle.store.upsert_lord(lord.to_mongo())
    with pytest.raises(TavernInsufficientPurseError):
        await bundle.tavern.buy_drink("lord-b", "vin", now=_T0)
    # The failed pour left every lock untouched.
    reloaded = await bundle.tavern.lord("lord-b")
    assert reloaded.purse == 1
    assert reloaded.tavern_locked_until is None
    assert reloaded.tavern_next_drink_at is None


async def test_an_unknown_drink_never_pours() -> None:
    bundle = await _launched()
    with pytest.raises(TavernDrinkNotFoundError):
        await bundle.tavern.buy_drink("lord-a", "cidre", now=_T0)


async def test_a_drinking_lord_declares_plans_defends_nothing() -> None:
    bundle = await _launched()
    await bundle.territories.draw_initial(seed=1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.tavern.buy_drink("king-a", "verre", now=_T0)
    # The four combat actions bounce while the lock runs.
    with pytest.raises(LordTavernLockedError):
        await bundle.attacks.declare_attack(
            "king-a", target, "aoe2de://lobby", now=_T0 + timedelta(minutes=30)
        )
    with pytest.raises(LordTavernLockedError):
        await bundle.attacks.plan_attack(
            "king-a",
            [target],
            _T0 + timedelta(hours=7),
            now=_T0 + timedelta(minutes=30),
        )
    # The Embuscade bounces too (the lock fires before the stock check).
    with pytest.raises(LordTavernLockedError):
        await bundle.attacks.declare_embuscade(
            "king-a", target, now=_T0 + timedelta(minutes=30)
        )
    # Another lord's declaration stands; the DRINKING defender bounces.
    attack = await bundle.attacks.declare_attack(
        "king-b", (await bundle.owner_maps(await bundle.kingdom_id("Aquitaine")))[0],
        "aoe2de://lobby",
        now=_T0 + timedelta(minutes=40),
    )
    await bundle.tavern.buy_drink("lord-a", "verre", now=_T0)
    with pytest.raises(LordTavernLockedError):
        await bundle.attacks.respond_defense(
            attack.id, "lord-a", now=_T0 + timedelta(minutes=45)
        )
    # Past the lock the same declaration passes — the lock expired.
    later = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", now=_T0 + timedelta(hours=1, minutes=1)
    )
    assert later.state is not None


async def test_the_tavern_lock_is_per_lord() -> None:
    bundle = await _launched()
    await bundle.territories.draw_initial(seed=3)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.tavern.buy_drink("king-a", "verre", now=_T0)
    # The OTHER lord of the same kingdom keeps every action.
    attack = await bundle.attacks.declare_attack(
        "lord-a", target, "aoe2de://lobby", now=_T0 + timedelta(minutes=30)
    )
    assert attack.map_key == target


def test_the_default_catalog_carries_drasahs_three_drinks() -> None:
    settings = TavernSettings()
    assert [drink.key for drink in settings.drinks] == ["verre", "vin", "alcool"]
    assert settings.drink("verre").price == 2
    assert settings.drink("vin").price == 4
    assert settings.drink("alcool").price == 8
    assert settings.drink("verre").lock_minutes == 60
    assert settings.drink("vin").lock_minutes == 180
    assert settings.drink("alcool").lock_minutes == 360
    assert settings.drink_cooldown_hours == 24
    assert settings.purse_start == 10
    assert settings.drink("cidre") is None
