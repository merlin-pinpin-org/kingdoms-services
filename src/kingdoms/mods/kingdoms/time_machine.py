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
from kingdoms.mods.kingdoms.game_clock import (
    format_offset,
    game_now,
    game_offset_seconds,
    parse_duration,
    set_game_offset,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.models import SeasonState

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

_SNAPSHOT_MOMENTS = ("cycle", "age", "end", "lords_day_end")
"""The key moments a snapshot can be tagged with (the rewind targets)."""


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
        """Return the game clock and the upcoming key moments.

        machine-temps-v2: the clock reads the GAME time (real now +
        the season's persisted ``clock_offset``) — the process cache
        re-syncs from the season here, so a restarted bot converges
        back to the test offset on the first panel read.
        """
        season = await self._kingdoms.current_season()
        if season is not None:
            set_game_offset(int(getattr(season, "clock_offset", 0) or 0))
        timestamp = now or game_now()
        if season is None:
            return {
                "season": None,
                "now": timestamp,
                "real_now": datetime.now(tz=UTC),
                "offset": game_offset_seconds(),
                "snapshot_count": len(await self.snapshots()),
            }
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
        age_display = next(
            (age.display_name for age in self._config.ages if age.key == season.current_age_key),
            season.current_age_key,
        )
        return {
            "season": season.id,
            "now": timestamp,
            "real_now": datetime.now(tz=UTC),
            "offset": int(getattr(season, "clock_offset", 0) or 0),
            "started_at": season.started_at,
            "cycle": season.current_cycle,
            "weeks": season.weeks,
            "age": season.current_age_key,
            "age_display": age_display,
            "phase": season.phase,
            "finished": season.finished,
            "frozen": season.frozen,
            "next_cycle_at": next_cycle,
            "next_age_at": next_age,
            "season_end_at": end_slot,
            "timezone": str(tz),
            "snapshot_count": len(await self.snapshots()),
        }

    # ------------------------------------------------------------------
    # Snapshots — the reversible side (Drasah's "vrai retour arrière")
    # ------------------------------------------------------------------
    async def create_snapshot(
        self, label: str, *, now: datetime | None = None, moment: str | None = None
    ) -> dict[str, Any]:
        """Snapshot the full season data under ``label`` (never wiped).

        ``moment`` tags the KEY moment the snapshot sits on
        (``cycle``, ``age``, ``end``, ``lords_day_end``): the
        one-click rewind buttons restore the latest snapshot of a
        given moment. Manual snapshots carry ``None``.
        """
        timestamp = now or game_now()
        snapshot: dict[str, Any] = {
            "_id": f"tm-{timestamp:%Y%m%d-%H%M%S-%f}-{_slugify(label)}",
            "kind": TIME_MACHINE_KIND,
            "label": label,
            "moment": moment,
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
        logger.info("kingdoms: time-machine snapshot %r saved (moment=%r)", label, moment)
        return snapshot

    async def snapshots(self, moment: str | None = None) -> list[dict[str, Any]]:
        """Return the time-machine snapshots, most recent first.

        ``moment`` filters to one key moment's snapshots (None lists
        every snapshot, tagged or not).
        """
        archived = [
            dict(doc)
            for doc in await self._find_all("find_season_archives")
            if doc.get("kind") == TIME_MACHINE_KIND
            and (moment is None or doc.get("moment") == moment)
        ]
        archived.sort(key=lambda doc: str(doc.get("created_at", "")), reverse=True)
        return archived

    async def restore(self, snapshot_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Restore a snapshot verbatim — a true, reversible rewind.

        The CURRENT state is snapshotted first (``avant restauration``):
        a restore is itself restorable, the time line is never lost.
        The restored season comes back FROZEN (Drasah's rule,
        2026-10-11): the real scheduler would otherwise re-advance the
        clock to now within one tick and undo the rewind — the admin
        resumes deliberately (the freeze toggle) when the test is done.
        """
        timestamp = now or game_now()
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
        restored_frozen = await self._set_frozen(True)
        # machine-temps-v2: the restored season carries the offset of
        # its day — a rewind is a TRUE return, clock included.
        restored_season = await self._kingdoms.current_season()
        if restored_season is not None:
            set_game_offset(int(getattr(restored_season, "clock_offset", 0) or 0))
        logger.info(
            "kingdoms: time-machine snapshot %r restored (frozen=%s)", snapshot_id, restored_frozen
        )
        return {"restored": snapshot_id, "label": snapshot.get("label"), "backup": backup["_id"]}

    async def restore_last(self, moment: str, *, now: datetime | None = None) -> dict[str, Any]:
        """One-click rewind to the latest snapshot of one key moment.

        ``moment`` is the tag ``create_snapshot`` stored: ``cycle``
        (the state right before the last cycle switch), ``age`` (the
        last age switch), ``lords_day_end`` (the Monday-10h window
        close), ``end`` (the season verdict).
        """
        if moment not in _SNAPSHOT_MOMENTS:
            raise TimeMachineError(f"unknown moment {moment!r}")
        candidates = await self.snapshots(moment)
        if not candidates:
            raise TimeMachineError(f"no snapshot tagged {moment!r} exists yet")
        return await self.restore(candidates[0]["_id"], now=now)

    # ------------------------------------------------------------------
    # Freeze — the pause (Drasah's "faire pause sur la partie")
    # ------------------------------------------------------------------
    async def freeze(self) -> bool:
        """Pause the game clock: the scheduler replays no slot."""
        return await self._set_frozen(True)

    async def unfreeze(self) -> bool:
        """Resume the game clock (the scheduler catches up on its next tick)."""
        return await self._set_frozen(False)

    async def _set_frozen(self, frozen: bool) -> bool:
        """Flip the season's frozen flag and persist it."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise TimeMachineError("no season is running")
        season.frozen = frozen
        await self._store.upsert_season(season.to_mongo())
        logger.info("kingdoms: game clock %s", "frozen" if frozen else "resumed")
        return frozen

    # ------------------------------------------------------------------
    # Back to setup — the fresh-start button (Drasah's test loop)
    # ------------------------------------------------------------------
    async def back_to_setup(self) -> dict[str, Any]:
        """Wipe the season data (NO archive) and relaunch in setup.

        Drasah's rules (2026-10-11): the players are sent back to the
        waiting queue (everything wiped, they re-apply via Postuler),
        the player kingdom salons are deleted by the caller, and the
        season ARCHIVE only happens at a real season end — this wipe
        archives nothing. The time-machine snapshots survive (they
        live in the archive collection, never wiped).
        """
        await self._store.wipe_season_data()
        season = await self._kingdoms.launch()
        set_game_offset(0)
        logger.info("kingdoms: time-machine back-to-setup (fresh season %s)", season.id)
        return {"season": season.id, "phase": season.phase}

    async def reset_clock(self) -> dict[str, Any]:
        """Reset the game clock back to the real time (machine-temps-v2).

        Snapshot the current state first (« avant reset horloge », the
        reset is reversible like every other mutation), drop the
        season's ``clock_offset`` back to 0 and freeze the season: a
        reset mid-test resumes PAUSED, deliberately (Drasah 14/10) —
        the scheduler would otherwise catch up on real time.
        """
        timestamp = game_now()
        season = await self._kingdoms.current_season()
        if season is None:
            raise TimeMachineError("no season is running")
        backup = await self.create_snapshot("avant reset horloge", now=timestamp)
        season.clock_offset = 0
        season.frozen = True
        await self._store.upsert_season(season.to_mongo())
        set_game_offset(0)
        logger.info("kingdoms: time-machine clock reset (backup %s)", backup["_id"])
        return {"backup": backup["_id"], "offset": 0, "frozen": True}

    # ------------------------------------------------------------------
    # Fast-forward — advance and jump (Drasah's "accélérer le temps")
    # ------------------------------------------------------------------
    async def advance(self, days: int, *, now: datetime | None = None) -> dict[str, Any]:
        """Fast-forward ``days`` days: snapshot, then replay the missed slots."""
        if days <= 0:
            raise TimeMachineError("the fast-forward needs a positive number of days")
        return await self.advance_seconds(days * 86400, now=now, label=f"avant +{days}j")

    async def advance_duration(
        self, text: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """Fast-forward a FREE duration (« 90m », « 2h », « 15m », « 3d »).

        machine-temps-v2 (Drasah 14/10): the admin types the jump
        length; the parser accepts minutes, hours and days, alone or
        combined (« 1d2h30m »). The shortcut buttons (+1/+3/+7 days)
        ride the same offset path.
        """
        try:
            seconds = parse_duration(text)
        except ValueError as exc:
            raise TimeMachineError(str(exc)) from exc
        return await self.advance_seconds(seconds, now=now, label=f"avant saut {text.strip()}")

    async def advance_seconds(
        self, seconds: int, *, now: datetime | None = None, label: str | None = None
    ) -> dict[str, Any]:
        """Advance the game clock by ``seconds``: snapshot, replay, offset.

        The game clock = real clock + ``clock_offset``: a jump of D
        seconds adds D to the persisted offset, replays every due slot
        up to the new game time (``run_due``) and elapses the marriage
        locks with the same delta. Negative jumps are refused — a
        rewind goes through the snapshots, never through the clock.
        """
        if seconds <= 0:
            raise TimeMachineError("the jump needs a strictly positive duration")
        timestamp = now or game_now()
        await self._require_started_season()
        target = timestamp + timedelta(seconds=seconds)
        return await self._advance_to(target, label or f"avant +{format_offset(seconds)}", timestamp)

    async def jump_to(self, moment: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Jump to the next key moment: cycle switch, age switch or season end."""
        if moment not in _MOMENTS:
            raise TimeMachineError(f"unknown moment {moment!r}")
        timestamp = now or game_now()
        season = await self._require_started_season()
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

    async def _require_started_season(self) -> SeasonState:
        """Refuse time travel while the game sits in its setup phase.

        Drasah's rule (2026-10-11): in ``setup`` no random content
        exists — the time buttons must answer "no season is running"
        instead of silently advancing a game that has not started.
        """
        season = await self._kingdoms.current_season()
        if season is None:
            raise TimeMachineError("no season is running")
        if season.phase == "setup":
            raise TimeMachineError("the game has not started yet (setup phase)")
        return season

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _advance_to(
        self, target: datetime, label: str, timestamp: datetime
    ) -> dict[str, Any]:
        """Snapshot the state, then replay every slot up to ``target``.

        Each replayed slot is preceded by its OWN key-moment snapshot
        (``run_due``'s snapshotter hook): a "+7 days" leaves tagged
        ``cycle``/``age`` snapshots behind, so the one-click rewind
        buttons land on the exact switch moments even after a manual
        fast-forward.
        """

        async def _slot_snapshotter(kind: str) -> None:
            await self.create_snapshot(f"auto avant {kind}", now=timestamp, moment=kind)

        backup = await self.create_snapshot(label, now=timestamp)
        # machine-temps-v2: the jump rides the offset — the persisted
        # ``clock_offset`` grows by the jump length, the process cache
        # follows, and every ``game_now()`` read shifts with it.
        delta = int((target - timestamp).total_seconds())
        season = await self._require_started_season()
        season.clock_offset = int(getattr(season, "clock_offset", 0) or 0) + delta
        await self._store.upsert_season(season.to_mongo())
        set_game_offset(int(season.clock_offset))
        reports = await self._events.run_due(target, snapshotter=_slot_snapshotter)
        # The locks live on the wall clock (D34) but the game just
        # jumped forward: elapse the honeymoons with the game clock so
        # a "+N days" also expires the marriage locks (Drasah's
        # 2026-10-12 livery test flow). Best effort, never blocks.
        try:
            await self._kingdoms._shift_marriage_locks(delta)
        except Exception:
            logger.warning("kingdoms: time-machine lock shift failed", exc_info=True)
        return {
            "backup": backup["_id"],
            "reports": reports,
            "target": target,
            "offset": int(season.clock_offset),
        }

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
