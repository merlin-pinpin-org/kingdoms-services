"""The Lieu Sacrée / Cathédrale service (lieu-sacree-v1, Drasah 14/10/2026).

At the Cathédrale palier the King pays 3 ⚙️ and consecrates one of
his territories: a 72h cathedral CHANTIER is set on it. While it
runs, the territory resists EVERY adverse technology (Embuscade,
Corruption…) but stays attackable by CLASSIC attacks with unlimited
attempts — each one consumes its attack budget normally. A chantier
that survives its 72h makes the territory SACRED forever: immune to
everything. A victorious attacker destroys ONLY the chantier — the
territory stays with its defender, the attacker banks 2 ⚙️ and must
RE-ATTACK to conquer; the cathedral auto-rebuilds on another
territory of the defender, or is lost when none exists (repurchase
3 ⚙️).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.admin_debug import bypass_guard
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.models import KingdomModel, TerritoryModel
from kingdoms.mods.kingdoms.service import KING_ROLE, KingdomsModError, NoSeasonError
from kingdoms.mods.kingdoms.storage import KingdomsStore

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.cathedrals")


class CathedralError(KingdomsModError):
    """Base of the Lieu Sacrée errors."""

    code = "KINGDOMS_CATHEDRAL_ERROR"
    message_key = "kingdoms.errors.cathedral_unexpected"


class CathedralKingOnlyError(CathedralError):
    """Raised when a non-King tries to consecrate."""

    code = "KINGDOMS_CATHEDRAL_KING_ONLY"
    message_key = "kingdoms.errors.cathedral_king_only"


class CathedralTierError(CathedralError):
    """Raised below the Cathédrale palier."""

    code = "KINGDOMS_CATHEDRAL_TIER"
    message_key = "kingdoms.errors.cathedral_tier"


class CathedralNotFoundError(CathedralError):
    """Raised for a territory outside the King's kingdom."""

    code = "KINGDOMS_CATHEDRAL_NOT_FOUND"
    message_key = "kingdoms.errors.cathedral_not_found"


class CathedralActiveError(CathedralError):
    """Raised when the kingdom already runs a chantier or the territory is sacred."""

    code = "KINGDOMS_CATHEDRAL_ACTIVE"
    message_key = "kingdoms.errors.cathedral_active"


class CathedralInsufficientTechError(CathedralError):
    """Raised when the treasury cannot pay the 3 ⚙️."""

    code = "KINGDOMS_CATHEDRAL_INSUFFICIENT_TECH"
    message_key = "kingdoms.errors.cathedral_insufficient_tech"


class CathedralService:
    """Own the Lieu Sacrée consecration and its 72h verdict."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms: KingdomsService,
        territories: TerritoryService,
    ) -> None:
        self._store = store
        self._config = config
        self._kingdoms = kingdoms
        self._territories = territories

    # ------------------------------------------------------------------
    # Consecration (King only)
    # ------------------------------------------------------------------
    async def _kingdom_by_id(self, kingdom_id: str) -> KingdomModel:
        return next(k for k in await self._kingdoms.kingdoms() if k.id == kingdom_id)

    async def _owned_territory(self, kingdom_id: str, map_key: str) -> TerritoryModel | None:
        """Return the kingdom's territory of a map key, None when foreign."""
        return next(
            (
                item
                for item in await self._territories.territories()
                if item.map_key == map_key and item.owner_kingdom_id == kingdom_id
            ),
            None,
        )

    def _tier_ok(self, kingdom: KingdomModel) -> bool:
        tiers = self._config.parish.tiers
        return int(kingdom.parish_tier) >= len(tiers)

    async def consecrate(
        self,
        player_id: str,
        map_key: str,
        *,
        now: datetime | None = None,
    ) -> TerritoryModel:
        """Set a cathedral chantier on the King's territory (lieu-sacree-v1).

        The King pays the consecration price (3 ⚙️) from the tech
        bank; the territory carries the 72h chantier and resists
        every adverse technology until the verdict.
        """
        timestamp = now or game_now()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        season = await self._kingdoms.current_season()
        if season is None or season.finished:
            raise NoSeasonError("no season runs the Lieu Sacrée consecration")
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None or lord.role is not KING_ROLE or not lord.kingdom_id:
            raise CathedralKingOnlyError("only the King consecrates a Lieu Sacrée")
        kingdom = await self._kingdom_by_id(lord.kingdom_id)
        territory = await self._owned_territory(lord.kingdom_id, map_key)
        if territory is None:
            raise CathedralNotFoundError("the territory belongs to no land of this kingdom")
        if not self._tier_ok(kingdom) and not bypass_guard(
            f"cathedral tier of {kingdom.name} (parish tier {kingdom.parish_tier})"
        ):
            raise CathedralTierError("the kingdom did not reach the Cathédrale palier")
        if territory.sacred or territory.is_chantier_at(timestamp):
            raise CathedralActiveError("the territory already carries a Lieu Sacrée")
        others = await self.consecratable(lord.kingdom_id, now=timestamp)
        if map_key not in {item.map_key for item in others}:
            raise CathedralActiveError("the kingdom already runs a cathedral chantier")
        cost = self._config.parish.cathedral_cost
        if int(kingdom.tech_points_bank) < cost and not bypass_guard(
            f"cathedral consecration cost {cost} of {kingdom.name} "
            f"(bank {kingdom.tech_points_bank})"
        ):
            raise CathedralInsufficientTechError("the treasury cannot pay the consecration")
        # The bypassed path pays what the treasury holds (never negative).
        paid = min(int(kingdom.tech_points_bank), cost)
        kingdom.tech_points_bank = int(kingdom.tech_points_bank) - paid
        await self._store.upsert_kingdom(kingdom.to_mongo())
        territory.cathedral_until = timestamp + timedelta(
            hours=self._config.parish.cathedral_chantier_hours
        )
        await self._store.upsert_territory(territory.to_mongo())
        logger.info(
            "kingdoms: %s consecrated the Lieu Sacrée on %s (-%d tech)",
            lord.kingdom_id,
            map_key,
            cost,
        )
        return territory

    async def consecratable(
        self,
        kingdom_id: str,
        *,
        now: datetime | None = None,
    ) -> list[TerritoryModel]:
        """Return the kingdom's territories open to a consecration.

        A territory already SACRED or carrying a chantier is never
        consecratable; when the kingdom already runs a chantier
        elsewhere, NOTHING is (single Lieu Sacrée per kingdom).
        """
        timestamp = now or game_now()
        owned = [
            item
            for item in await self._territories.territories()
            if item.owner_kingdom_id == kingdom_id
        ]
        if any(item.sacred or item.is_chantier_at(timestamp) for item in owned):
            return []
        return [item for item in owned if not item.sacred]

    # ------------------------------------------------------------------
    # The 72h verdict
    # ------------------------------------------------------------------
    async def resolve_due(
        self,
        *,
        now: datetime | None = None,
    ) -> list[TerritoryModel]:
        """Turn every expired chantier into a SACRED territory, forever.

        The tick is idempotent: replaying it after the verdict
        changes nothing (the chantier is already consumed).
        """
        timestamp = now or game_now()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        season = await self._kingdoms.current_season()
        if season is None or season.finished:
            return []
        consecrated: list[TerritoryModel] = []
        for territory in await self._territories.territories():
            if not territory.sacred and territory.is_chantier_at(timestamp):
                continue  # the chantier still runs
            if territory.sacred or territory.cathedral_until is None:
                continue  # nothing pending
            if territory.cathedral_until > timestamp:
                continue
            territory.sacred = True
            territory.cathedral_until = None
            await self._store.upsert_territory(territory.to_mongo())
            consecrated.append(territory)
            logger.info(
                "kingdoms: the Lieu Sacrée of %s survived — SACRED forever",
                territory.map_key,
            )
        return consecrated
