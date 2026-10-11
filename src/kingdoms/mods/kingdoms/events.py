"""Kingdoms mod weekly events service (kingdoms-services#159, T5).

Reference §15-§17 and decisions D1/D4/D18/D29/D30/D31/D44/D51: the
event orchestrator - the cycle switch with the Lord's Day (new Gaia
maps), the age switch (Wednesday midnight) distributing the epoch
bonuses, and the Saturday exploration FFA. The schedule is recomputed
from the season start, so a restart never loses an event: ``run_due``
replays every missed slot from the season state alone.

The cadastre **effects** application stays a later concern (issue
#163 scope note); this slice owns the event triggers and the map and
tech-point economics they carry.
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from kingdoms.mods.kingdoms.territories import (
    MapPoolExhaustedError,
    TerritoryService,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.attacks import AttackService
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.diplomacy import DiplomacyService
    from kingdoms.mods.kingdoms.models import KingdomModel, SeasonState
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.events")

WEEK = timedelta(weeks=1)

_CRON_DAYS: dict[str, int] = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}


def _parse_weekly_cron(cron: str) -> tuple[int, int, int]:
    """Parse a weekly ``m h * * DAY`` cron into (minute, hour, weekday).

    Only the weekly shape is supported (the Season II schedules are
    weekly): anything else is a configuration error and fails loudly
    rather than silently skipping the events.
    """
    parts = cron.split()
    if len(parts) != 5 or parts[2] != "*" or parts[3] != "*":
        raise ValueError(f"only weekly crons (m h * * DAY) are supported: {cron!r}")
    day = _CRON_DAYS.get(parts[4].upper())
    if day is None or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"invalid weekly cron: {cron!r}")
    minute, hour = int(parts[0]), int(parts[1])
    if minute > 59 or hour > 23:
        raise ValueError(f"invalid weekly cron time: {cron!r}")
    return minute, hour, day


def _schedule_timezone(config: KingdomsSeasonConfig) -> ZoneInfo:
    """Resolve the scheduling timezone, UTC as the safe fallback."""
    try:
        return ZoneInfo(config.events.schedule_timezone)
    except Exception:
        logger.warning(
            "kingdoms: unknown schedule timezone %r - falling back to UTC",
            config.events.schedule_timezone,
        )
        return ZoneInfo("UTC")


def cron_slots_between(
    cron: str,
    after: datetime,
    before: datetime,
    tz: ZoneInfo,
) -> list[datetime]:
    """Return every UTC slot of the weekly cron in ``(after, before]``.

    Drasah's cadence rule (2026-10-11): the first switch lands on the
    FIRST calendar occurrence of the cron day after the season start —
    a season started Saturday gets its first cycle the very next
    Sunday 23:30, no full week of dead time.
    """
    minute, hour, weekday = _parse_weekly_cron(cron)
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    if before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    slots: list[datetime] = []
    day = after.astimezone(tz).date()
    last = before.astimezone(tz).date()
    while day <= last:
        if day.weekday() == weekday:
            slot = datetime.combine(day, time(hour, minute), tzinfo=tz).astimezone(UTC)
            if after < slot <= before:
                slots.append(slot)
        day += timedelta(days=1)
    return slots


def _cycle_slot_offset(config: KingdomsSeasonConfig) -> timedelta:
    """Return the cycle slot's offset within its day (23h30 by default)."""
    minute, hour, _weekday = _parse_weekly_cron(config.events.cycle_cron)
    return timedelta(hours=hour, minutes=minute)


def lords_day_window(
    config: KingdomsSeasonConfig,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Return the active Lord's Day window around ``now``, if any.

    The window opens at the weekly cycle slot (Sunday 23:30 by default,
    ``events.cycle_cron``) and closes the NEXT day at
    ``events.lords_day_end_hour`` (Monday 10:00) in the schedule
    timezone. Inside the window an attack cannot be PLAYED: the attack
    service uses this helper to block executions while still accepting
    a declaration whose execution lands after the window (Drasah's
    attack-vs-declaration distinction).
    """
    tz = _schedule_timezone(config)
    minute, hour, weekday = _parse_weekly_cron(config.events.cycle_cron)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local = now.astimezone(tz)
    for back in range(7):
        day = local.date() - timedelta(days=back)
        if day.weekday() != weekday:
            continue
        start = datetime.combine(day, time(hour, minute), tzinfo=tz).astimezone(UTC)
        if start > now:
            continue
        end = datetime.combine(
            day + timedelta(days=1), time(config.events.lords_day_end_hour, 0), tzinfo=tz
        ).astimezone(UTC)
        return (start, end) if now < end else None
    return None


def active_lords_day_window(
    config: KingdomsSeasonConfig,
    season: SeasonState,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Return the calendar Lord's Day window, suppressed on the end slot.

    Drasah's endcycle rule (2026-10-11): the endcycle's own Sunday is a
    NORMAL switch (budgets, maps, alliances), but the season's LAST
    Sunday carries NO Lord's Day window — the verdict applies at 23:59
    instead. A finished season never reopens a calendar window either.
    The forced test window is handled separately by the attack service.
    """
    if season.finished:
        return None
    window = lords_day_window(config, now)
    if window is None:
        return None
    tz = _schedule_timezone(config)
    # The window's opening slot is the last cycle slot before ``now``
    # (the next one sits a week later, past the window's end); when the
    # season already consumed its ``weeks`` slots the opening slot was
    # the END one — no window before the 23:59 verdict.
    slots = cron_slots_between(config.events.cycle_cron, season.started_at, now, tz)
    if len(slots) >= config.weeks:
        return None
    return window


class SeasonExhaustedError(MapPoolExhaustedError):
    """Raised when Gaia cannot receive the Lord's Day maps (D31).

    The season stops automatically: the caller announces the end and
    hands over to the closing report (T8).
    """

    code = "KINGDOMS_SEASON_EXHAUSTED"
    message_key = "kingdoms.errors.season_exhausted"


class EventService:
    """The weekly event orchestrator (reference §15-§17)."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
        territory_service: TerritoryService,
        attacks_service: AttackService,
        diplomacy_service: DiplomacyService | None = None,
    ) -> None:
        """Store the seams of the orchestrated services.

        ``diplomacy_service`` powers the Lord's Day alliance
        recomputation (D51): every cycle switch recomputes the playable
        civilizations from the territories and marriages. It stays
        optional so older wirings (and the tests) keep working.
        """
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._territories = territory_service
        self._attacks = attacks_service
        self._diplomacy = diplomacy_service

    # ------------------------------------------------------------------
    # Cycle switch + Lord's Day (§16, D1/D31)
    # ------------------------------------------------------------------
    async def run_cycle_switch(self, *, seed: int | None = None) -> dict[str, object]:
        """Run the Sunday 23:30 switch (D1) and the Lord's Day.

        Recharges every weekly budget, then draws ``lords_day_new_maps``
        non-out maps for Gaia. Raises SeasonExhaustedError (D31) when
        the pool cannot cover the draw - the season then ends
        automatically. Returns the cycle report for the announcement.
        """
        season = await self._require_season()
        recharged = await self._attacks.recharge_weekly_budgets()
        season.current_cycle += 1
        await self._store.upsert_season(season.to_mongo())
        gaia = next((k for k in await self._kingdoms.kingdoms() if k.is_gaia), None)
        added: list[str] = []
        if gaia is not None and self._config.events.lords_day_new_maps > 0:
            added = await self._draw_gaia_maps(
                gaia.id, self._config.events.lords_day_new_maps, seed=seed
            )
        logger.info(
            "kingdoms: cycle %s switched - %s budgets recharged, %s Gaia maps added",
            season.current_cycle,
            recharged,
            len(added),
        )
        alliances = False
        if self._diplomacy is not None:
            try:
                await self._diplomacy.recalculate()
                alliances = True
            except Exception:
                logger.warning("kingdoms: alliance recomputation failed", exc_info=True)
        return {
            "cycle": season.current_cycle,
            "recharged_budgets": recharged,
            "gaia_new_maps": added,
            "gaia_kingdom_id": gaia.id if gaia is not None else None,
            "alliances_recomputed": alliances,
        }

    # ------------------------------------------------------------------
    # Age switch (§15, D18/D51)
    # ------------------------------------------------------------------
    async def run_age_switch(self) -> dict[str, object]:
        """Advance to the next age and distribute the epoch bonuses.

        Gaia's AI level follows the epoch scale (D18), each kingdom
        gains the epoch tech points, and the marriage capacity grows by
        the epoch's extra marriages (D53). The last age is a no-op.
        """
        season = await self._require_season()
        keys = [age.key for age in self._config.ages]
        if season.current_age_key not in keys:
            raise ValueError(f"unknown age key in season: {season.current_age_key}")
        index = keys.index(season.current_age_key)
        if index + 1 >= len(keys):
            logger.info("kingdoms: the season already sits in its last age")
            return {"age": season.current_age_key, "tech_points": 0, "extra_marriages": 0}
        epoch = self._config.ages[index + 1]
        season.current_age_key = epoch.key
        await self._store.upsert_season(season.to_mongo())
        for kingdom in await self._kingdoms.kingdoms():
            if kingdom.is_gaia:
                continue
            kingdom.tech_points_bank += epoch.tech_points
            kingdom.marriage_capacity += epoch.extra_marriages
            await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info(
            "kingdoms: age switched to %s (+%s tech, +%s marriages)",
            epoch.display_name,
            epoch.tech_points,
            epoch.extra_marriages,
        )
        return {
            "age": epoch.key,
            "display_name": epoch.display_name,
            "gaia_ai_level": epoch.gaia_ai_level,
            "tech_points": epoch.tech_points,
            "extra_marriages": epoch.extra_marriages,
        }

    async def rollback_age(self) -> dict[str, object]:
        """Undo the last age switch (Drasah's manual test seam).

        Moves the age pointer back one epoch and removes the bonuses
        the epoch granted (tech points, marriage capacity). Points
        already SPENT on technologies cannot be reclaimed — the bank
        simply floors at zero. The first age cannot be rolled back
        further (ValueError).
        """
        season = await self._require_season()
        keys = [age.key for age in self._config.ages]
        if season.current_age_key not in keys:
            raise ValueError(f"unknown age key in season: {season.current_age_key}")
        index = keys.index(season.current_age_key)
        if index == 0:
            raise ValueError("the season already sits in its first age")
        granted = self._config.ages[index]
        previous = self._config.ages[index - 1]
        season.current_age_key = previous.key
        await self._store.upsert_season(season.to_mongo())
        for kingdom in await self._kingdoms.kingdoms():
            if kingdom.is_gaia:
                continue
            kingdom.tech_points_bank = max(0, kingdom.tech_points_bank - granted.tech_points)
            kingdom.marriage_capacity = max(
                0, kingdom.marriage_capacity - granted.extra_marriages
            )
            await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info(
            "kingdoms: age rolled back to %s (-%s tech, -%s marriages)",
            previous.display_name,
            granted.tech_points,
            granted.extra_marriages,
        )
        return {
            "age": previous.key,
            "display_name": previous.display_name,
            "tech_points": -granted.tech_points,
            "extra_marriages": -granted.extra_marriages,
        }

    # ------------------------------------------------------------------
    # Manual Lord's Day phase (Drasah's test seam, 2026-10-11)
    # ------------------------------------------------------------------
    async def enter_lords_day_phase(
        self, *, now: datetime | None = None, seed: int | None = None
    ) -> dict[str, object]:
        """Force the Lord's Day phase open and run its cycle switch.

        Drasah's manual test seam on the Paramètres panel: entering
        the phase runs the FULL Sunday switch (budgets recharged, Gaïa
        maps drawn, alliances recomputed) and forces the attack window
        open with the same shape as the real one (cycle slot → the
        next day at ``lords_day_end_hour``, 10.5h by default). The
        window stays open until ``exit_lords_day_phase`` closes it or
        the forced range ends on its own.
        """
        timestamp = now or datetime.now(tz=UTC)
        report: dict[str, object] = dict(await self.run_cycle_switch(seed=seed))
        season = await self._require_season()
        duration = timedelta(days=1) - _cycle_slot_offset(self._config) + timedelta(
            hours=self._config.events.lords_day_end_hour
        )
        season.lords_day_forced_start = timestamp
        season.lords_day_forced_end = timestamp + duration
        await self._store.upsert_season(season.to_mongo())
        report["lords_day_forced"] = True
        report["lords_day_forced_until"] = season.lords_day_forced_end
        logger.info(
            "kingdoms: manual Lord's Day phase opened until %s",
            season.lords_day_forced_end.isoformat(),
        )
        return report

    async def exit_lords_day_phase(self) -> None:
        """Close the manually forced Lord's Day window."""
        season = await self._require_season()
        season.lords_day_forced_start = None
        season.lords_day_forced_end = None
        await self._store.upsert_season(season.to_mongo())
        logger.info("kingdoms: manual Lord's Day phase closed")

    # ------------------------------------------------------------------
    # Exploration (§17, D29/D30/D44)
    # ------------------------------------------------------------------
    async def run_exploration(
        self,
        ranking: list[str],
        *,
        seed: int | None = None,
    ) -> dict[str, object]:
        """Run the Saturday exploration FFA (optional, 1 lord/kingdom).

        ``ranking`` holds the kingdom ids best-first (the game result
        comes through the game contract, D20). Rewards: the first
        kingdom wins the drawn map as a territory plus one tech point,
        the second three, the third two, the others one; a tie at the
        top rewards both sides (D29); nobody leaves the map to Gaia
        and a single participant takes the lot unplayed (D30).
        """
        season = await self._require_season()
        drawn = await self._draw_free_map(seed=seed)
        rewards: dict[str, int] = {}
        settings = self._config.events
        for kingdom_id in ranking:
            rewards[kingdom_id] = settings.exploration_other_tech
        if len(ranking) >= 3:
            rewards[ranking[0]] = settings.exploration_first_tech
            rewards[ranking[1]] = settings.exploration_second_tech
            rewards[ranking[2]] = settings.exploration_third_tech
        elif len(ranking) == 2:
            rewards[ranking[0]] = settings.exploration_first_tech
            rewards[ranking[1]] = settings.exploration_second_tech
        elif len(ranking) == 1:
            rewards[ranking[0]] = settings.exploration_first_tech
        winner_ids: list[str] = ranking[:1]
        if not ranking:
            # D30: no participant - the map goes to Gaia.
            gaia = next((k for k in await self._kingdoms.kingdoms() if k.is_gaia), None)
            if gaia is not None:
                await self._territories.draw_map_for(gaia.id, drawn)
                logger.info("kingdoms: exploration unplayed - %s goes to Gaia", drawn)
            return {"map": drawn, "rewards": rewards, "winners": [gaia.id] if gaia else []}
        for kingdom_id in ranking:
            kingdom = await self._kingdom_by_id(kingdom_id)
            kingdom.tech_points_bank += rewards.get(kingdom_id, 0)
            await self._store.upsert_kingdom(kingdom.to_mongo())
        for kingdom_id in winner_ids:
            await self._territories.draw_map_for(kingdom_id, drawn)
        logger.info("kingdoms: exploration on %s - rewards %s", drawn, rewards)
        return {"map": drawn, "rewards": rewards, "winners": winner_ids, "season_id": season.id}

    # ------------------------------------------------------------------
    # Restart-safe scheduling (D1)
    # ------------------------------------------------------------------
    async def run_due(self, now: datetime) -> list[dict[str, object]]:
        """Replay every event slot missed since the season start (D1).

        The cadence is CALENDAR-based (Drasah's rule, 2026-10-11): the
        season launch IS cycle 1 (startcycle), the first Sunday 23:30
        switch moves to cycle 2, and the first Wednesday midnight
        switches the first age. A restart never skips a switch: this
        call walks the missed slots and replays them in chronological
        order (a cycle and an age are interleaved the way the calendar
        ordered them).

        Drasah's endcycle rule (2026-10-11): the cycle slots up to the
        last-but-one are NORMAL switches; the season's LAST Sunday slot
        is the END slot — no Lord's Day window opens, and the verdict
        applies at 23:59 (the slot plus 29 minutes): the ongoing
        attacks are cancelled, the closing counts the territories and
        the phase moves to ``ended``. Nothing replays afterwards.
        """
        season = await self._require_season()
        tz = _schedule_timezone(self._config)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if season.finished:
            return []
        due = self._due_slots(season, now, tz)
        reports: list[dict[str, object]] = []
        for _slot, kind in due:
            if kind == "end":
                report: dict[str, object] = dict(await self._apply_season_end())
            elif kind == "cycle":
                report = dict(await self.run_cycle_switch())
            else:
                report = dict(await self.run_age_switch())
            report["kind"] = kind
            reports.append(report)
            if kind == "end":
                break  # the season is over — nothing replays after the verdict
        return reports

    def _due_slots(
        self, season: SeasonState, now: datetime, tz: ZoneInfo
    ) -> list[tuple[datetime, str]]:
        """Return every due slot between the season start and ``now``.

        The cycle slots up to the last-but-one are normal switches;
        the LAST one is the END slot whose verdict applies at 23:59
        (the slot plus 29 minutes). The age slots after the last age
        are consumed silently — the last age switch is a no-op.
        """
        cycle_slots = cron_slots_between(
            self._config.events.cycle_cron, season.started_at, now, tz
        )
        age_slots = cron_slots_between(
            self._config.events.age_cron, season.started_at, now, tz
        )
        keys = [age.key for age in self._config.ages]
        last_age = len(keys) - 1
        age_index = keys.index(season.current_age_key) if season.current_age_key in keys else None
        if age_index is None:
            logger.warning(
                "kingdoms: unknown age key %r - age catch-up disabled",
                season.current_age_key,
            )
        due: list[tuple[datetime, str]] = []
        for index, slot in enumerate(cycle_slots):
            if index < season.current_cycle - 1:
                continue  # the switch already ran
            if index < self._config.weeks - 1:
                due.append((slot, "cycle"))
            else:
                # The end slot: the verdict applies at 23:59.
                end_at = slot + timedelta(minutes=29)
                if now >= end_at:
                    due.append((end_at, "end"))
        if age_index is not None and age_index < last_age:
            due.extend((slot, "age") for slot in age_slots[age_index:])
        due.sort(key=lambda item: item[0])
        return due

    async def _apply_season_end(self) -> dict[str, object]:
        """Apply the endcycle verdict at 23:59 (Drasah's end rule).

        Every ongoing attack is cancelled first, then the closing
        counts the territories (ShowMatch on a tie) and the phase moves
        to ``ended`` — the season stays frozen until the next launch.
        """
        cancelled = await self._attacks.cancel_ongoing()
        season = await self._require_season()
        season.lords_day_forced_start = None
        season.lords_day_forced_end = None
        await self._store.upsert_season(season.to_mongo())
        from kingdoms.mods.kingdoms.season_end import SeasonEndService

        closer = SeasonEndService(self._store, self._config, self._kingdoms, self._territories)
        report = dict(await closer.close_season())
        await self._kingdoms.set_phase("ended")
        report["cancelled_attacks"] = len(cancelled)
        report["phase"] = "ended"
        logger.info(
            "kingdoms: season ended - %s ongoing attack(s) cancelled, winner %s",
            len(cancelled),
            report.get("winner_name"),
        )
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _draw_free_map(self, *, seed: int | None = None) -> str:
        """Draw one allowed map that is not out yet (§17)."""
        out = await self._territories.drawn_map_keys()
        catalog = [entry.key for entry in self._config.maps]
        pool = [key for key in catalog if key not in out]
        if not pool:
            raise MapPoolExhaustedError("no allowed map remains to draw", missing=1)
        rng = random.Random(seed)  # noqa: S311 - game draw, not crypto
        return rng.choice(pool)

    async def _draw_gaia_maps(
        self, gaia_id: str, count: int, *, seed: int | None = None
    ) -> list[str]:
        """Draw ``count`` non-out maps as Gaia territories (§16, D31)."""
        out = await self._territories.drawn_map_keys()
        catalog = [entry.key for entry in self._config.maps]
        pool = [key for key in catalog if key not in out]
        if len(pool) < count:
            raise SeasonExhaustedError(
                "Gaia cannot receive the Lord's Day maps - the season ends",
                missing=count - len(pool),
            )
        rng = random.Random(seed)  # noqa: S311 - game draw, not crypto
        rng.shuffle(pool)
        for key in pool[:count]:
            await self._territories.draw_map_for(gaia_id, key)
        return pool[:count]

    async def _kingdom_by_id(self, kingdom_id: str) -> KingdomModel:
        """Resolve a kingdom by id."""
        kingdom = next(
            (item for item in await self._kingdoms.kingdoms() if item.id == kingdom_id),
            None,
        )
        if kingdom is None:
            raise ValueError(f"no kingdom with this id: {kingdom_id}")
        return kingdom

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self._kingdoms.current_season()
        if season is None:
            from kingdoms.mods.kingdoms.service import NoSeasonError

            raise NoSeasonError("no season is running")
        return season
