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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.territories import (
    MapPoolExhaustedError,
    TerritoryService,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.attacks import AttackService
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import KingdomModel, SeasonState
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.events")

WEEK = timedelta(weeks=1)


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
    ) -> None:
        """Store the seams of the orchestrated services."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._territories = territory_service
        self._attacks = attacks_service

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
        return {
            "cycle": season.current_cycle,
            "recharged_budgets": recharged,
            "gaia_new_maps": added,
            "gaia_kingdom_id": gaia.id if gaia is not None else None,
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

        The cadence is recomputed from ``started_at`` (one cycle per
        week); a bot restart never skips a switch: this call walks the
        missing weeks and runs one cycle switch per week.
        """
        season = await self._require_season()
        missed = self._missed_weeks(season, now)
        reports: list[dict[str, object]] = []
        for _ in range(missed):
            reports.append(await self.run_cycle_switch())
        return reports

    def _missed_weeks(self, season: SeasonState, now: datetime) -> int:
        """How many weekly switches sit between the state and ``now``."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        elapsed = now - season.started_at
        if elapsed <= WEEK:
            return 0
        return max(0, int(elapsed // WEEK) - season.current_cycle)

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
