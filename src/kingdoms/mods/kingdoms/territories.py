"""Kingdoms mod territories & maps service (kingdoms-services#163, T3).

Reference §6-§10 + decision D31: the admin-managed map catalog, the
random season draw without duplicates, the per-season "map is out"
state, the admin ejection with an automatic replacement, and the
idempotent ownership-transfer primitive reused by T4/T5/T7.

The cadastre **effects** of a map (CADASTRE.md) are stored as data on
the catalog entry and applied by the later slices — this service owns
possession and drawing only (issue #163 scope note).
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.models import TerritoryModel
from kingdoms.mods.kingdoms.service import (
    KingdomNotFoundError,
    KingdomsModError,
    NoSeasonError,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import SeasonState
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.territories")


class MapPoolExhaustedError(KingdomsModError):
    """Raised when the allowed-map catalog cannot cover a draw (§7, D31).

    ``missing`` context entry tells the admin how many more maps the
    catalog needs; the caller surfaces it as an admin warning.
    """

    code = "KINGDOMS_MAP_POOL_EXHAUSTED"
    message_key = "kingdoms.errors.map_pool_exhausted"

    def __init__(self, message: str, *, missing: int = 0) -> None:
        """Create the error with the missing-map count for the admin."""
        super().__init__(message, missing=str(missing))
        self.missing = missing


class TerritoryNotFoundError(KingdomsModError):
    """Raised when no territory of the current data set matches."""

    code = "KINGDOMS_TERRITORY_NOT_FOUND"
    message_key = "kingdoms.errors.territory_not_found"


class TerritoryService:
    """Territory possession and map-drawing rules (reference §6-§10)."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
    ) -> None:
        """Store the seams: persistence, season config, and the enrollment service."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service

    async def territories(self) -> list[TerritoryModel]:
        """Return every territory of the current data set."""
        return [TerritoryModel.from_mongo(doc) for doc in await self._store.find_territories()]

    async def drawn_map_keys(self) -> set[str]:
        """Return the "out" map keys: drawn once, never redrawn this season (§8).

        Drawn territories plus the ejected maps recorded on the season
        document — an ejected map stays out for the whole season.
        """
        keys = {territory.map_key for territory in await self.territories()}
        season = await self._kingdoms.current_season()
        return keys | set(season.out_maps) if season is not None else keys

    async def draw_initial(self, *, seed: int | None = None) -> list[TerritoryModel]:
        """Draw the season's initial territories (§6.3).

        N per player kingdom + M for Gaïa, random, without duplicates.

        Fails atomically with an admin-actionable error when the catalog
        is too small (§7) — nothing is persisted then. ``seed`` exists for
        deterministic tests only.
        """
        season = await self._require_season()
        kingdoms = await self._kingdoms.kingdoms()
        player_kingdoms = [kingdom for kingdom in kingdoms if not kingdom.is_gaia]
        gaia = next((kingdom for kingdom in kingdoms if kingdom.is_gaia), None)
        wanted = len(player_kingdoms) * self._config.territories_per_kingdom
        wanted += self._config.gaia_territories if gaia is not None else 0
        catalog = [entry.key for entry in self._config.maps]
        already = await self.drawn_map_keys()
        pool = [key for key in catalog if key not in already]
        if len(pool) < wanted:
            raise MapPoolExhaustedError(
                "the allowed-map catalog cannot cover the initial draw",
                missing=wanted - len(pool),
            )
        rng = random.Random(seed)  # noqa: S311 - game draw, not crypto
        rng.shuffle(pool)
        cursor = 0
        created: list[TerritoryModel] = []
        for kingdom in player_kingdoms:
            for _ in range(self._config.territories_per_kingdom):
                created.append(self._new_territory(season, pool[cursor], kingdom.id))
                cursor += 1
        if gaia is not None:
            for _ in range(self._config.gaia_territories):
                created.append(self._new_territory(season, pool[cursor], gaia.id))
                cursor += 1
        for territory in created:
            await self._store.upsert_territory(territory.to_mongo())
        logger.info("kingdoms: initial draw — %s territories over %s kingdoms", cursor, len(player_kingdoms) + 1)
        return created

    async def transfer(self, territory_id: str, new_owner_kingdom_id: str) -> TerritoryModel:
        """Idempotent ownership transfer (§9, primitive for T4/T5/T7).

        Transferring to the current owner is a no-op; the target kingdom
        must exist (Gaïa included — conquered by, corruption…).
        """
        await self._require_season()
        territory = await self._find_territory(territory_id)
        kingdoms = await self._kingdoms.kingdoms()
        if not any(kingdom.id == new_owner_kingdom_id for kingdom in kingdoms):
            raise KingdomNotFoundError("no kingdom with this id in the current season")
        if territory.owner_kingdom_id == new_owner_kingdom_id:
            return territory
        territory.owner_kingdom_id = new_owner_kingdom_id
        await self._store.upsert_territory(territory.to_mongo())
        logger.info(
            "kingdoms: territory %s (%s) transferred to %s",
            territory.id,
            territory.map_key,
            new_owner_kingdom_id,
        )
        return territory

    async def eject(self, map_key: str, *, seed: int | None = None) -> TerritoryModel:
        """Admin ejection (§9): remove the drawn map, replace it.

        The replacement goes to the same owner with a random non-out map.

        Fails atomically when no replacement exists — the drawn map then
        stays in place and the admin is warned (issue #163 spec 4).
        """
        await self._require_season()
        territories = await self.territories()
        territory = next((item for item in territories if item.map_key == map_key), None)
        if territory is None:
            raise TerritoryNotFoundError("this map is not a drawn territory of the current season")
        catalog = [entry.key for entry in self._config.maps]
        already = {item.map_key for item in territories}
        pool = [key for key in catalog if key not in already]
        if not pool:
            raise MapPoolExhaustedError("no non-out map remains as a replacement", missing=1)
        rng = random.Random(seed)  # noqa: S311 - game draw, not crypto
        replacement_key = rng.choice(pool)
        season = await self._require_season()
        if map_key not in season.out_maps:
            season.out_maps = [*season.out_maps, map_key]
        await self._store.upsert_season(season.to_mongo())
        replacement = TerritoryModel(
            _id=territory.id,
            season_id=territory.season_id,
            map_key=replacement_key,
            owner_kingdom_id=territory.owner_kingdom_id,
            drawn_at=datetime.now(tz=UTC),
        )
        await self._store.upsert_territory(replacement.to_mongo())
        logger.info(
            "kingdoms: map %s ejected, replaced by %s for %s",
            map_key,
            replacement_key,
            territory.owner_kingdom_id,
        )
        return replacement

    async def territory_count_by_kingdom(self) -> dict[str, int]:
        """Return owned-territory counts per kingdom id (Conquest basis, T8)."""
        counts: dict[str, int] = {}
        for territory in await self.territories():
            counts[territory.owner_kingdom_id] = counts.get(territory.owner_kingdom_id, 0) + 1
        return counts

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        return season

    async def _find_territory(self, territory_id: str) -> TerritoryModel:
        """Return the territory document or raise a not-found error."""
        territory = next(
            (item for item in await self.territories() if item.id == territory_id),
            None,
        )
        if territory is None:
            raise TerritoryNotFoundError("no territory with this id in the current data set")
        return territory

    @staticmethod
    def _new_territory(season: SeasonState, map_key: str, owner_kingdom_id: str) -> TerritoryModel:
        """Build one territory document with a season-unique id."""
        return TerritoryModel(
            _id=f"{season.id}-t-{map_key}",
            season_id=season.id,
            map_key=map_key,
            owner_kingdom_id=owner_kingdom_id,
            drawn_at=datetime.now(tz=UTC),
        )
