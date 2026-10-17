"""Kingdoms mod season closing & ShowMatch service (kingdoms-services#162, T8).

Reference §23 and decisions D16/D19/D38/D50/D51/D52: the Conquest
finish at the Monday midnight following the last cycle - territory
counts decide the winner; a tie throws the two leading kingdoms into
the ShowMatch PA2, a ladder of duels (1v1 first, 2v2/3v3 when every
lord has dueled) whose maps come from the finalists' own territories
without reuse and whose civilizations never repeat, Megarandom as the
map fallback. ``archive`` snapshots the whole season data before the
wholesale reset of the next launch (§3.3/D38/D52).
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.models import DuelModel, ShowMatchModel
from kingdoms.mods.kingdoms.service import KingdomsModError, NoSeasonError

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import (
        SeasonState,
    )
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.season_end")

FORMATS = ("1v1", "2v2", "3v3")


class SeasonEndError(KingdomsModError):
    """Base of the season-closing errors."""

    code = "KINGDOMS_SEASON_END_ERROR"
    message_key = "kingdoms.errors.season_end_unexpected"


class ShowMatchError(SeasonEndError):
    """Raised when the ShowMatch flow is misused (D50)."""

    code = "KINGDOMS_SHOWMATCH_ERROR"
    message_key = "kingdoms.errors.showmatch_unexpected"


class SeasonEndService:
    """Conquest closing, ShowMatch PA2 and the season archive (§23)."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
        territory_service: TerritoryService,
    ) -> None:
        """Store the seams: persistence, config, enrollment, territories."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._territories = territory_service

    # ------------------------------------------------------------------
    # Conquest closing (D16/D19/D52)
    # ------------------------------------------------------------------
    async def close_season(self) -> dict[str, object]:
        """Close the season on the territory counts (Conquest, D16).

        The leading kingdom wins; a tie between the two leaders moves
        the decision to a ShowMatch PA2 (D50) and the winner stays
        undecided until its end. Idempotent: calling again on a closed
        season returns the same verdict without touching state.
        """
        season = await self._require_season()
        if season.finished:
            return await self._report(season, season.winner_kingdom_id)
        counts = await self._territories.territory_count_by_kingdom()
        kingdoms = {
            kingdom.id: kingdom
            for kingdom in await self._kingdoms.kingdoms()
            if not kingdom.is_gaia
        }
        scores = {kid: counts.get(kid, 0) for kid in kingdoms}
        ordered = sorted(scores, key=lambda kid: scores[kid], reverse=True)
        winner: str | None = None
        showmatch: ShowMatchModel | None = None
        if ordered and scores[ordered[0]] > scores.get(ordered[1] if len(ordered) > 1 else "", -1):
            winner = ordered[0]
        elif ordered:
            tied = [kid for kid in ordered if scores[kid] == scores[ordered[0]]][:2]
            if len(tied) < 2:
                winner = ordered[0]
            else:
                showmatch = await self._create_showmatch(season, tied[0], tied[1])
        season.finished = True
        season.winner_kingdom_id = winner
        await self._store.upsert_season(season.to_mongo())
        if showmatch is not None:
            logger.info(
                "kingdoms: season closed on a tie - ShowMatch %s vs %s",
                showmatch.kingdom_a,
                showmatch.kingdom_b,
            )
        else:
            logger.info("kingdoms: season closed - winner %s", winner)
        return await self._report(season, winner)

    # ------------------------------------------------------------------
    # ShowMatch PA2 (D50)
    # ------------------------------------------------------------------
    async def showmatch(self) -> ShowMatchModel | None:
        """Return the season's ShowMatch, if any (D50)."""
        season = await self._require_season()
        for doc in await self._store.find_showmatches():
            match = ShowMatchModel.from_mongo(doc)
            if match.season_id == season.id:
                return match
        return None

    async def require_showmatch(self) -> ShowMatchModel:
        """Return the season's ShowMatch or raise (D50)."""
        match = await self.showmatch()
        if match is None:
            raise ShowMatchError("no ShowMatch was opened for this season")
        return match

    async def next_duel(self, *, seed: int | None = None) -> DuelModel:
        """Prepare the next ShowMatch duel (D50).

        Lords duel 1v1 first; when every lord has dueled in a format,
        the ladder escalates - 2v2, then 3v3, then back to 1v1. Maps
        come from the finalists' own territories and never repeat;
        civilizations never repeat either, and the Megarandom fallback
        applies when the own-territory pool is empty.
        """
        match = await self.require_showmatch()
        if match.finished:
            raise ShowMatchError("the ShowMatch is already finished")
        rng = random.Random(seed)  # noqa: S311 - game draw, not crypto
        duel = await self._build_duel(match, rng)
        match.duels = [*match.duels, duel]
        await self._store.upsert_showmatch(match.to_mongo())
        logger.info(
            "kingdoms: ShowMatch duel #%s prepared (%s, map %s)",
            duel.index,
            duel.format,
            duel.map_key,
        )
        return duel

    async def record_duel_result(self, duel_index: int, winner_side: str) -> ShowMatchModel:
        """Record a duel outcome (a/b) and settle the ShowMatch (D50).

        The ShowMatch ends at ``wins_needed`` wins for one side; a 1-1
        tie simply chains the next duel. Recording twice the same duel
        is refused, as is an unknown side.
        """
        match = await self.require_showmatch()
        if winner_side not in ("a", "b"):
            raise ShowMatchError("the winner side must be 'a' or 'b'")
        duels = list(match.duels)
        if not duels or duel_index >= len(duels):
            raise ShowMatchError("no duel with this index")
        duel = duels[duel_index]
        if duel.winner_side is not None:
            raise ShowMatchError("this duel already carries a result")
        duel.winner_side = winner_side
        if winner_side == "a":
            match.score_a += 1
        else:
            match.score_b += 1
        needed = self._config.showmatch.wins_needed
        if match.score_a >= needed or match.score_b >= needed:
            match.finished = True
            match.winner_kingdom_id = (
                match.kingdom_a if match.score_a > match.score_b else match.kingdom_b
            )
            season = await self._require_season()
            season.winner_kingdom_id = match.winner_kingdom_id
            await self._store.upsert_season(season.to_mongo())
        match.duels = duels
        await self._store.upsert_showmatch(match.to_mongo())
        logger.info(
            "kingdoms: ShowMatch duel #%s won by side %s (%s-%s)",
            duel_index,
            winner_side,
            match.score_a,
            match.score_b,
        )
        return match

    # ------------------------------------------------------------------
    # Archive (§3.3, D38/D52)
    # ------------------------------------------------------------------
    async def archive(self) -> dict[str, object]:
        """Snapshot the whole season data before the next reset (D38/D52).

        The reset itself belongs to the launch (wholesale wipe); this
        returns the full archive document the surface persists or
        exports - every season-scoped collection, verbatim.
        """
        return {
            "seasons": await self._store.find_seasons(),
            "kingdoms": await self._store.find_kingdoms(),
            "lords": await self._store.find_lords(),
            "territories": await self._store.find_territories(),
            "technologies": await self._store.find_technologies(),
            "attacks": await self._store.find_attacks(),
            "showmatches": await self._store.find_showmatches(),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _create_showmatch(
        self, season: SeasonState, kingdom_a: str, kingdom_b: str
    ) -> ShowMatchModel:
        """Open the ShowMatch document of the two tied leaders (D50)."""
        match = ShowMatchModel(
            _id=f"{season.id}-showmatch",
            season_id=season.id,
            kingdom_a=kingdom_a,
            kingdom_b=kingdom_b,
        )
        await self._store.upsert_showmatch(match.to_mongo())
        return match

    async def _build_duel(self, match: ShowMatchModel, rng: random.Random) -> DuelModel:
        """Pick the sides, the map and the civilizations of one duel."""
        fmt = await self._next_format(match)
        size = int(fmt[0])
        used_in_format = {
            player
            for duel in match.duels
            if duel.format == fmt
            for player in [*duel.side_a, *duel.side_b]
        }
        lords = await self._lords_by_kingdom()
        side_a = [lord for lord in lords.get(match.kingdom_a, []) if lord not in used_in_format]
        side_b = [lord for lord in lords.get(match.kingdom_b, []) if lord not in used_in_format]
        if len(side_a) < size or len(side_b) < size:
            # Escalation exhausted every fresh lord: reuse the whole rosters.
            side_a = lords.get(match.kingdom_a, [])
            side_b = lords.get(match.kingdom_b, [])
        side_a = side_a[:size]
        side_b = side_b[:size]
        duel = DuelModel(index=len(match.duels), format=fmt, side_a=side_a, side_b=side_b)
        duel.map_key = await self._pick_map(match, rng)
        for player in [*duel.side_a, *duel.side_b]:
            duel.civilizations[player] = self._pick_civilization(match, rng)
        return duel

    async def _next_format(self, match: ShowMatchModel) -> str:
        """Return the format of the next duel (D50 escalation).

        1v1 first; once every active lord has dueled in the current
        format the ladder escalates - 2v2, then 3v3, then back to 1v1.
        """
        current = FORMATS[0]
        for duel in match.duels:
            if duel.winner_side is None:
                return duel.format
            current = duel.format
        if not match.duels:
            return current
        lords = await self._lords_by_kingdom()
        for fmt in (current, *FORMATS):
            size = int(fmt[0])
            used = {
                player
                for duel in match.duels
                if duel.format == fmt
                for player in [*duel.side_a, *duel.side_b]
            }
            fresh_a = [lord for lord in lords.get(match.kingdom_a, []) if lord not in used]
            fresh_b = [lord for lord in lords.get(match.kingdom_b, []) if lord not in used]
            if len(fresh_a) >= size and len(fresh_b) >= size:
                return fmt
        return current

    async def _pick_map(self, match: ShowMatchModel, rng: random.Random) -> str:
        """Pick an own-territory map, Megarandom as fallback (D50)."""
        owned = {
            territory.map_key
            for territory in await self._territories.territories()
            if territory.owner_kingdom_id in (match.kingdom_a, match.kingdom_b)
        }
        pool = sorted(owned - set(match.used_map_keys))
        if pool:
            key = rng.choice(pool)
        else:
            key = self._config.showmatch.fallback_map_key
        match.used_map_keys = [*match.used_map_keys, key]
        return key

    def _pick_civilization(self, match: ShowMatchModel, rng: random.Random) -> str:
        """Pick a civilization that never repeated in the ShowMatch (D50)."""
        catalog = [civ.key for civ in self._config.civilizations]
        pool = [key for key in catalog if key not in match.used_civilizations]
        if not pool:
            pool = catalog or ["random"]
        key = rng.choice(sorted(pool))
        match.used_civilizations = [*match.used_civilizations, key]
        return key

    async def _lords_by_kingdom(self) -> dict[str, list[str]]:
        """Group the active lords by kingdom id."""
        grouped: dict[str, list[str]] = {}
        for lord in await self._kingdoms.lords():
            if lord.left or lord.kingdom_id is None:
                continue
            grouped.setdefault(lord.kingdom_id, []).append(lord.id)
        return grouped

    async def _report(self, season: SeasonState, winner: str | None) -> dict[str, object]:
        """Build the closing report broadcast by the surface."""
        counts = await self._territories.territory_count_by_kingdom()
        kingdoms = await self._kingdoms.kingdoms()
        names = {kingdom.id: kingdom.name for kingdom in kingdoms}
        gaia_ids = {kingdom.id for kingdom in kingdoms if kingdom.is_gaia}
        per_kingdom = {
            names.get(kid, kid): counts.get(kid, 0)
            for kid in counts
            if kid not in gaia_ids
        }
        return {
            "season_id": season.id,
            "winner_kingdom_id": winner,
            "winner_name": names.get(winner) if winner else None,
            "territories": per_kingdom,
            "showmatch": await self.showmatch() is not None,
        }

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        return season
