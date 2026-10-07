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
        # Idempotent: a kingdom only draws its missing share, so an
        # explicit draw after the launch draw is a no-op.
        existing = await self.territories()
        counts = self._count_by_kingdom(existing)
        per_kingdom = self._config.territories_per_kingdom
        incomplete = [
            kingdom for kingdom in player_kingdoms if counts.get(kingdom.id, 0) < per_kingdom
        ]
        gaia_missing = (
            max(0, self._config.gaia_territories - counts.get(gaia.id, 0)) if gaia is not None else 0
        )
        wanted = sum(per_kingdom - counts.get(kingdom.id, 0) for kingdom in incomplete)
        wanted += gaia_missing
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
        created: list[TerritoryModel] = []
        # Balance rule (product decision): every player kingdom receives
        # the same number of bonus maps (non-empty cadastre effects); the
        # rest is drawn neutrally, one single pool, pure random.
        if incomplete:
            bonus_each = min(len(pool) // len(incomplete), per_kingdom) if pool else 0
            for kingdom in incomplete:
                missing = per_kingdom - counts.get(kingdom.id, 0)
                created.extend(
                    self._draw_for_kingdom(season, rng, pool, kingdom.id, missing, bonus_each)
                )
        if gaia is not None:
            for _ in range(gaia_missing):
                created.append(self._new_territory(season, pool.pop(), gaia.id))
        if not created:
            return []
        for territory in created:
            await self._store.upsert_territory(territory.to_mongo())
        logger.info("kingdoms: initial draw — %s territories over %s kingdoms", len(created), len(player_kingdoms) + 1)
        return created

    def _draw_for_kingdom(
        self,
        season: SeasonState,
        rng: random.Random,
        pool: list[str],
        kingdom_id: str,
        per_kingdom: int,
        bonus_target: int,
    ) -> list[TerritoryModel]:
        """Draw one kingdom's territories from the shared pool.

        Bonus maps first (parity), then the neutral remainder.
        """
        bonus_keys = [key for key in pool if self._map_cadastre(key)]
        neutral_keys = [key for key in pool if key not in bonus_keys]
        drawn: list[str] = []
        for _ in range(min(bonus_target, len(bonus_keys))):
            drawn.append(bonus_keys.pop(rng.randrange(len(bonus_keys))))
        rest = per_kingdom - len(drawn)
        for _ in range(min(rest, len(neutral_keys))):
            drawn.append(neutral_keys.pop(rng.randrange(len(neutral_keys))))
        while len(drawn) < per_kingdom and bonus_keys:  # pool skewed to bonus
            drawn.append(bonus_keys.pop(rng.randrange(len(bonus_keys))))
        for key in drawn:
            pool.remove(key)
        return [self._new_territory(season, key, kingdom_id) for key in drawn]

    async def top_up_kingdom(self, kingdom_id: str, *, seed: int | None = None) -> list[TerritoryModel]:
        """Draw the missing territories of one post-launch kingdom.

        Free-founding mode, same bonus-parity rule, so every kingdom
        converges to the same bonus-map count.
        """
        season = await self._require_season()
        already = await self.drawn_map_keys()
        pool = [entry.key for entry in self._config.maps if entry.key not in already]
        kingdom = next(
            (kingdom for kingdom in await self._kingdoms.kingdoms() if kingdom.id == kingdom_id),
            None,
        )
        if kingdom is None or kingdom.is_gaia:
            raise TerritoryNotFoundError(f"no player kingdom {kingdom_id}")
        all_territories = await self.territories()
        kingdom_territories = [
            territory for territory in all_territories if territory.owner_kingdom_id == kingdom_id
        ]
        if len(kingdom_territories) >= self._config.territories_per_kingdom:
            return []  # already complete — idempotent
        per_kingdom = self._config.territories_per_kingdom - len(kingdom_territories)
        player_kingdoms = [k for k in await self._kingdoms.kingdoms() if not k.is_gaia]
        # Parity target: the bonus count of the completed player kingdoms.
        counts = [
            sum(
                1
                for territory in all_territories
                if territory.owner_kingdom_id == k.id and self._map_cadastre(territory.map_key)
            )
            for k in player_kingdoms
            if any(territory.owner_kingdom_id == k.id for territory in all_territories)
        ]
        bonus_target = min(counts) if counts else 0
        rng = random.Random(seed)  # noqa: S311 - game draw, not crypto
        rng.shuffle(pool)
        created = self._draw_for_kingdom(season, rng, pool, kingdom_id, per_kingdom, bonus_target)
        for territory in created:
            await self._store.upsert_territory(territory.to_mongo())
        logger.info("kingdoms: top-up draw — %s territories for kingdom %s", len(created), kingdom_id)
        return created

    @staticmethod
    def _count_by_kingdom(territories: list[TerritoryModel]) -> dict[str, int]:
        """Territory count per kingdom id."""
        counts: dict[str, int] = {}
        for territory in territories:
            owner = territory.owner_kingdom_id
            counts[owner] = counts.get(owner, 0) + 1
        return counts

    def _map_cadastre(self, map_key: str) -> bool:
        """True when the catalog entry carries cadastre effects (a bonus map)."""
        entry = next((entry for entry in self._config.maps if entry.key == map_key), None)
        return entry is not None and bool(entry.cadastre)

    async def draw_map_for(self, owner_kingdom_id: str, map_key: str) -> TerritoryModel:
        """Draw one specific allowed map as a territory (T5/T7 helper).

        Refuses out maps (§8) and maps outside the admin catalog; used
        by the Lord's Day Gaia draw, the exploration reward and the
        Explorateur technology.
        """
        season = await self._require_season()
        catalog = {entry.key for entry in self._config.maps}
        if map_key not in catalog:
            raise TerritoryNotFoundError("this map is not in the allowed catalog")
        if map_key in await self.drawn_map_keys():
            raise MapPoolExhaustedError("this map is already out for the season", missing=0)
        territory = self._new_territory(season, map_key, owner_kingdom_id)
        await self._store.upsert_territory(territory.to_mongo())
        logger.info("kingdoms: map %s drawn for %s", map_key, owner_kingdom_id)
        return territory

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
