"""Kingdoms mod admin-debug-v1 (Drasah 15/10/2026) — unit tests.

Behind an in-memory store: every surgical mutation of the
``/kingdoms-admin debug`` toolkit — the volière cooldown reset, the
bird slot restoration, the lord lock purge, the attack phase forcing,
the tech-bank adjustment, the territory and civ handovers, the
chronicle injection, the corbeau theft clock fast-forward and the
status report — plus the ADMIN BYPASS: with the process flag ON, the
player-button gate checks (slot, budget, tier, treasury) let the real
flow run; with it OFF, every gate applies again.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kingdoms.mods.kingdoms.admin_debug import (
    DebugError,
    DebugService,
    admin_bypass_enabled,
    set_admin_bypass,
)
from kingdoms.mods.kingdoms.attacks import (
    InsufficientTechPointsError,
)
from kingdoms.mods.kingdoms.cathedrale import (
    CathedralService,
    CathedralTierError,
)
from kingdoms.mods.kingdoms.economy import InsufficientPointsError
from kingdoms.mods.kingdoms.service import ParishInsufficientTechError
from kingdoms.mods.kingdoms.voliere import VoliereLockedError

from .test_kingdoms_voliere import VoliereBundle

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _bypass_off_after_each_test() -> None:
    """Keep the process-global bypass flag OFF after every test."""
    set_admin_bypass(False)
    yield
    set_admin_bypass(False)


class DebugBundle(VoliereBundle):
    """The volière bundle extended with the debug toolkit."""

    def __init__(self) -> None:
        super().__init__()
        self.debug = DebugService(
            self.store,
            self.config,
            self.kingdoms,
            self.territories,
            self.attacks,
            self.voliere,
        )
        self.cathedrals = CathedralService(
            self.store, self.config, self.kingdoms, self.territories
        )


async def _kingdom(bundle: DebugBundle, name: str):
    return next(k for k in await bundle.kingdoms.kingdoms() if k.name == name)


async def _lord(bundle: DebugBundle, lord_id: str):
    return next(item for item in await bundle.kingdoms.lords() if item.id == lord_id)


# ----------------------------------------------------------------------
# The volière mutations (cooldown reset, bird slot restoration)
# ----------------------------------------------------------------------


async def test_reset_voliere_cooldown_lands_the_bird_now() -> None:
    """voliere-reset: the cooldown lands now (or N minutes from now)."""
    bundle = DebugBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    report = await bundle.debug.reset_voliere_cooldown("Aquitaine", "corbeau", now=_NOW)
    assert report["bird"] == "corbeau"
    kingdom = await _kingdom(bundle, "Aquitaine")
    assert kingdom.voliere_cooldowns["corbeau"] == int(_NOW.timestamp())
    # Resolution works by display name casefold AND by raw id.
    later = await bundle.debug.reset_voliere_cooldown(aquitaine, "all", 10, now=_NOW)
    assert later["ready_at"] == int(_NOW.timestamp()) + 600
    with pytest.raises(DebugError):
        await bundle.debug.reset_voliere_cooldown("Aquitaine", "perruche", now=_NOW)
    with pytest.raises(DebugError):
        await bundle.debug.reset_voliere_cooldown("Nulle part", "corbeau", now=_NOW)


async def test_grant_bird_restores_a_dead_corbeau_slot() -> None:
    """bird-grant: the contest-killed Corbeau comes back to its perch."""
    bundle = DebugBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "corbeau")
    kingdom = await _kingdom(bundle, "Aquitaine")
    assert kingdom.voliere_slots == ["corbeau"]
    kingdom.voliere_slots = []
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    report = await bundle.debug.grant_bird("Aquitaine", "corbeau", now=_NOW)
    assert report["slots"] == ["corbeau"]
    fresh = await _kingdom(bundle, "Aquitaine")
    assert fresh.voliere_slots == ["corbeau"]
    assert fresh.voliere_cooldowns["corbeau"] == int(_NOW.timestamp())


# ----------------------------------------------------------------------
# The lord lock purge
# ----------------------------------------------------------------------


async def test_clear_lock_purges_every_real_time_lock() -> None:
    """lock-clear: marriage/Exploration/no-show locks vanish at once."""
    bundle = DebugBundle()
    await bundle.launch_season()
    lord = await _lord(bundle, "lord-a")
    lord.marriage_locked_until = int(_NOW.timestamp()) + 3600
    lord.exploration_locked_until = int(_NOW.timestamp()) + 3600
    lord.ffa_locked_until = int(_NOW.timestamp()) + 3600
    await bundle.store.upsert_lord(lord.to_mongo())
    report = await bundle.debug.clear_lock("Lord A", "all")
    assert set(report["cleared"]) == {"marriage", "exploration", "ffa"}
    fresh = await _lord(bundle, "lord-a")
    assert fresh.marriage_locked_until is None
    assert fresh.exploration_locked_until is None
    assert fresh.ffa_locked_until is None
    # Purging again clears nothing (no lock set anymore).
    again = await bundle.debug.clear_lock("lord-a", "marriage")
    assert again["cleared"] == ["none-set"]
    with pytest.raises(DebugError):
        await bundle.debug.clear_lock("lord-a", "week-end")
    with pytest.raises(DebugError):
        await bundle.debug.clear_lock("Personne", "all")


# ----------------------------------------------------------------------
# The attack phase forcing
# ----------------------------------------------------------------------


async def test_advance_attack_forces_the_reveal_phase() -> None:
    """attack-advance reveal: the defense cutoff lands and reveals."""
    bundle = DebugBundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", now=_NOW
    )
    report = await bundle.debug.advance_attack(attack.id, "reveal", now=_NOW)
    assert report["stage"] == "reveal"
    assert "reveal" in report["ran"]
    with pytest.raises(DebugError):
        await bundle.debug.advance_attack(attack.id, "nulle", now=_NOW)
    with pytest.raises(DebugError):
        await bundle.debug.advance_attack("attack-404", "reveal", now=_NOW)


# ----------------------------------------------------------------------
# The bank, territory and civ handovers
# ----------------------------------------------------------------------


async def test_adjust_bank_credits_and_debits_with_a_floor() -> None:
    """bank: credit, debit, and never below zero."""
    bundle = DebugBundle()
    await bundle.launch_season()
    report = await bundle.debug.adjust_bank("aquitaine", 4)
    assert report == {"kingdom": "Aquitaine", "before": 0, "after": 4}
    debited = await bundle.debug.adjust_bank("Aquitaine", -2)
    assert debited["after"] == 2
    floored = await bundle.debug.adjust_bank("Aquitaine", -99)
    assert floored["after"] == 0
    with pytest.raises(DebugError):
        await bundle.debug.adjust_bank("Nulle part", 1)


async def test_grant_territory_transfers_the_owner() -> None:
    """territory-grant: the map key changes hands directly."""
    bundle = DebugBundle()
    await bundle.launch_season()
    await bundle.draw(1)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    report = await bundle.debug.grant_territory(target, "Aquitaine")
    assert report["to"] == "Aquitaine"
    assert target in await bundle.owner_maps(aquitaine)
    with pytest.raises(DebugError):
        await bundle.debug.grant_territory("map-404", "Aquitaine")


async def test_grant_civ_puts_one_civ_in_the_hand() -> None:
    """civ-grant: the civilization joins the kingdom's hand, once."""
    bundle = DebugBundle()
    await bundle.launch_season()
    report = await bundle.debug.grant_civ("Aquitaine", "civ-1")
    assert report["hand"] == ["civ-1"]
    twice = await bundle.debug.grant_civ("aquitaine", "civ-1")
    assert twice["hand"] == ["civ-1"]
    kingdom = await _kingdom(bundle, "Aquitaine")
    assert list(kingdom.civilizations) == ["civ-1"]


# ----------------------------------------------------------------------
# The chronicle injection
# ----------------------------------------------------------------------


async def test_inject_chronicle_appends_one_factice_entry() -> None:
    """chronicle-inject: the entry lands on the lord's chronicle."""
    bundle = DebugBundle()
    await bundle.launch_season()
    report = await bundle.debug.inject_chronicle(
        "Lord A", "marriage", {"civ": "civ-2"}, now=_NOW
    )
    assert report["kind"] == "marriage"
    lord = await _lord(bundle, "lord-a")
    assert lord.chronicle[-1].kind == "marriage"
    assert lord.chronicle[-1].params["civ"] == "civ-2"
    with pytest.raises(DebugError):
        await bundle.debug.inject_chronicle("Personne", "marriage", {}, now=_NOW)


# ----------------------------------------------------------------------
# The corbeau theft clock
# ----------------------------------------------------------------------


async def test_advance_theft_executes_and_expires_the_windows() -> None:
    """theft-advance: the 1h delay and the 5-minute window jump to NOW."""
    bundle = DebugBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    await bundle.draw(1)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(aquitaine, "corbeau")
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    # The target bank must hold ⚙️ for the theft to steal one.
    target_kingdom = await _kingdom(bundle, "Bourgogne")
    target_kingdom.tech_points_bank = 5
    await bundle.store.upsert_kingdom(target_kingdom.to_mongo())
    executed = await bundle.debug.advance_theft("Aquitaine", "execute", now=_NOW)
    assert executed["executed"] == 1
    kingdom = await _kingdom(bundle, "Aquitaine")
    theft = dict(kingdom.corbeau_theft or {})
    assert theft["state"] == "awaiting_continue"
    # Stage jump again before the window opened: refused.
    with pytest.raises(DebugError):
        await bundle.debug.advance_theft("Aquitaine", "execute", now=_NOW)


async def test_advance_theft_refuses_without_a_running_theft() -> None:
    """theft-advance: no corbeau theft, no clock to fast-forward."""
    bundle = DebugBundle()
    await bundle.launch_season()
    with pytest.raises(DebugError):
        await bundle.debug.advance_theft("Aquitaine", "execute", now=_NOW)


# ----------------------------------------------------------------------
# The status report
# ----------------------------------------------------------------------


async def test_status_reports_the_bypass_flag_and_running_thefts() -> None:
    """status: bypass flag + the corbeau thefts still in flight."""
    bundle = DebugBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    await bundle.draw(1)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(aquitaine, "corbeau")
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    report = await bundle.debug.status(now=_NOW)
    assert report["bypass"] is False
    assert len(report["thefts"]) == 1
    assert report["thefts"][0]["kingdom"] == "Aquitaine"


# ----------------------------------------------------------------------
# The ADMIN BYPASS — the player-button gates
# ----------------------------------------------------------------------


async def test_bypass_flag_toggles_and_reports() -> None:
    """bypass on/off: the process flag flips and the status follows."""
    assert admin_bypass_enabled() is False
    set_admin_bypass(True, source="test")
    assert admin_bypass_enabled() is True
    set_admin_bypass(False, source="test")
    assert admin_bypass_enabled() is False


async def test_bypass_unlocks_the_voliere_age_gate() -> None:
    """With the flag ON, the Féodal/slot/cooldown gates let the draw run."""
    bundle = DebugBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    with pytest.raises(VoliereLockedError):
        await bundle.voliere.pick_civilization(aquitaine, "pigeon_civ", now=_NOW)
    set_admin_bypass(True, source="test")
    # Before the Féodal, with NO pigeon on its perch — the three gates
    # (feudal age, bird slot, cooldown) are all bypassed at once.
    drawn = await bundle.voliere.pick_civilization(aquitaine, "pigeon_civ", now=_NOW)
    assert drawn != ""


async def test_bypass_unlocks_the_economy_treasury_gate() -> None:
    """With the flag ON, a broke kingdom still buys through the market."""
    bundle = DebugBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    with pytest.raises(InsufficientPointsError):
        await bundle.economy.spend_points(aquitaine, 5)
    set_admin_bypass(True, source="test")
    await bundle.economy.spend_points(aquitaine, 5)
    kingdom = await _kingdom(bundle, "Aquitaine")
    # The bypassed path pays what the treasury holds (never negative).
    assert kingdom.tech_points_bank == 0


async def test_bypass_unlocks_the_combat_technology_gates() -> None:
    """With the flag ON, tech points and per-season limits stop blocking."""
    bundle = DebugBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    from kingdoms.mods.kingdoms.attacks import COMBAT_TECHNOLOGIES

    technology = next(iter(COMBAT_TECHNOLOGIES))
    with pytest.raises(InsufficientTechPointsError):
        await bundle.attacks.buy_technology(aquitaine, technology)
    set_admin_bypass(True, source="test")
    state = await bundle.attacks.buy_technology(aquitaine, technology)
    assert state.purchases[technology] == 1
    assert state.tech_points >= 0
    # The per-season limit no longer blocks either.
    again = await bundle.attacks.buy_technology(aquitaine, technology)
    assert again.purchases[technology] == 2
    set_admin_bypass(False, source="test")
    with pytest.raises(InsufficientTechPointsError):
        await bundle.attacks.buy_technology(aquitaine, technology)


async def test_bypass_unlocks_the_parish_and_cathedral_gates() -> None:
    """With the flag ON, the Paroisse treasury and Cathédrale tier let go."""
    bundle = DebugBundle()
    await bundle.launch_season()
    await bundle.draw(1)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    with pytest.raises(ParishInsufficientTechError):
        await bundle.kingdoms.upgrade_parish(aquitaine)
    set_admin_bypass(True, source="test")
    kingdom = await bundle.kingdoms.upgrade_parish(aquitaine)
    assert kingdom.parish_tier == 2
    # With the flag OFF, a second upgrade without funds is refused again.
    set_admin_bypass(False, source="test")
    with pytest.raises(ParishInsufficientTechError):
        await bundle.kingdoms.upgrade_parish(aquitaine)


async def test_bypass_unlocks_the_cathedral_tier_and_treasury() -> None:
    """With the flag ON, a low-tier broke kingdom consecrates anyway."""
    bundle = DebugBundle()
    await bundle.launch_season()
    await bundle.draw(1)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    target = (await bundle.owner_maps(aquitaine))[0]
    with pytest.raises(CathedralTierError):
        await bundle.cathedrals.consecrate("king-a", target, now=_NOW)
    kingdom = await _kingdom(bundle, "Aquitaine")
    kingdom.tech_points_bank = 0
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    set_admin_bypass(True, source="test")
    territory = await bundle.cathedrals.consecrate("king-a", target, now=_NOW)
    assert territory.cathedral_until is not None
    assert territory.cathedral_until > _NOW
    # With the flag back OFF, a second consecration hits the tier gate
    # again (the first gate the flow meets — tier before treasury).
    set_admin_bypass(False, source="test")
    kingdom = await _kingdom(bundle, "Aquitaine")
    kingdom.tech_points_bank = 0
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    with pytest.raises(CathedralTierError):
        await bundle.cathedrals.consecrate("king-a", target, now=_NOW)
