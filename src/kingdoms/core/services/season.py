"""Season service: generic season lifecycle over ladder pool rotations (kingdoms-services#132).

Seasons live in the generic core so tournament/other mods reuse them later.
Activation is a transactional pool switch delegated to GameDataService
(``activate_map_pool``), plus the season's own state transition, the
``pool.switched`` notification intent and an audit line. ``reset_ratings``
seasons additionally announce ``ratings.reset`` — the rating path itself
(RESET rating-history entries) belongs to the ladder domain (#134).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kingdoms.core.ids import season_id
from kingdoms.core.models.season import (
    SEASON_STATE_ACTIVE,
    SEASON_STATE_ENDED,
    SEASON_STATE_SCHEDULED,
    SeasonModel,
)
from kingdoms.core.services.game_data import GameDataService

logger = logging.getLogger("kingdoms.core.seasons")

SEASONS_COLLECTION = "seasons"


class SeasonDatabase(Protocol):
    """Narrow async MongoDB seam the SeasonService depends on."""

    async def upsert_season(self, document: dict[str, Any]) -> None:
        """Insert or replace one season document by ``_id``."""
        ...

    async def find_season(self, season_id: str) -> dict[str, Any] | None:
        """Return one season document; None when absent."""
        ...

    async def find_ladder_seasons(self, ladder_id: str) -> list[dict[str, Any]]:
        """List the seasons of a ladder (ascending by start)."""
        ...

    async def find_active_season(self, ladder_id: str) -> dict[str, Any] | None:
        """Return the ladder's active season; None when none."""
        ...

    async def delete_season(self, season_id: str) -> None:
        """Delete one season document (never-started seasons only)."""
        ...


class SeasonEvents(Protocol):
    """Notification-intent seam: the surface layer fans out (pool.switched...)."""

    async def emit(self, intent: str, payload: dict[str, Any]) -> None:
        """Emit one notification intent (best-effort)."""
        ...


class SeasonAudit(Protocol):
    """Narrow audit seam: every season mutation is recorded."""

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        """Persist one admin-audit line."""
        ...


class SeasonNotScheduledError(ValueError):
    """The season is not in the scheduled state for this transition."""


class SeasonActiveError(ValueError):
    """The season is already active, or another season is active on the ladder."""


class SeasonService:
    """Create, schedule, activate and end seasons on a ladder."""

    def __init__(
        self,
        database: SeasonDatabase,
        game_data: GameDataService,
        events: SeasonEvents | None = None,
        audit: SeasonAudit | None = None,
    ) -> None:
        """Wire the persistence, game-data (pool switch) and event seams."""
        self._db = database
        self._game_data = game_data
        self._events = events
        self._audit = audit

    async def create_season(
        self,
        ladder_id: str,
        name: str,
        map_pool_id: str | None,
        start_at: int,
        end_at: int | None = None,
        reset_ratings: bool = False,
    ) -> SeasonModel:
        """Create a scheduled season; no live (scheduled or active) season may exist when another is created.

        The season index is incremental per ladder (1, 2, 3…) and the
        visible id embeds it: ``<ladder_id>:<index>`` — uniqueness is
        the (ladder, index) pair, never the name.
        """
        if end_at is not None and end_at <= start_at:
            raise ValueError("season end must be after start")
        existing = await self._db.find_ladder_seasons(ladder_id)
        unfinished = [d for d in existing if d.get("state") != SEASON_STATE_ENDED]
        if unfinished:
            raise SeasonActiveError(
                f"ladder {ladder_id!r} already has a live season {unfinished[0].get('_id')!r} "
                "(end or delete it before creating a new one)"
            )
        index = max((int(doc.get("index", 0)) for doc in existing), default=0) + 1
        season = SeasonModel(
            _id=season_id(ladder_id, index),
            ladder_id=ladder_id,
            index=index,
            name=name,
            map_pool_id=map_pool_id,
            start_at=start_at,
            end_at=end_at,
            reset_ratings=reset_ratings,
        )
        await self._db.upsert_season(season.to_mongo())
        await self._audit_record("season.create", {"season_id": season.id, "ladder_id": ladder_id, "name": name})
        return season

    async def get_season(self, season_id: str) -> SeasonModel | None:
        """Return one season; None when unknown."""
        doc = await self._db.find_season(season_id)
        return SeasonModel.from_mongo(doc) if doc else None

    async def list_seasons(self, ladder_id: str) -> list[SeasonModel]:
        """List the seasons of a ladder, ascending by start."""
        docs = await self._db.find_ladder_seasons(ladder_id)
        return [SeasonModel.from_mongo(d) for d in docs]

    async def get_active_season(self, ladder_id: str) -> SeasonModel | None:
        """Return the ladder's active season; None when none."""
        doc = await self._db.find_active_season(ladder_id)
        return SeasonModel.from_mongo(doc) if doc else None

    async def activate_season(self, season_id: str, now: int) -> SeasonModel:
        """Activate a scheduled season: transactional pool switch + state.

        Delegates the pool history to ``GameDataService.activate_map_pool``
        (the transactional switch of reference §4), then flips the season
        state, emits ``pool.switched`` (players) and — for reset seasons —
        ``ratings.reset`` intents, and audits the activation.
        """
        season = await self._require(season_id)
        if season.state != SEASON_STATE_SCHEDULED:
            raise SeasonNotScheduledError(f"season {season_id!r} is not scheduled (state {season.state!r})")
        current = await self._db.find_active_season(season.ladder_id)
        if current is not None and current["_id"] != season_id:
            raise SeasonActiveError(f"ladder {season.ladder_id!r} already has an active season {current['_id']!r}")
        if season.map_pool_id:
            await self._game_data.activate_map_pool(season.ladder_id, season.map_pool_id)
        season = season.model_copy(update={"state": SEASON_STATE_ACTIVE, "activated_at": now})
        await self._db.upsert_season(season.to_mongo())
        if season.map_pool_id:
            await self._emit("pool.switched", {"ladder_id": season.ladder_id, "map_pool_id": season.map_pool_id})
        if season.reset_ratings:
            await self._emit("ratings.reset", {"ladder_id": season.ladder_id, "season_id": season.id})
        await self._audit_record("season.activate", {"season_id": season.id, "ladder_id": season.ladder_id})
        return season

    async def reopen_season(self, season_id: str, now: int) -> SeasonModel:
        """Reopen an ended season: it becomes the ladder's active season again.

        Allowed only when no other live (scheduled or active) season exists
        on the ladder — creating a new season remains the alternative path.
        The season returns to the active state, keeps its pool and history,
        and the reopening is audited.
        """
        season = await self._require(season_id)
        if season.state != SEASON_STATE_ENDED:
            raise SeasonActiveError(
                f"season {season_id!r} is not ended (state {season.state!r}) - it cannot be reopened"
            )
        existing = await self._db.find_ladder_seasons(season.ladder_id)
        live = [d for d in existing if d.get("state") != SEASON_STATE_ENDED and d.get("_id") != season_id]
        if live:
            raise SeasonActiveError(
                f"ladder {season.ladder_id!r} has a live season {live[0].get('_id')!r} "
                "(end or delete it before reopening)"
            )
        season = season.model_copy(update={"state": SEASON_STATE_ACTIVE, "ended_at": None})
        await self._db.upsert_season(season.to_mongo())
        await self._audit_record("season.reopen", {"season_id": season.id, "ladder_id": season.ladder_id})
        return season

    async def delete_season(self, season_id: str) -> SeasonModel:
        """Delete a season that never started (scheduled, never activated).

        A season with an activation or an end is history: deleting it
        would break the ladder's audit trail. Only a plain scheduled
        season (created but never activated) can be removed, e.g. to
        recreate one with a corrected id.
        """
        season = await self._require(season_id)
        if season.state != SEASON_STATE_SCHEDULED or season.activated_at is not None:
            raise SeasonActiveError(
                f"season {season_id!r} already started (state {season.state!r}) - it cannot be deleted"
            )
        await self._db.delete_season(season_id)
        await self._audit_record("season.delete", {"season_id": season.id, "ladder_id": season.ladder_id})
        return season

    async def end_season(self, season_id: str, now: int) -> SeasonModel:
        """End the active season; the ladder keeps its current pool."""
        season = await self._require(season_id)
        if season.state != SEASON_STATE_ACTIVE:
            raise SeasonActiveError(f"season {season_id!r} is not active (state {season.state!r})")
        season = season.model_copy(update={"state": SEASON_STATE_ENDED, "ended_at": now})
        await self._db.upsert_season(season.to_mongo())
        await self._audit_record("season.end", {"season_id": season.id, "ladder_id": season.ladder_id})
        return season

    # ── helpers ──────────────────────────────────────────────────────────

    async def _require(self, season_id: str) -> SeasonModel:
        """Fetch one season or fail loudly (unknown ids are bugs)."""
        doc = await self._db.find_season(season_id)
        if doc is None:
            raise ValueError(f"unknown season {season_id!r}")
        return SeasonModel.from_mongo(doc)

    async def _emit(self, intent: str, payload: dict[str, Any]) -> None:
        """Emit one notification intent (best-effort, never raises)."""
        if self._events is None:
            return
        try:
            await self._events.emit(intent, payload)
        except Exception:
            logger.warning("SEASON EVENT EMIT FAILED (%s)", intent, exc_info=True)

    async def _audit_record(self, action: str, payload: dict[str, Any]) -> None:
        """Write one audit line (best-effort: a store failure logs, never raises)."""
        if self._audit is None:
            return
        try:
            await self._audit.record(action, payload)
        except Exception:
            logger.warning("AUDIT WRITE FAILED (%s)", action, exc_info=True)
