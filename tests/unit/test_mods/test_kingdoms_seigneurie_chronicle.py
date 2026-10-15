"""Kingdoms mod Seigneurie chronicle (Drasah 15/10/2026) — unit tests.

The chronicle of a lord: FACTUAL, exactly-timestamped, only the actions
the lord REALLY CLICKED. Recording is best-effort (never raises), capped
at 50 entries (oldest dropped first), and the fiche GATING lives here
too — a status + chronique render only while the target lord is « suivi »
by the consulting kingdom's Corbeau (corbeau_track, 24h window).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kingdoms.mods.kingdoms import chronicle
from kingdoms.mods.kingdoms.chronicle import CHRONICLE_CAP
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.models import LordModel

from .test_kingdoms_attacks import Bundle as AttackBundle
from .test_kingdoms_diplomacy import Bundle as DiplomacyBundle

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


async def _lord_of(bundle: Any, lord_id: str) -> LordModel:
    return next(item for item in await bundle.kingdoms.lords() if item.id == lord_id)


# ----------------------------------------------------------------------
# record / recent — the storage contract
# ----------------------------------------------------------------------


async def test_record_appends_a_timestamped_entry() -> None:
    """One entry: exact timestamp, kind and string-coerced params."""
    bundle = AttackBundle()
    await bundle.launch_season()
    lord = await _lord_of(bundle, "lord-a")
    await chronicle.record(
        lord,
        chronicle.KIND_VOLIERE_ACTION,
        now=_NOW,
        store=bundle.store,
        action="corbeau_track",
        target="Bourgogne",
    )
    fresh = await _lord_of(bundle, "lord-a")
    # The enrollment entry sits first (its own click); ours lands last.
    assert fresh.chronicle[0].kind == "enrolled"
    assert len(fresh.chronicle) == 2
    entry = fresh.chronicle[-1]
    assert entry.at == int(_NOW.timestamp())
    assert entry.kind == "voliere_action"
    assert entry.params == {"action": "corbeau_track", "target": "Bourgogne"}


async def test_record_caps_at_fifty_oldest_dropped() -> None:
    """The log keeps the LAST 50 entries — the oldest go first."""
    bundle = AttackBundle()
    await bundle.launch_season()
    lord = await _lord_of(bundle, "lord-a")
    for index in range(CHRONICLE_CAP + 10):
        await chronicle.record(
            lord,
            chronicle.KIND_MARKET_PURCHASE,
            now=_NOW + timedelta(seconds=index),
            store=bundle.store,
            item=str(index),
        )
    fresh = await _lord_of(bundle, "lord-a")
    assert len(fresh.chronicle) == CHRONICLE_CAP
    assert fresh.chronicle[0].params["item"] == "10"
    assert fresh.chronicle[-1].params["item"] == str(CHRONICLE_CAP + 9)


async def test_recent_returns_newest_first() -> None:
    """:func:`recent` hands back the last entries, NEWEST FIRST."""
    bundle = AttackBundle()
    await bundle.launch_season()
    lord = await _lord_of(bundle, "lord-a")
    for index in range(5):
        await chronicle.record(
            lord,
            chronicle.KIND_MARKET_PURCHASE,
            now=_NOW + timedelta(seconds=index),
            store=bundle.store,
            item=str(index),
        )
    top = chronicle.recent(lord, 3)
    assert [entry.params["item"] for entry in top] == ["4", "3", "2"]


async def test_record_never_breaks_the_action() -> None:
    """A broken store is swallowed — the game action stays unharmed."""
    bundle = AttackBundle()
    await bundle.launch_season()
    lord = await _lord_of(bundle, "lord-a")

    class BrokenStore:
        async def upsert_lord(self, document: dict) -> None:
            raise RuntimeError("mongo down")

    await chronicle.record(
        lord, chronicle.KIND_LEFT, now=_NOW, store=BrokenStore()
    )


# ----------------------------------------------------------------------
# The wiring — only CLICKED actions land on the clicking lord
# ----------------------------------------------------------------------


async def test_enroll_and_leave_are_recorded() -> None:
    """Inscription + départ: two clicked actions, two entries."""
    bundle = AttackBundle()
    await bundle.launch_season()
    await bundle.kingdoms.enroll(
        "lord-x", "Lord X", lord_role(), kingdom_name="Aquitaine"
    )
    lord = await _lord_of(bundle, "lord-x")
    assert lord.chronicle[-1].kind == "enrolled"
    await bundle.kingdoms.leave("lord-x", reason="test")
    fresh = await _lord_of(bundle, "lord-x")
    assert fresh.chronicle[-1].kind == "left"


def lord_role() -> Any:
    """Return the LORD role without importing the enum at module top."""
    from kingdoms.mods.kingdoms.models import LordRole

    return LordRole.LORD


async def test_marriage_is_recorded_on_the_marrying_lord() -> None:
    """A celebrated marriage lands on the lord who clicked it."""
    bundle = DiplomacyBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.draw_one(aquitaine, "black-forest")
    await bundle.diplomacy.recalculate()
    await bundle.diplomacy.marry("lord-a", "celtes")
    lord = await _lord_of(bundle, "lord-a")
    assert lord.chronicle[-1].kind == "marriage"
    assert lord.chronicle[-1].params["civ"] == "celtes"


async def test_declared_attack_is_recorded_on_the_attacker() -> None:
    """The attack lands on the ATTACKER's chronicle — never the victim's."""
    bundle = AttackBundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.attacks.declare_attack("king-a", target, "aoe2de://x", now=_NOW)
    attacker = await _lord_of(bundle, "king-a")
    assert attacker.chronicle[-1].kind == "attack_declared"
    victim = await _lord_of(bundle, "king-b")
    assert all(entry.kind != "attack_declared" for entry in victim.chronicle)


# ----------------------------------------------------------------------
# The GATING — corbeau_track, 24h window
# ----------------------------------------------------------------------


async def test_is_tracked_by_follows_the_24h_window() -> None:
    """:func:`is_tracked_by`: tracked inside 24h, closed after."""
    bundle = AttackBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    kingdom.corbeau_tracking = [
        {"lord_id": "king-b", "until": int(_NOW.timestamp()) + 24 * 3600}
    ]
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    fresh = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert chronicle.is_tracked_by(fresh, "king-b", _NOW)
    assert chronicle.is_tracked_by(fresh, "king-b", _NOW + timedelta(hours=23))
    assert not chronicle.is_tracked_by(fresh, "king-b", _NOW + timedelta(hours=24))
    assert not chronicle.is_tracked_by(fresh, "lord-a", _NOW)


async def test_the_fiche_gating_stays_a_pure_function() -> None:
    """No tracking, no chronique leak — the seam answers by itself."""
    bundle = AttackBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert chronicle.is_tracked_by(kingdom, "king-b", game_now()) is False
