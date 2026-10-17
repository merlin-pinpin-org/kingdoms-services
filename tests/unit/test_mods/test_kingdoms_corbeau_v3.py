"""Kingdoms mod corbeau-v3 (Drasah 15/10/2026) — unit tests.

Behind an in-memory store: the ⚙️ THEFT lifecycle — the 1h send→execute
delay, the 1st ⚙️ stolen at execution, the 5-minute contest window, the
thief's « Continuer ? » prompt (STOP keeps the 1st ⚙️ safe, CONTINUER
risks everything: a contest kills the Corbeau, refunds ALL and purges
the slot; no answer auto-settles the 1st ⚙️), the « vole ce qui reste »
bank rule, the one-theft-per-kingdom gate, the 72h cooldown and the
verify asymmetry (inspect_attack reveals the attacker's 3 civs).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.voliere import (
    VoliereLockedError,
    VoliereTargetError,
    VoliereTheftError,
)

from .test_kingdoms_voliere import VoliereBundle

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
"""A Sunday 14:00 Paris moment — outside the Lord's Day window."""


async def _bank(bundle: VoliereBundle, kingdom_id: str, amount: int) -> None:
    """Force a kingdom's ⚙️ bank (test helper)."""
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == kingdom_id)
    kingdom.tech_points_bank = amount
    await bundle.store.upsert_kingdom(kingdom.to_mongo())


async def _theft_of(bundle: VoliereBundle, kingdom_id: str) -> dict:
    """Return the live theft doc of a kingdom."""
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == kingdom_id)
    return dict(kingdom.corbeau_theft or {})


async def _corbeau_ready(bundle: VoliereBundle) -> tuple[str, str]:
    """Launch the season, reach the Féodal and arm one Corbeau."""
    await bundle.launch_season()
    await bundle.to_feudal()
    await bundle.draw(1)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(aquitaine, "corbeau")
    return aquitaine, bourgogne


# ----------------------------------------------------------------------
# The send (1h delay, gates, 72h cooldown)
# ----------------------------------------------------------------------


async def test_theft_sends_pending_with_one_hour_delay() -> None:
    """corbeau-v3: nothing is stolen at the send — execution is 1h later."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    theft = await bundle.voliere.send_corbeau_theft(
        aquitaine, bourgogne, "king-a", now=_NOW
    )
    assert theft["state"] == "pending"
    assert theft["stolen"] == 0
    assert theft["execute_at"] == int(_NOW.timestamp()) + 3600
    assert theft["target_kingdom_name"] == "Bourgogne"
    # The whole Corbeau flies for 72h (was 168h in v2).
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert kingdom.voliere_cooldowns["corbeau"] == int(_NOW.timestamp()) + 72 * 3600
    # The chronique of the CLICKING lord carries the send.
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.chronicle[-1].kind == "corbeau_theft"
    assert lord.chronicle[-1].params["target"] == "Bourgogne"


async def test_theft_gates_self_and_second_flight() -> None:
    """A kingdom cannot rob itself nor run two corbeau thefts at once."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    with pytest.raises(VoliereTargetError):
        await bundle.voliere.send_corbeau_theft(
            aquitaine, aquitaine, "king-a", now=_NOW
        )
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    with pytest.raises(VoliereTheftError):
        await bundle.voliere.send_corbeau_theft(
            aquitaine, bourgogne, "king-a", now=_NOW
        )


async def test_theft_needs_the_corbeau_slot_and_the_feudal_age() -> None:
    """No corbeau in the volière (or before the Féodal) — no theft."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    with pytest.raises(VoliereLockedError):
        await bundle.voliere.send_corbeau_theft(
            aquitaine, bourgogne, "king-a", now=_NOW
        )
    await bundle.to_feudal()
    with pytest.raises(VoliereLockedError):
        await bundle.voliere.send_corbeau_theft(
            aquitaine, bourgogne, "king-a", now=_NOW
        )


# ----------------------------------------------------------------------
# The execution (1st ⚙️, contest window, continue prompt)
# ----------------------------------------------------------------------


async def test_resolve_before_the_delay_is_a_no_op() -> None:
    """Nothing executes before the 1h delay — the alarm list stays empty."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(minutes=59))
    assert executed == []
    assert (await _theft_of(bundle, aquitaine))["state"] == "pending"


async def test_execution_steals_the_first_point_and_opens_the_window() -> None:
    """At the delay: 1 ⚙️ moves, the 5-minute contest window opens."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    assert len(executed) == 1
    theft = await _theft_of(bundle, aquitaine)
    assert theft["state"] == "awaiting_continue"
    assert theft["stolen"] == 1
    assert theft["contest_until"] == int(_NOW.timestamp()) + 3600 + 5 * 60
    kingdoms = {k.name: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms["Bourgogne"].tech_points_bank == 4
    assert kingdoms["Aquitaine"].tech_points_bank == 1


async def test_stop_keeps_the_first_point_safe() -> None:
    """STOP at the prompt: the thief leaves with the 1st ⚙️, nothing to contest."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    result = await bundle.voliere.continue_theft(
        executed[0]["id"], aquitaine, False, now=_NOW + timedelta(hours=1, minutes=1)
    )
    assert result["state"] == "settled_first"
    assert result["stolen"] == 1
    kingdoms = {k.name: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms["Bourgogne"].tech_points_bank == 4
    assert kingdoms["Aquitaine"].tech_points_bank == 1


async def test_continue_then_window_expiry_settles_both() -> None:
    """CONTINUER with no contest: the corbeau leaves with BOTH ⚙️."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    result = await bundle.voliere.continue_theft(
        executed[0]["id"], aquitaine, True, now=_NOW + timedelta(hours=1, minutes=1)
    )
    assert result["state"] == "continuing"
    assert result["stolen"] == 2
    await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1, minutes=6))
    theft = await _theft_of(bundle, aquitaine)
    assert theft["state"] == "settled_both"
    assert theft["stolen"] == 2
    kingdoms = {k.name: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms["Bourgogne"].tech_points_bank == 3
    assert kingdoms["Aquitaine"].tech_points_bank == 2


async def test_no_answer_auto_settles_the_first_point() -> None:
    """A silent thief keeps the 1st ⚙️ — the window closes by itself."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    executed = await bundle.voliere.resolve_due_thefts(
        now=_NOW + timedelta(hours=1, minutes=5)
    )
    assert executed == []
    theft = await _theft_of(bundle, aquitaine)
    assert theft["state"] == "settled_first"
    assert theft["stolen"] == 1
    kingdoms = {k.name: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms["Aquitaine"].tech_points_bank == 1


# ----------------------------------------------------------------------
# The contest (Corbeau mort, refund, slot purgé)
# ----------------------------------------------------------------------


async def test_contest_kills_the_corbeau_and_refunds_everything() -> None:
    """A contest inside the window: dead corbeau, full refund, slot purged."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    result = await bundle.voliere.contest_theft(
        executed[0]["id"], bourgogne, now=_NOW + timedelta(hours=1, minutes=2)
    )
    assert result["state"] == "dead"
    assert result["refunded"] == 1
    assert result["thief_kingdom_name"] == "Aquitaine"
    kingdoms = {k.name: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms["Bourgogne"].tech_points_bank == 5
    assert kingdoms["Aquitaine"].tech_points_bank == 0
    assert "corbeau" not in (kingdoms["Aquitaine"].voliere_slots or [])
    assert (await _theft_of(bundle, aquitaine))["state"] == "dead"


async def test_contest_after_the_window_is_refused() -> None:
    """Once the 5 minutes are over there is nothing left to contest."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    with pytest.raises(VoliereTheftError):
        await bundle.voliere.contest_theft(
            executed[0]["id"], bourgogne, now=_NOW + timedelta(hours=1, minutes=5)
        )


async def test_contest_on_a_continuing_theft_refunds_both() -> None:
    """CONTINUER then contest: the corbeau dies and BOTH ⚙️ go back."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 5)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    await bundle.voliere.continue_theft(
        executed[0]["id"], aquitaine, True, now=_NOW + timedelta(hours=1, minutes=1)
    )
    result = await bundle.voliere.contest_theft(
        executed[0]["id"], bourgogne, now=_NOW + timedelta(hours=1, minutes=2)
    )
    assert result["state"] == "dead"
    assert result["refunded"] == 2
    kingdoms = {k.name: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms["Bourgogne"].tech_points_bank == 5
    assert kingdoms["Aquitaine"].tech_points_bank == 0


# ----------------------------------------------------------------------
# « Le Corbeau vole ce qui reste »
# ----------------------------------------------------------------------


async def test_empty_bank_settles_without_prompt() -> None:
    """A bank at 0 settles the theft with nothing and NO continue prompt."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 0)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    assert executed == []
    theft = await _theft_of(bundle, aquitaine)
    assert theft["state"] == "settled_first"
    assert theft["stolen"] == 0


async def test_bank_of_one_steals_what_remains() -> None:
    """A bank below 2 gives what remains — here a single ⚙️."""
    bundle = VoliereBundle()
    aquitaine, bourgogne = await _corbeau_ready(bundle)
    await _bank(bundle, bourgogne, 1)
    await bundle.voliere.send_corbeau_theft(aquitaine, bourgogne, "king-a", now=_NOW)
    executed = await bundle.voliere.resolve_due_thefts(now=_NOW + timedelta(hours=1))
    assert len(executed) == 1
    theft = await _theft_of(bundle, aquitaine)
    assert theft["state"] == "awaiting_continue"
    assert theft["stolen"] == 1
    kingdoms = {k.name: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms["Bourgogne"].tech_points_bank == 0
    assert kingdoms["Aquitaine"].tech_points_bank == 1
    # CONTINUER on an empty bank steals nothing more.
    result = await bundle.voliere.continue_theft(
        executed[0]["id"], aquitaine, True, now=_NOW + timedelta(hours=1, minutes=1)
    )
    assert result["stolen"] == 1
    assert result["state"] == "continuing"


# ----------------------------------------------------------------------
# corbeau_verify — the attacker reveals his 3 civs FIRST
# ----------------------------------------------------------------------


async def test_inspect_attack_reveals_the_attacker_civs() -> None:
    """corbeau-v3 asymmetry: the inspection carries the attacker's 3 civs."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    await bundle.draw(2)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(bourgogne, "corbeau")
    await bundle.stock(aquitaine, contre_espionnage=1)
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    kingdom.civilizations = ["civ-0", "civ-1", "civ-2"]
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://x", cover_lord_id="lord-a", now=_NOW
    )
    inspection = await bundle.voliere.inspect_attack(
        bourgogne, attack.id, now=_NOW
    )
    assert inspection["attacker_name"] == "King A"
    assert inspection["attacker_lord_id"] == "king-a"
    assert inspection["map_key"] == target
    assert inspection["attacker_civs"] == ["civ-0", "civ-1", "civ-2"]
