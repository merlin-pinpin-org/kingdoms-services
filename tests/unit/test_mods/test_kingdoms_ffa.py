"""Kingdoms FFA Exploration - unit tests (kingdoms-services#244, ffa-exploration-v1).

The Saturday FFA state machine behind an in-memory store: the
random-hour draw (deterministic by seed, restart-safe by persistence),
the enrollment window (announcement opens it, the « Aller explorer »
button toggles the slot), the 1-lord-per-kingdom admin parameter and
the participant ceiling, the Explorateur/no-show lockouts, and the D20
result ingestion — rewards through ``run_exploration`` plus the 72h
no-show combat lock.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from kingdoms.mods.kingdoms.attacks import AttackService, LordLockedError
from kingdoms.mods.kingdoms.config import Epoch, KingdomsSeasonConfig, default_map_catalog
from kingdoms.mods.kingdoms.events import EventService
from kingdoms.mods.kingdoms.ffa_exploration import (
    FFAExplorationService,
    FFAFullError,
    FFAKingdomFullError,
    FFANotOpenError,
)
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService

from .test_kingdoms_events import Bundle as _EventsBundle
from .test_kingdoms_events import MemoryStore as _EventsStore

PARIS = ZoneInfo("Europe/Paris")


class MemoryStore(_EventsStore):
    """The events store extended with the FFA collection."""

    def __init__(self) -> None:
        super().__init__()
        self.ffas: dict[str, dict] = {}

    async def upsert_ffa(self, document: dict) -> None:
        self.ffas[document["_id"]] = document

    async def find_ffas(self) -> list[dict]:
        return list(self.ffas.values())


class Bundle(_EventsBundle):
    """The events bundle extended with the FFA Exploration service."""

    def __init__(self, config: KingdomsSeasonConfig | None = None) -> None:
        # The full map catalog: the board draw plus the winner's FFA map
        # both consume the pool, unlike the exhaustion-tuned events one.
        super().__init__(config or _full_config())
        # Rebuild over the FFA-carrying store.
        self.store = MemoryStore()
        self.kingdoms = KingdomsService(self.store, self.config)  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]
        self.attacks = AttackService(self.store, self.config, self.kingdoms, self.territories)  # type: ignore[arg-type]
        self.events = EventService(self.store, self.config, self.kingdoms, self.territories, self.attacks)  # type: ignore[arg-type]
        self.ffa = FFAExplorationService(self.store, self.config, self.kingdoms, self.events)

    async def lord(self, lord_id: str) -> Any:
        return next(item for item in await self.kingdoms.lords() if item.id == lord_id)

    async def start(self) -> None:
        """Launch, draw the board, then flip the season to ``started``."""
        await self.launch_season()
        await self.draw(1)
        await self.kingdoms.start_season()


def _full_config() -> KingdomsSeasonConfig:
    """A full-catalog config: board draw AND winner map never exhaust it."""
    return KingdomsSeasonConfig(
        maps=default_map_catalog(),
        ages=(
            Epoch(key="dark_age", display_name="Âge sombre", gaia_ai_level=2, tech_points=0, extra_marriages=1),
            Epoch(key="feudal_age", display_name="Âge féodal", gaia_ai_level=3, tech_points=1, extra_marriages=1),
            Epoch(key="castle_age", display_name="Âge des châteaux", gaia_ai_level=5, tech_points=2, extra_marriages=1),
            Epoch(key="imperial_age", display_name="Âge impérial", gaia_ai_level=5, tech_points=2, extra_marriages=1),
        ),
    )


def _saturday(hour_utc: int, day: str = "2026-01-17") -> datetime:
    """One pinned Saturday instant (2026-01-17 IS a Saturday)."""
    return datetime.fromisoformat(f"{day}T{hour_utc:02d}:00+00:00")


async def test_draw_is_deterministic_and_restart_safe() -> None:
    """The drawn hour follows the seed; a redraw reads the same doc back."""
    bundle = Bundle()
    await bundle.start()
    now = _saturday(7)
    ffa = await bundle.ffa.draw_or_get(now, seed=3)
    assert ffa is not None
    expected_hour = random.Random(3).choice(  # noqa: S311 - game draw, not crypto
        bundle.config.events.ffa_start_hours
    )
    assert ffa.start_at.astimezone(PARIS).hour == expected_hour
    assert ffa.announce_at == ffa.start_at - timedelta(hours=2)
    assert ffa.one_lord_per_kingdom is True
    # Restart-safe: the doc persists, a new tick reads it back untouched.
    again = await bundle.ffa.draw_or_get(now, seed=9)
    assert again is not None
    assert again.start_at == ffa.start_at
    assert len(bundle.store.ffas) == 1


async def test_enrollment_opens_with_the_announcement_and_toggles() -> None:
    """Before the announcement: refused. After: join, then leave, then rejoin."""
    bundle = Bundle()
    await bundle.start()
    now = _saturday(7)
    ffa = await bundle.ffa.draw_or_get(now, seed=1)
    assert ffa is not None
    with pytest.raises(FFANotOpenError):
        await bundle.ffa.enroll("king-a", now=now)
    await bundle.ffa.mark_announced(ffa)
    inside = ffa.start_at - timedelta(minutes=30)
    state, doc = await bundle.ffa.enroll("king-a", now=inside)
    assert state == "joined" and doc.participants == ["king-a"]
    # A second click releases the slot (the withdrawal toggle).
    state, doc = await bundle.ffa.enroll("king-a", now=inside)
    assert state == "left" and doc.participants == []
    state, doc = await bundle.ffa.enroll("king-a", now=inside)
    assert state == "joined" and doc.participants == ["king-a"]
    # Once started the enrollments close.
    await bundle.ffa.start_due(now=ffa.start_at + timedelta(minutes=1))
    with pytest.raises(FFANotOpenError):
        await bundle.ffa.enroll("lord-b", now=ffa.start_at + timedelta(minutes=2))


async def test_one_lord_per_kingdom_and_free_mode() -> None:
    """Default mode: one slot per kingdom; ``libre`` lifts the rule."""
    bundle = Bundle()
    await bundle.start()
    ffa = await bundle.ffa.draw_or_get(_saturday(7), seed=1)
    assert ffa is not None
    await bundle.ffa.mark_announced(ffa)
    inside = ffa.start_at - timedelta(minutes=30)
    await bundle.ffa.enroll("king-a", now=inside)
    with pytest.raises(FFAKingdomFullError):
        await bundle.ffa.enroll("lord-a", now=inside)
    # A lord of another kingdom passes.
    state, _ = await bundle.ffa.enroll("king-b", now=inside)
    assert state == "joined"
    # Free mode: the admin flips the config between two Saturdays.
    bundle.config.events.ffa_one_lord_per_kingdom = False
    free = await bundle.ffa.draw_or_get(_saturday(7, day="2026-01-24"), seed=1)
    assert free is not None
    assert free.one_lord_per_kingdom is False
    await bundle.ffa.mark_announced(free)
    free_inside = free.start_at - timedelta(minutes=30)
    await bundle.ffa.enroll("king-a", now=free_inside)
    state, _ = await bundle.ffa.enroll("lord-a", now=free_inside)
    assert state == "joined"


async def test_participant_ceiling_closes_the_ffa() -> None:
    """The Gaïa free-for-all cap (here 2) caps the FFA slots too."""
    bundle = Bundle()
    await bundle.start()
    bundle.config.events.ffa_one_lord_per_kingdom = False
    bundle.config.attacks.gaia_max_participants = 2
    ffa = await bundle.ffa.draw_or_get(_saturday(7), seed=1)
    assert ffa is not None
    await bundle.ffa.mark_announced(ffa)
    inside = ffa.start_at - timedelta(minutes=30)
    await bundle.ffa.enroll("king-a", now=inside)
    await bundle.ffa.enroll("lord-a", now=inside)
    with pytest.raises(FFAFullError):
        await bundle.ffa.enroll("king-b", now=inside)


async def test_exploration_lock_forbids_enrollment() -> None:
    """marche-v2 art. 6: an exploring lord cannot take an FFA slot."""
    bundle = Bundle()
    await bundle.start()
    ffa = await bundle.ffa.draw_or_get(_saturday(7), seed=1)
    assert ffa is not None
    await bundle.ffa.mark_announced(ffa)
    inside = ffa.start_at - timedelta(minutes=30)
    lord = await bundle.lord("king-a")
    lord.exploration_locked_until = int((inside + timedelta(hours=12)).timestamp())
    await bundle.store.upsert_lord(lord.to_mongo())
    with pytest.raises(LordLockedError):
        await bundle.ffa.enroll("king-a", now=inside)


async def test_no_show_locks_combat_from_the_next_cycle() -> None:
    """A participant absent from the ranking is locked 72h from the switch."""
    bundle = Bundle()
    await bundle.start()
    bundle.config.events.ffa_one_lord_per_kingdom = False
    ffa = await bundle.ffa.draw_or_get(_saturday(7), seed=1)
    assert ffa is not None
    await bundle.ffa.mark_announced(ffa)
    inside = ffa.start_at - timedelta(minutes=30)
    await bundle.ffa.enroll("king-a", now=inside)
    await bundle.ffa.enroll("lord-b", now=inside)
    # The game runs 1h15; king-a shows up, lord-b does not.
    after = ffa.start_at + timedelta(minutes=80)
    resolved = await bundle.ffa.ingest_result(["king-a"], now=after)
    assert resolved.finished is True
    assert resolved.no_shows == ["lord-b"]
    aquitaine = await bundle.kingdom_id("Aquitaine")
    # Next cycle switch after the Saturday: Sunday 23:30 Paris = 22:30 UTC.
    next_switch = datetime(2026, 1, 18, 22, 30, tzinfo=UTC)
    expected_lock = int((next_switch + timedelta(hours=72)).timestamp())
    lord_b = await bundle.lord("lord-b")
    assert lord_b.ffa_locked_until == expected_lock
    # The no-show is locked out of every combat lane.
    bourgogne = await bundle.kingdom_id("Bourgogne")
    aquitaine_map = (await bundle.owner_maps(aquitaine))[0]
    with pytest.raises(LordLockedError):
        await bundle.attacks.declare_attack("lord-b", aquitaine_map, "aoe2de://x", now=after)
    bourgogne_map = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", bourgogne_map, "aoe2de://x", now=after)
    # respond_defense reads the REAL clock: pin the deadline INSIDE the
    # [declaration, T-15] window and a still-live lock so only the no-show
    # rule refuses (Drasah 18/10).
    attack_doc = attack.to_mongo()
    attack_doc["expires_at"] = datetime.now(tz=UTC) + timedelta(minutes=30)
    await bundle.store.upsert_attack(attack_doc)
    lord_b.ffa_locked_until = int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp())
    await bundle.store.upsert_lord(lord_b.to_mongo())
    with pytest.raises(LordLockedError):
        await bundle.attacks.respond_defense(attack.id, "lord-b")
    gaia_maps = [
        t.map_key
        for t in await bundle.territories.territories()
        if t.owner_kingdom_id not in (aquitaine, bourgogne)
    ]
    with pytest.raises(LordLockedError):
        await bundle.attacks.declare_gaia_attack("lord-b", gaia_maps[0], "aoe2de://x", now=after)


async def test_rewards_flow_and_replay_is_idempotent() -> None:
    """The kingdom ranking feeds run_exploration; replaying changes nothing."""
    bundle = Bundle()
    await bundle.start()
    bundle.config.events.ffa_one_lord_per_kingdom = False
    ffa = await bundle.ffa.draw_or_get(_saturday(7), seed=1)
    assert ffa is not None
    await bundle.ffa.mark_announced(ffa)
    inside = ffa.start_at - timedelta(minutes=30)
    await bundle.ffa.enroll("king-a", now=inside)
    await bundle.ffa.enroll("lord-b", now=inside)
    # Both show up: king-a first, lord-b second → kingdoms A then B.
    after = ffa.start_at + timedelta(minutes=80)
    resolved = await bundle.ffa.ingest_result(["king-a", "lord-b"], now=after)
    assert resolved.finished is True and resolved.no_shows == []
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    kingdoms = {k.id: k for k in await bundle.kingdoms.kingdoms()}
    bank_a = kingdoms[aquitaine].tech_points_bank
    bank_b = kingdoms[bourgogne].tech_points_bank
    settings = bundle.config.events
    assert bank_a == settings.exploration_first_tech
    assert bank_b == settings.exploration_second_tech
    # The winner's drawn map exists as a fresh territory.
    assert any(
        t.owner_kingdom_id == aquitaine for t in await bundle.territories.territories()
    )
    # Replaying the contract's ranking is a no-op.
    again = await bundle.ffa.ingest_result(["king-a", "lord-b"], now=after)
    assert again.finished is True
    kingdoms2 = {k.id: k for k in await bundle.kingdoms.kingdoms()}
    assert kingdoms2[aquitaine].tech_points_bank == bank_a
    assert kingdoms2[bourgogne].tech_points_bank == bank_b
