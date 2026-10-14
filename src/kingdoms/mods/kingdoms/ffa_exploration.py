"""The Saturday FFA Exploration service (ffa-exploration-v1, Drasah 14/10/2026).

Each Saturday the bot draws a random start hour (10h-20h Paris, full
hours), announces the FFA in the Géopolitique salon 2h before — the
announcement opens the enrollments through the persistent « Aller
explorer » button. Registration is FREE (no attack budget), capped by
the Gaïa free-for-all participant ceiling (7), with the admin
parameter ``1 seigneur par royaume`` or ``libre``; a lord locked by a
fresh Explorateur purchase (marche-v2 art. 6) or by a previous FFA
no-show cannot enroll.

One single 1h15 game mixes the AI Gaïa and every participant; the
ranking comes back through the game contract (D20) as a lord-id list
best-first — the tie rule (plus petit Elo) is resolved upstream, the
mod carries no Elo yet. The rewards flow through the existing
``EventService.run_exploration`` pipeline (random Gaïa map for the
winner + graduated tech points), and a registered participant absent
from the ranking is a no-show: 72h combat lock starting at the NEXT
cycle switch.
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from kingdoms.mods.kingdoms.attacks import LordLockedError
from kingdoms.mods.kingdoms.events import cron_slots_between
from kingdoms.mods.kingdoms.models import FFAExplorationModel
from kingdoms.mods.kingdoms.service import KingdomsModError, NoSeasonError

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.events import EventService
    from kingdoms.mods.kingdoms.models import LordModel
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.ffa")

# The enroll/leave answer of the toggle button.
FFA_JOINED = "joined"
FFA_LEFT = "left"


class FFAError(KingdomsModError):
    """Base of the FFA Exploration errors."""

    code = "KINGDOMS_FFA_ERROR"
    message_key = "kingdoms.errors.ffa_unexpected"


class FFANotOpenError(FFAError):
    """Raised when no FFA accepts enrollments at the moment."""

    code = "KINGDOMS_FFA_NOT_OPEN"
    message_key = "kingdoms.errors.ffa_not_open"


class FFAFullError(FFAError):
    """Raised when the FFA accepts no more participant."""

    code = "KINGDOMS_FFA_FULL"
    message_key = "kingdoms.errors.ffa_full"


class FFAKingdomFullError(FFAError):
    """Raised when the kingdom already holds a slot (1/royaume mode)."""

    code = "KINGDOMS_FFA_KINGDOM_FULL"
    message_key = "kingdoms.errors.ffa_kingdom_full"


def _schedule_timezone(config: KingdomsSeasonConfig) -> ZoneInfo:
    """Resolve the scheduling timezone, UTC as the safe fallback."""
    try:
        return ZoneInfo(config.events.schedule_timezone)
    except Exception:
        logger.warning("kingdoms: unknown schedule timezone %r", config.events.schedule_timezone)
        return ZoneInfo("UTC")


def _saturday(now: datetime, tz: ZoneInfo) -> date:
    """Return the local date of the (most recent) Saturday of ``now``."""
    local = now.astimezone(tz)
    back = (local.weekday() - 5) % 7  # Saturday = 5
    return (local - timedelta(days=back)).date()


class FFAExplorationService:
    """Own the Saturday FFA Exploration state machine."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms: KingdomsService,
        events: EventService,
    ) -> None:
        self._store = store
        self._config = config
        self._kingdoms = kingdoms
        self._events = events

    # ------------------------------------------------------------------
    # The Saturday draw and its calendar
    # ------------------------------------------------------------------
    def _doc_id(self, season_id: str, date: object) -> str:
        return f"{season_id}-ffa-{date}"

    async def _find_doc(self, doc_id: str) -> FFAExplorationModel | None:
        for document in await self._store.find_ffas():
            if document.get("_id") == doc_id:
                return FFAExplorationModel.from_mongo(document)
        return None

    async def current(self, now: datetime) -> FFAExplorationModel | None:
        """Return this Saturday's FFA document, None outside a Saturday draw."""
        season = await self._kingdoms.current_season()
        if season is None or season.finished:
            return None
        tz = _schedule_timezone(self._config)
        date = _saturday(now, tz)
        return await self._find_doc(self._doc_id(season.id, date))

    async def draw_or_get(
        self,
        now: datetime,
        *,
        seed: int | None = None,
    ) -> FFAExplorationModel | None:
        """Draw (or return) this Saturday's FFA start hour.

        Restart-safe by persistence: the first tick of the Saturday
        draws the hour and stores the document — every later tick (and
        every restart) reads the same draw back. Returns None when the
        FFA is disabled or the season has no running Saturday FFA.
        """
        settings = self._config.events
        if not settings.ffa_enabled:
            return None
        season = await self._kingdoms.current_season()
        if season is None or season.finished or season.phase == "setup":
            return None
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        tz = _schedule_timezone(self._config)
        date = _saturday(now, tz)
        doc_id = self._doc_id(season.id, date)
        existing = await self._find_doc(doc_id)
        if existing is not None:
            return existing
        hours = tuple(settings.ffa_start_hours) or (14,)
        generator = random.Random(seed) if seed is not None else random  # noqa: S311 - game draw, not crypto
        start_local = datetime.combine(date, time(generator.choice(hours)), tzinfo=tz)
        ffa = FFAExplorationModel(
            _id=doc_id,
            season_id=season.id,
            date=date.isoformat(),
            start_at=start_local.astimezone(UTC),
            announce_at=start_local.astimezone(UTC)
            - timedelta(hours=settings.ffa_announce_lead_hours),
            one_lord_per_kingdom=bool(settings.ffa_one_lord_per_kingdom),
        )
        await self._store.upsert_ffa(ffa.to_mongo())
        logger.info(
            "kingdoms: FFA Exploration of %s drawn at %02dh (Paris)", date, start_local.hour
        )
        return ffa

    def due_announce(self, ffa: FFAExplorationModel, now: datetime) -> bool:
        """Whether the Géopolitique announcement is due (2h before)."""
        if ffa.announced:
            return False
        return now >= ffa.announce_at

    async def mark_announced(self, ffa: FFAExplorationModel) -> FFAExplorationModel:
        """Flag the FFA as announced — enrollments open from now on."""
        ffa.announced = True
        await self._store.upsert_ffa(ffa.to_mongo())
        return ffa

    def due_start(self, ffa: FFAExplorationModel, now: datetime) -> bool:
        """Whether the FFA game is due to start."""
        if ffa.started or ffa.finished:
            return False
        return now >= ffa.start_at

    # ------------------------------------------------------------------
    # Enrollments (the « Aller explorer » button)
    # ------------------------------------------------------------------
    async def enroll(
        self,
        player_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[str, FFAExplorationModel]:
        """Reserve (or release) one FFA slot — a toggle, first come first served.

        Drasah's open point (withdrawals): the executor's proposal is a
        TOGGLE — clicking « Aller explorer » again before the start
        RELEASES the slot, so a withdrawer never blocks a seat.
        """
        timestamp = now or datetime.now(tz=UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        ffa = await self.current(timestamp)
        if (
            ffa is None
            or not ffa.announced
            or ffa.started
            or ffa.finished
            or timestamp >= ffa.start_at
        ):
            raise FFANotOpenError("no FFA Exploration accepts enrollments right now")
        lords = {item.id: item for item in await self._kingdoms.lords()}
        lord = lords.get(player_id)
        if lord is None or lord.kingdom_id is None or lord.left:
            raise FFANotOpenError("the player belongs to no kingdom of the current season")
        # The button toggles: a second click releases the slot.
        if player_id in ffa.participants:
            ffa.participants = [p for p in ffa.participants if p != player_id]
            await self._store.upsert_ffa(ffa.to_mongo())
            logger.info("kingdoms: %s leaves the FFA Exploration of %s", player_id, ffa.date)
            return FFA_LEFT, ffa
        # marche-v2 art. 6: a lord sent exploring (48h lock) may
        # NOT enroll in the FFA — same rule as the Gaïa free-for-all.
        if lord.exploration_locked_until is not None and lord.exploration_locked_until > int(
            timestamp.timestamp()
        ):
            raise LordLockedError("the lord is locked by the exploration (marche-v2 art. 6)")
        # ffa-exploration-v1: a previous no-show stays locked out.
        if lord.ffa_locked_until is not None and lord.ffa_locked_until > int(
            timestamp.timestamp()
        ):
            raise LordLockedError("the lord is locked by a missed FFA (ffa-exploration-v1)")
        if len(ffa.participants) >= self._max_participants():
            raise FFAFullError("the FFA Exploration is full")
        self._refuse_second_kingdom_slot(lord, ffa, lords)
        ffa.participants = [*ffa.participants, player_id]
        await self._store.upsert_ffa(ffa.to_mongo())
        logger.info(
            "kingdoms: %s takes the FFA Exploration slot of %s", player_id, ffa.date
        )
        return FFA_JOINED, ffa

    def _refuse_second_kingdom_slot(
        self,
        lord: LordModel,
        ffa: FFAExplorationModel,
        lords: dict[str, LordModel],
    ) -> None:
        """Refuse a second slot for the same kingdom in 1/royaume mode."""
        if not ffa.one_lord_per_kingdom:
            return
        for participant in ffa.participants:
            other = lords.get(participant)
            if other is not None and other.kingdom_id == lord.kingdom_id:
                raise FFAKingdomFullError("the kingdom already holds an FFA Exploration slot")

    def _max_participants(self) -> int:
        return int(self._config.attacks.gaia_max_participants)

    def max_participants(self) -> int:
        """Return the FFA participant ceiling (the Gaïa free-for-all cap, 7).

        Exposed for the panel views and the join button notes.
        """
        return self._max_participants()

    # ------------------------------------------------------------------
    # The game start and the result ingestion (D20)
    # ------------------------------------------------------------------
    async def start_due(
        self,
        now: datetime | None = None,
    ) -> FFAExplorationModel | None:
        """Flip the due FFA to started; return it when it just started."""
        timestamp = now or datetime.now(tz=UTC)
        ffa = await self.current(timestamp)
        if ffa is None or not self.due_start(ffa, timestamp):
            return None
        ffa.started = True
        await self._store.upsert_ffa(ffa.to_mongo())
        logger.info("kingdoms: FFA Exploration of %s starts (%d participant(s))", ffa.date, len(ffa.participants))
        return ffa

    async def ingest_result(
        self,
        ranking: list[str],
        *,
        now: datetime | None = None,
    ) -> FFAExplorationModel:
        """Apply the game contract's ranking (lord ids best-first).

        The rewards flow through ``EventService.run_exploration`` with
        the KINGDOM ranking (a kingdom ranks as its best lord, deduped
        — the mod has no per-lord treasury). Every registered
        participant absent from the ranking is a no-show: 72h combat
        lock starting at the NEXT cycle switch (the lock lands in the
        future by construction — a no-show on Saturday is locked from
        the following Sunday 23:30).
        """
        timestamp = now or datetime.now(tz=UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        ffa = await self.current(timestamp)
        if ffa is None:
            raise NoSeasonError("no FFA Exploration document to resolve")
        if ffa.finished:
            return ffa  # idempotent replay
        ffa.ranking = list(ranking)
        ffa.no_shows = [p for p in ffa.participants if p not in ffa.ranking]
        ffa.finished = True
        ffa.resolved_at = timestamp
        await self._store.upsert_ffa(ffa.to_mongo())
        # Kingdom ranking: a kingdom ranks as its best lord.
        lords = {item.id: item for item in await self._kingdoms.lords()}
        kingdom_ranking: list[str] = []
        for lord_id in ffa.ranking:
            lord = lords.get(lord_id)
            if lord is None or lord.kingdom_id is None or lord.kingdom_id in kingdom_ranking:
                continue
            kingdom_ranking.append(lord.kingdom_id)
        report = await self._events.run_exploration(kingdom_ranking)
        logger.info(
            "kingdoms: FFA Exploration of %s resolved — rewards %s, no-shows %s",
            ffa.date,
            report.get("rewards"),
            ffa.no_shows,
        )
        # The no-show lock: 72h starting at the NEXT cycle switch.
        lock_until = await self._no_show_lock_until(timestamp)
        for lord_id in ffa.no_shows:
            lord = lords.get(lord_id)
            if lord is None:
                continue
            lord.ffa_locked_until = int(lock_until.timestamp())
            await self._store.upsert_lord(lord.to_mongo())
            logger.info(
                "kingdoms: %s missed the FFA — locked until %s",
                lord_id,
                lock_until.isoformat(),
            )
        return ffa

    async def _no_show_lock_until(self, now: datetime) -> datetime:
        """Compute the next cycle switch plus the configured lock hours."""
        tz = _schedule_timezone(self._config)
        slots = cron_slots_between(
            self._config.events.cycle_cron, now, now + timedelta(days=8), tz
        )
        base = slots[0] if slots else now
        return base + timedelta(hours=self._config.events.ffa_no_show_lock_hours)
