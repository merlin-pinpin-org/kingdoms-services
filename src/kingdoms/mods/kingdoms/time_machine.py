"""Kingdoms time machine — Drasah's test panel over the game clock (chantier E).

The game runs on the real calendar (the season's ``started_at`` anchors
every weekly slot), which makes testing slow: a full season takes four
real weeks. This module is the *accelerator* Drasah asked for
(2026-10-11): the admin sees the game clock, fast-forwards it (+1/+3/+7
days or straight to the next key moment), and — because a fast-forward
mutates the real state — every jump is REVERSIBLE through snapshots.

Design decisions (Drasah's arbitration):
- true rewind WITH snapshots: every jump auto-snapshots the full
  season data first, and any snapshot can be restored verbatim;
- the snapshots reuse the season-archive collection (never wiped by a
  reset) with ``kind: "time_machine"`` — a restore survives even a
  bad reset;
- forward simulation replays the real orchestrator (``run_due``) at a
  future date — the state advances exactly the way the real calendar
  would have done it, announced the same way.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from kingdoms.mods.kingdoms.events import _schedule_timezone, cron_slots_between

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.events import EventService
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.time_machine")

TIME_MACHINE_KIND = "time_machine"
"""The archive discriminator: a jump snapshot, not a season-end archive."""

END_SLOT_OFFSET = timedelta(minutes=29)
"""Sunday 23:30 → the 23:59 verdict (Drasah's endcycle rule)."""

_HORIZON = timedelta(days=60)
"""How far ahead the slot lookups search (a season never spans this)."""

_MOMENTS = ("next_cycle", "next_age", "season_end")


def _slugify(label: str) -> str:
    """Normalize a snapshot label into an id-safe slug."""
    text = re.sub(r"[^a-z0-9]+", "-", label.lower().strip(), flags=re.ASCII)
    return text.strip("-")[:40] or "snapshot"


class TimeMachineError(Exception):
    """Raised when a time-machine action is impossible (bad moment, unknown snapshot)."""

    code = "KINGDOMS_TIME_MACHINE"
    message_key = "kingdoms.errors.time_machine"


class TimeMachineService:
    """The game clock: status, reversible fast-forward, snapshot restore."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
        events_service: EventService,
    ) -> None:
        """Store the seams: persistence, config, season and events services."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._events = events_service

    # ------------------------------------------------------------------
    # Status — the game clock (Drasah's "voir le temps de jeu")
    # ------------------------------------------------------------------
    async def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Return the game clock and the upcoming key moments."""
        timestamp = now or datetime.now(tz=UTC)
        season = await self._kingdoms.current_season()
        if season is None:
            return {"season": None, "now": timestamp, "snapshot_count": len(await self.snapshots())}
        tz = _schedule_timezone(self._config)
        cycle_slots = cron_slots_between(
            self._config.events.cycle_cron, season.started_at, timestamp + _HORIZON, tz
        )
        next_cycle = next((slot for slot in cycle_slots if slot > timestamp), None)
        end_slot = (
            cycle_slots[self._config.weeks - 1] + END_SLOT_OFFSET
            if len(cycle_slots) >= self._config.weeks
            else None
        )
        if end_slot is not None and end_slot <= timestamp:
            end_slot = None
        age_slots = cron_slots_between(
            self._config.events.age_cron, season.started_at, timestamp + _HORIZON, tz
        )
        next_age = next((slot for slot in age_slots if slot > timestamp), None)
        return {
            "season": season.id,
            "now": timestamp,
            "started_at": season.started_at,
            "cycle": season.current_cycle,
            "weeks": season.weeks,
            "age": season.current_age_key,
            "phase": season.phase,
            "finished": season.finished,
            "next_cycle_at": next_cycle,
            "next_age_at": next_age,
            "season_end_at": end_slot,
            "snapshot_count": len(await self.snapshots()),
        }

    # ------------------------------------------------------------------
    # Snapshots — the reversible side (Drasah's "vrai retour arrière")
    # ------------------------------------------------------------------
    async def create_snapshot(self, label: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Snapshot the full season data under ``label`` (never wiped)."""
        timestamp = now or datetime.now(tz=UTC)
        snapshot: dict[str, Any] = {
            "_id": f"tm-{timestamp:%Y%m%d-%H%M%S-%f}-{_slugify(label)}",
            "kind": TIME_MACHINE_KIND,
            "label": label,
            "created_at": timestamp,
            "seasons": await self._find_all("find_seasons"),
            "kingdoms": await self._find_all("find_kingdoms"),
            "lords": await self._find_all("find_lords"),
            "territories": await self._find_all("find_territories"),
            "technologies": await self._find_all("find_technologies"),
            "attacks": await self._find_all("find_attacks"),
            "showmatches": await self._find_all("find_showmatches"),
        }
        await self._store.upsert_season_archive(snapshot)
        logger.info("kingdoms: time-machine snapshot %r saved", label)
        return snapshot

    async def snapshots(self) -> list[dict[str, Any]]:
        """Return the time-machine snapshots, most recent first."""
        archived = [
            dict(doc)
            for doc in await self._find_all("find_season_archives")
            if doc.get("kind") == TIME_MACHINE_KIND
        ]
        archived.sort(key=lambda doc: str(doc.get("created_at", "")), reverse=True)
        return archived

    async def restore(self, snapshot_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Restore a snapshot verbatim — a true, reversible rewind.

        The CURRENT state is snapshotted first (``avant restauration``):
        a restore is itself restorable, the time line is never lost.
        """
        timestamp = now or datetime.now(tz=UTC)
        snapshot = next(
            (doc for doc in await self.snapshots() if doc.get("_id") == snapshot_id), None
        )
        if snapshot is None:
            raise TimeMachineError("no time-machine snapshot with this id")
        backup = await self.create_snapshot("avant restauration", now=timestamp)
        await self._store.wipe_season_data()
        for document in snapshot.get("seasons", []):
            await self._store.upsert_season(document)
        for document in snapshot.get("kingdoms", []):
            await self._store.upsert_kingdom(document)
        for document in snapshot.get("lords", []):
            await self._store.upsert_lord(document)
        for document in snapshot.get("territories", []):
            await self._store.upsert_territory(document)
        for document in snapshot.get("technologies", []):
            await self._store.upsert_technology(document)
        for document in snapshot.get("attacks", []):
            await self._store.upsert_attack(document)
        for document in snapshot.get("showmatches", []):
            await self._store.upsert_showmatch(document)
        logger.info("kingdoms: time-machine snapshot %r restored", snapshot_id)
        return {"restored": snapshot_id, "label": snapshot.get("label"), "backup": backup["_id"]}

    # ------------------------------------------------------------------
    # Fast-forward — advance and jump (Drasah's "accélérer le temps")
    # ------------------------------------------------------------------
    async def advance(self, days: int, *, now: datetime | None = None) -> dict[str, Any]:
        """Fast-forward ``days`` days: snapshot, then replay the missed slots."""
        if days <= 0:
            raise TimeMachineError("the fast-forward needs a positive number of days")
        timestamp = now or datetime.now(tz=UTC)
        return await self._advance_to(timestamp + timedelta(days=days), f"avant +{days}j", timestamp)

    async def jump_to(self, moment: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Jump to the next key moment: cycle switch, age switch or season end."""
        if moment not in _MOMENTS:
            raise TimeMachineError(f"unknown moment {moment!r}")
        timestamp = now or datetime.now(tz=UTC)
        season = await self._kingdoms.current_season()
        if season is None:
            raise TimeMachineError("no season is running")
        labels = {
            "next_cycle": "avant saut cycle",
            "next_age": "avant saut âge",
            "season_end": "avant fin de saison",
        }
        if moment == "next_age":
            target = self._next_slot(
                self._config.events.age_cron, season.started_at, timestamp
            )
        elif moment == "season_end":
            target = self._season_end_slot(season.started_at, timestamp)
        else:
            target = self._next_slot(
                self._config.events.cycle_cron, season.started_at, timestamp
            )
        if target is None or target <= timestamp:
            raise TimeMachineError("this key moment already passed (or sits too far)")
        return await self._advance_to(target, labels[moment], timestamp)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _advance_to(
        self, target: datetime, label: str, timestamp: datetime
    ) -> dict[str, Any]:
        """Snapshot the state, then replay every slot up to ``target``."""
        backup = await self.create_snapshot(label, now=timestamp)
        reports = await self._events.run_due(target)
        return {"backup": backup["_id"], "reports": reports, "target": target}

    def _next_slot(self, cron: str, started_at: datetime, timestamp: datetime) -> datetime | None:
        """Return the first cron slot strictly after ``timestamp``."""
        tz = _schedule_timezone(self._config)
        slots = cron_slots_between(cron, started_at, timestamp + _HORIZON, tz)
        return next((slot for slot in slots if slot > timestamp), None)

    def _season_end_slot(self, started_at: datetime, timestamp: datetime) -> datetime | None:
        """Return the season-end verdict moment (the last slot + 29 min)."""
        tz = _schedule_timezone(self._config)
        slots = cron_slots_between(
            self._config.events.cycle_cron, started_at, timestamp + _HORIZON, tz
        )
        if len(slots) < self._config.weeks:
            return None
        return slots[self._config.weeks - 1] + END_SLOT_OFFSET

    async def _find_all(self, finder: str) -> list[dict[str, Any]]:
        """Read a store collection defensively (optional on minimal stores)."""
        method = getattr(self._store, finder, None)
        if method is None:
            return []
        try:
            return list(await method())
        except Exception:
            logger.warning("kingdoms: time-machine read %s failed", finder, exc_info=True)
            return []
