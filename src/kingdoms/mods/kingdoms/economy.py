"""Kingdoms mod special-action economy service (kingdoms-services#161, T7).

Reference §20 and decisions D9/D15/D36/D47/D48/D58/D61: the kingdom
tech-point wallet (``tech_points_bank`` fed by the epochs and the
exploration rewards) and the three purchasable special actions built on
it - Explorateur (receive one random non-out territory, D48),
Corruption (steal any territory, protected 48 real hours, D58) and the
Garde Royale shield (24h, one active guard per kingdom, extendable at
a rising cost, D61). The wallet is single: every purchase debits the
kingdom bank, including combat technologies through the transfer seam
toward the T4 technology state.
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.service import (
    KingdomNotFoundError,
    KingdomsModError,
    NoSeasonError,
)
from kingdoms.mods.kingdoms.territories import (
    MapPoolExhaustedError,
    TerritoryNotFoundError,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.attacks import AttackService
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import KingdomModel, SeasonState, TerritoryModel
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.economy")

EXPLORATEUR = "explorateur"
CORRUPTION = "corruption"
GARDE_ROYALE = "garde_royale"
ROYAL_GUARD_HOURS = 24
ROYAL_GUARD_EXTENSION_HOURS = 3
CORRUPTION_PROTECTION_HOURS = 48
CORRUPTION_COMPENSATION_TECH = 1
GUARD_UNTIL_KEY = "garde_royale_until"
GUARD_EXTENSIONS_KEY = "garde_royale_extensions"


class EconomyError(KingdomsModError):
    """Base of the economy-domain errors."""

    code = "KINGDOMS_ECONOMY_ERROR"
    message_key = "kingdoms.errors.economy_unexpected"


class InsufficientPointsError(EconomyError):
    """Raised when the kingdom wallet cannot cover a purchase (D9)."""

    code = "KINGDOMS_INSUFFICIENT_POINTS"
    message_key = "kingdoms.errors.insufficient_points"


class EconomyLimitReachedError(EconomyError):
    """Raised when a consumable hits its per-season purchase limit (D36/D48)."""

    code = "KINGDOMS_ECONOMY_LIMIT_REACHED"
    message_key = "kingdoms.errors.economy_limit_reached"


class TerritoryProtectedError(EconomyError):
    """Raised when a protected territory resists corruption or attack (D15/D37/D47)."""

    code = "KINGDOMS_TERRITORY_PROTECTED"
    message_key = "kingdoms.errors.territory_protected"


class GuardAlreadyActiveError(EconomyError):
    """Raised when a kingdom buys a second guard while one is active (D61)."""

    code = "KINGDOMS_GUARD_ALREADY_ACTIVE"
    message_key = "kingdoms.errors.guard_already_active"


class EconomyService:
    """The tech-point wallet and the purchasable special actions (reference §20)."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
        territory_service: TerritoryService,
        attacks_service: AttackService,
    ) -> None:
        """Store the seams: persistence, config, kingdoms, territories, attacks."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._territories = territory_service
        self._attacks = attacks_service

    # ------------------------------------------------------------------
    # Wallet (D9/D36)
    # ------------------------------------------------------------------
    async def grant_tech_points(self, kingdom_id: str, amount: int) -> KingdomModel:
        """Credit the kingdom wallet - epochs, exploration, admin grants."""
        kingdom = await self._kingdom_by_id(kingdom_id)
        kingdom.tech_points_bank += amount
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms: %s granted %s tech points", kingdom_id, amount)
        return kingdom

    async def spend_points(self, kingdom_id: str, cost: int) -> None:
        """Debit the wallet - the seam the other services call (D45).

        Raises when the wallet cannot cover the cost; the caller keeps
        its own invariants on top (limits, state transitions).
        """
        kingdom = await self._kingdom_by_id(kingdom_id)
        if kingdom.tech_points_bank < cost:
            raise InsufficientPointsError("the kingdom lacks tech points")
        kingdom.tech_points_bank -= cost
        await self._store.upsert_kingdom(kingdom.to_mongo())

    async def wallet(self, kingdom_id: str) -> int:
        """Return the kingdom's current tech-point balance."""
        return (await self._kingdom_by_id(kingdom_id)).tech_points_bank

    async def buy_combat_technology(self, kingdom_id: str, technology: str) -> object:
        """Buy a combat technology through the single wallet (D9/D36).

        The cost moves from the kingdom bank into the T4 technology
        state, which then owns the purchase counters and limits; a
        refused purchase (limit reached) refunds the bank in full.
        """
        cost = int(getattr(self._config.technologies, technology))
        await self.spend_points(kingdom_id, cost)
        state = await self._attacks.technology_state(kingdom_id)
        state.tech_points += cost
        await self._store.upsert_technology(state.to_mongo())
        try:
            return await self._attacks.buy_technology(kingdom_id, technology)
        except KingdomsModError:
            refunded = await self._kingdom_by_id(kingdom_id)
            refunded.tech_points_bank += cost
            await self._store.upsert_kingdom(refunded.to_mongo())
            raise

    # ------------------------------------------------------------------
    # Explorateur (D48)
    # ------------------------------------------------------------------
    async def buy_explorateur(self, kingdom_id: str) -> TerritoryModel:
        """Receive one random non-out map as an immediate territory (D48).

        Consumable: once per season per kingdom. The map is drawn at
        random among the allowed catalog entries not out yet (§8) - the
        territory goes to the buyer right away.
        """
        await self._check_consumable(kingdom_id, EXPLORATEUR)
        await self.spend_points(kingdom_id, self._cost(EXPLORATEUR))
        drawn = await self._territories.drawn_map_keys()
        pool = [entry.key for entry in self._config.maps if entry.key not in drawn]
        if not pool:
            await self.grant_tech_points(kingdom_id, self._cost(EXPLORATEUR))
            raise MapPoolExhaustedError("no allowed map remains to explore", missing=1)
        map_key = random.choice(pool)  # noqa: S311 - game draw, not crypto
        try:
            territory = await self._territories.draw_map_for(kingdom_id, map_key)
        except KingdomsModError:
            await self.grant_tech_points(kingdom_id, self._cost(EXPLORATEUR))
            raise
        await self._count_purchase(kingdom_id, EXPLORATEUR)
        logger.info("kingdoms: %s explored the map %s", kingdom_id, map_key)
        return territory

    # ------------------------------------------------------------------
    # Corruption (D15/D37)
    # ------------------------------------------------------------------
    async def buy_corruption(
        self,
        kingdom_id: str,
        territory_id: str,
        *,
        now: datetime | None = None,
    ) -> TerritoryModel:
        """Steal any territory from another kingdom (D15/D58).

        The buyer takes ownership right away; the territory becomes
        incorruptible (and unattackable) for 48 real hours, and the
        former owner gains one tech point as compensation. A territory
        already protected resists the corruption; the per-season
        purchase limit applies (D58).
        """
        await self._check_consumable(kingdom_id, CORRUPTION)
        timestamp = now or datetime.now(tz=UTC)
        territories = await self._territories.territories()
        territory = next(
            (item for item in territories if item.id == territory_id), None
        )
        if territory is None:
            raise TerritoryNotFoundError("no territory with this id")
        if territory.owner_kingdom_id == kingdom_id:
            raise EconomyError("a kingdom cannot corrupt its own territory")
        if territory.is_protected_at(timestamp):
            raise TerritoryProtectedError("the territory is protected against corruption")
        await self.spend_points(kingdom_id, self._cost(CORRUPTION))
        former_owner = territory.owner_kingdom_id
        await self._territories.transfer(territory_id, kingdom_id)
        fresh = next(
            (item for item in await self._territories.territories() if item.id == territory_id),
            None,
        )
        if fresh is None:
            raise TerritoryNotFoundError("the territory vanished mid-corruption")
        fresh.protected_until = timestamp + timedelta(hours=CORRUPTION_PROTECTION_HOURS)
        await self._store.upsert_territory(fresh.to_mongo())
        if former_owner != kingdom_id:
            await self.grant_tech_points(former_owner, CORRUPTION_COMPENSATION_TECH)
        await self._count_purchase(kingdom_id, CORRUPTION)
        logger.info(
            "kingdoms: %s corrupted the territory %s (protected until %s)",
            kingdom_id,
            territory_id,
            fresh.protected_until.isoformat(),
        )
        return fresh

    # ------------------------------------------------------------------
    # Garde Royale (D47)
    # ------------------------------------------------------------------
    async def buy_royal_guard(
        self,
        kingdom_id: str,
        territory_id: str,
        *,
        now: datetime | None = None,
    ) -> TerritoryModel:
        """Shield one of the kingdom's territories for 24 hours (D47).

        No stacking: a territory already shielded cannot be shielded
        again while the effect lasts - it extends instead.
        """
        territory = await self._owned_territory(kingdom_id, territory_id)
        timestamp = now or datetime.now(tz=UTC)
        state = await self._attacks.technology_state(kingdom_id)
        active_until = state.purchases.get(GUARD_UNTIL_KEY, 0)
        if active_until > int(timestamp.timestamp()):
            raise GuardAlreadyActiveError("a royal guard already shields a kingdom territory")
        if territory.is_protected_at(timestamp):
            raise TerritoryProtectedError("the royal guard already shields this territory")
        await self.spend_points(kingdom_id, self._cost(GARDE_ROYALE))
        territory.protected_until = timestamp + timedelta(hours=ROYAL_GUARD_HOURS)
        state.purchases[GUARD_UNTIL_KEY] = int(territory.protected_until.timestamp())
        await self._store.upsert_technology(state.to_mongo())
        await self._store.upsert_territory(territory.to_mongo())
        await self._count_purchase(kingdom_id, GARDE_ROYALE)
        logger.info(
            "kingdoms: %s guards the territory %s until %s",
            kingdom_id,
            territory_id,
            territory.protected_until.isoformat(),
        )
        return territory

    async def extend_royal_guard(
        self,
        kingdom_id: str,
        territory_id: str,
        *,
        now: datetime | None = None,
    ) -> TerritoryModel:
        """Extend the active guard: each prolongation costs one more (D61).

        The first extension costs 1 tech point, the second 2, the third
        3 - each buys three more hours. Only the territory carrying the
        kingdom's active guard can be extended.
        """
        territory = await self._owned_territory(kingdom_id, territory_id)
        timestamp = now or datetime.now(tz=UTC)
        until = territory.protected_until
        if until is None or until <= timestamp:
            raise TerritoryProtectedError("no royal guard shields this territory")
        state = await self._attacks.technology_state(kingdom_id)
        if state.purchases.get(GUARD_UNTIL_KEY, 0) != int(until.timestamp()):
            raise TerritoryProtectedError("no royal guard shields this territory")
        extensions = state.purchases.get(GUARD_EXTENSIONS_KEY, 0)
        await self.spend_points(kingdom_id, 1 + extensions)
        territory.protected_until = until + timedelta(
            hours=ROYAL_GUARD_EXTENSION_HOURS
        )
        state.purchases[GUARD_UNTIL_KEY] = int(territory.protected_until.timestamp())
        state.purchases[GUARD_EXTENSIONS_KEY] = extensions + 1
        await self._store.upsert_technology(state.to_mongo())
        await self._store.upsert_territory(territory.to_mongo())
        logger.info(
            "kingdoms: %s extended the guard on %s until %s",
            kingdom_id,
            territory_id,
            territory.protected_until.isoformat(),
        )
        return territory

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _cost(self, action: str) -> int:
        """Return the configured cost of one special action (D9)."""
        return int(getattr(self._config.technologies, action))

    def _limit(self, action: str) -> int:
        """Return the configured per-season purchase limit (D36)."""
        return int(self._config.technologies.limits.get(action, 0))

    async def _check_consumable(self, kingdom_id: str, action: str) -> None:
        """Refuse a consumable purchase beyond its per-season limit (D36/D48)."""
        bought = (await self._attacks.technology_state(kingdom_id)).purchases.get(action, 0)
        limit = self._limit(action)
        if limit > 0 and bought >= limit:
            raise EconomyLimitReachedError("the per-season purchase limit is reached")

    async def _count_purchase(self, kingdom_id: str, action: str) -> None:
        """Record one purchase in the kingdom's technology counters."""
        state = await self._attacks.technology_state(kingdom_id)
        state.purchases[action] = state.purchases.get(action, 0) + 1
        await self._store.upsert_technology(state.to_mongo())

    async def _owned_territory(self, kingdom_id: str, territory_id: str) -> TerritoryModel:
        """Resolve a territory and require the kingdom to own it."""
        territory = next(
            (item for item in await self._territories.territories() if item.id == territory_id),
            None,
        )
        if territory is None:
            raise TerritoryNotFoundError("no territory with this id")
        if territory.owner_kingdom_id != kingdom_id:
            raise EconomyError("the kingdom does not own this territory")
        return territory

    async def _kingdom_by_id(self, kingdom_id: str) -> KingdomModel:
        """Resolve a kingdom by id."""
        kingdom = next(
            (item for item in await self._kingdoms.kingdoms() if item.id == kingdom_id),
            None,
        )
        if kingdom is None:
            raise KingdomNotFoundError("no kingdom with this id")
        return kingdom

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        return season
