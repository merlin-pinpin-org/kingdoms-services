"""Kingdoms mod special-action economy service (kingdoms-services#161, T7).

Reference §20 and decisions D9/D15/D36/D47/D48/D58/D61/D68: the kingdom
tech-point wallet (``tech_points_bank`` fed by the epochs and the
exploration rewards) and the purchasable special actions built on
it - Explorateur (receive one random non-out territory, D48),
Corruption (steal any territory, protected 48 real hours, D58), the
Garde Royale shield (24h, one active guard per kingdom, extendable at
a rising cost, D61) and Patrouille (a daily 2h no-aggression slot,
D68). The wallet is single: every purchase debits the kingdom bank,
including combat technologies through the transfer seam toward the T4
technology state.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.attacks import (
    PATROUILLE_SLOT_KEYS,
    patrouille_active_at,
)
from kingdoms.mods.kingdoms.events import active_lords_day_window
from kingdoms.mods.kingdoms.service import (
    KingdomNotFoundError,
    KingdomsModError,
    NoSeasonError,
)
from kingdoms.mods.kingdoms.territories import (
    TerritoryNotFoundError,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.attacks import AttackService
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import (
        KingdomModel,
        LordModel,
        SeasonState,
        TerritoryModel,
    )
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.economy")

EXPLORATEUR = "explorateur"
EXPLORATION_LOCK_HOURS = 48
CORRUPTION = "corruption"
GARDE_ROYALE = "garde_royale"
PATROUILLE = "patrouille"
PATROUILLE_HOURS = 2
ROYAL_GUARD_HOURS = 12
CORRUPTION_PROTECTION_HOURS = 48
CORRUPTION_COMPENSATION_TECH = 1
GARDE_ROYALE_STOCK = "garde_royale_stock"
"""remparts-v2: bought-but-unplaced royal-guard tokens (no hard limit)."""
GUARD_TERRITORY_PREFIX = "garde_royale_until:"
"""remparts-v2: per-territory guard marker — ``garde_royale_until:<tid>``."""
PATROUILLE_UNLOCK_PREFIX = "patrouille_unlock_"
"""remparts-v2: slot-unlock markers — slot 1 first, then slot 2."""


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
    """Raised when a guard would shield a map that is already guarded (remparts-v2)."""

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

    async def buy_arranged_marriage_token(self, kingdom_id: str) -> KingdomModel:
        """Buy one arranged-marriage token for the Paroisse (D60, 2026-10-11).

        The Marché no longer celebrates the marriage itself: the King
        buys a token here (cost = ``technologies.mariage_arrange``, 3
        🔬), the kingdom's ``arranged_marriage_stock`` grows by one and
        the token is consumed at the Paroisse button. Payment first,
        token second — a refused purchase never grants a token.
        """
        kingdom = await self._kingdom_by_id(kingdom_id)
        cost = self._cost("mariage_arrange")
        await self.spend_points(kingdom_id, cost)
        kingdom = await self._kingdom_by_id(kingdom_id)
        kingdom.arranged_marriage_stock += 1
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info(
            "kingdoms: %s bought an arranged-marriage token (%s techs, stock=%s)",
            kingdom_id,
            cost,
            kingdom.arranged_marriage_stock,
        )
        await self._record_purchase(kingdom_id, "mariage_arrange", "jeton 💰", cost)
        return kingdom

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
            result = await self._attacks.buy_technology(kingdom_id, technology)
        except KingdomsModError:
            refunded = await self._kingdom_by_id(kingdom_id)
            refunded.tech_points_bank += cost
            await self._store.upsert_kingdom(refunded.to_mongo())
            raise
        await self._record_purchase(kingdom_id, technology, "", cost)
        return result

    # ------------------------------------------------------------------
    # Explorateur (D48)
    # ------------------------------------------------------------------
    async def buy_explorateur(
        self,
        kingdom_id: str,
        lord_id: str,
        *,
        now: datetime | None = None,
    ) -> LordModel:
        """Send one lord exploring: 48h combat lock, map at the end (art. 6).

        marche-v2 art. 6 (Drasah 13/10): any ACTIVE lord of the kingdom
        may explore (not only the King). The chosen lord is locked for
        48h FIXED (never scaled by the Parish, unlike the marriage) —
        no attack, no defence — and the territory does not exist yet:
        the map is drawn when the scheduler resolves the expired
        exploration (``resolve_due_explorations``). Once per season
        per kingdom; an exhausted pool refunds at resolution.
        """
        await self._check_consumable(kingdom_id, EXPLORATEUR)
        timestamp = now or datetime.now(tz=UTC)
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == lord_id),
            None,
        )
        if lord is None or lord.left or lord.kingdom_id != kingdom_id:
            raise EconomyError("the lord is no active lord of this kingdom")
        if lord.exploration_locked_until is not None and lord.exploration_locked_until > int(
            timestamp.timestamp()
        ):
            raise EconomyError("the lord is already exploring")
        await self.spend_points(kingdom_id, self._cost(EXPLORATEUR))
        lord.exploration_locked_until = int(timestamp.timestamp()) + int(
            EXPLORATION_LOCK_HOURS * 3600
        )
        await self._store.upsert_lord(lord.to_mongo())
        await self._count_purchase(kingdom_id, EXPLORATEUR)
        logger.info(
            "kingdoms: %s of %s explores until %s",
            lord.id,
            kingdom_id,
            lord.exploration_locked_until,
        )
        await self._record_purchase(
            kingdom_id, EXPLORATEUR, f"lord {lord.id}", self._cost(EXPLORATEUR)
        )
        return lord

    async def resolve_due_explorations(
        self, *, now: datetime | None = None
    ) -> list[tuple[LordModel, TerritoryModel | None]]:
        """Resolve every expired exploration (the tick's engine, art. 6).

        When the 48h lock passes, the kingdom receives a brand-new map
        drawn from the catalog (never an out map) and the lord's lock
        clears. An exhausted pool refunds the purchase in full and
        still clears the lock — the pass is idempotent, a restart
        mid-exploration never draws twice.
        """
        timestamp = now or datetime.now(tz=UTC)
        now_ts = int(timestamp.timestamp())
        resolved: list[tuple[LordModel, TerritoryModel | None]] = []
        drawn = await self._territories.drawn_map_keys()
        for lord in await self._kingdoms.lords():
            until = int(lord.exploration_locked_until or 0)
            if until <= 0 or until > now_ts:
                continue
            kingdom_id = lord.kingdom_id or ""
            pool = [
                entry.key
                for entry in self._config.maps
                if entry.key not in drawn
            ]
            if not pool:
                await self.grant_tech_points(kingdom_id, self._cost(EXPLORATEUR))
                logger.info(
                    "kingdoms: exploration of %s refunded (pool exhausted)", lord.id
                )
                resolved.append((lord, None))
            else:
                map_key = random.choice(pool)  # noqa: S311 - game draw, not crypto
                territory = await self._territories.draw_map_for(kingdom_id, map_key)
                drawn.add(map_key)
                logger.info(
                    "kingdoms: the exploration of %s revealed the map %s",
                    lord.id,
                    map_key,
                )
                resolved.append((lord, territory))
            lord.exploration_locked_until = None
            await self._store.upsert_lord(lord.to_mongo())
        return resolved

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
        # lieu-sacree-v1: a cathedral chantier resists every adverse
        # technology — Corruption included; a SACRED territory too.
        if territory.sacred or territory.is_tech_protected_at(timestamp):
            raise TerritoryProtectedError(
                "the territory resists the corruption (Lieu Sacrée, lieu-sacree-v1)"
            )
        # D68: the target kingdom's daily patrol resists the steal
        # (3h once Renforcée, marche-v2 art. 5).
        if patrouille_active_at(
            await self._attacks.technology_state(territory.owner_kingdom_id),
            timestamp,
            base_level=await self._attacks.base_ai_level(),
        ):
            raise TerritoryProtectedError(
                "the target kingdom's patrol protects its territories (D68)"
            )
        # Volière Lâcher de faucon (voliere-v1): the Corruption is an
        # adverse technology effect — a released victim is immune.
        victim = next(
            (k for k in await self._kingdoms.kingdoms() if k.id == territory.owner_kingdom_id),
            None,
        )
        if victim is not None and int(
            getattr(victim, "faucon_release_until", 0) or 0
        ) > int(timestamp.timestamp()):
            raise TerritoryProtectedError(
                "the victim released the faucon — immune to technologies (voliere-v1)"
            )
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
        await self._record_purchase(
            kingdom_id, CORRUPTION, f"map {fresh.map_key}", self._cost(CORRUPTION)
        )
        return fresh

    # ------------------------------------------------------------------
    # Garde Royale (remparts-v2, Drasah 14/10: guards in stock)
    # ------------------------------------------------------------------
    async def buy_royal_guard(self, kingdom_id: str, *, now: datetime | None = None) -> int:
        """Buy ONE royal-guard token at the market (remparts-v2).

        Drasah's ruling breaks the Check 43 single-guard rule: a
        kingdom may hold SEVERAL guards at once, with NO hard limit
        (« ça coûte cher quand même » — 2 ⚙️ each). The market sells
        the token; the King POSSES it from the 🧱 Remparts salon
        (``place_royal_guard``) on the map of his choice.
        """
        del now  # the token carries no expiry — only its placement does
        await self.spend_points(kingdom_id, self._cost(GARDE_ROYALE))
        state = await self._attacks.technology_state(kingdom_id)
        stock = int(state.purchases.get(GARDE_ROYALE_STOCK, 0) or 0) + 1
        state.purchases[GARDE_ROYALE_STOCK] = stock
        await self._store.upsert_technology(state.to_mongo())
        logger.info("kingdoms: %s stocks a royal guard (%d in reserve)", kingdom_id, stock)
        await self._record_purchase(
            kingdom_id, GARDE_ROYALE, "en réserve", self._cost(GARDE_ROYALE)
        )
        return stock

    async def place_royal_guard(
        self,
        kingdom_id: str,
        territory_id: str,
        *,
        now: datetime | None = None,
    ) -> TerritoryModel:
        """Spend one guard token: shield ONE owned map for 12 hours.

        remparts-v2: several guards may fly simultaneously (one per
        map — a map already shielded takes no second guard, no
        extension), each for its own 12h window. Not retroactive:
        attacks declared before the placement stay valid.
        """
        timestamp = now or datetime.now(tz=UTC)
        territory = await self._owned_territory(kingdom_id, territory_id)
        state = await self._attacks.technology_state(kingdom_id)
        stock = int(state.purchases.get(GARDE_ROYALE_STOCK, 0) or 0)
        if stock <= 0:
            raise EconomyError(
                "no royal guard in stock — buy one at the market first (remparts-v2)"
            )
        guard_key = f"{GUARD_TERRITORY_PREFIX}{territory.id}"
        if int(state.purchases.get(guard_key, 0) or 0) > int(timestamp.timestamp()):
            raise GuardAlreadyActiveError("a royal guard already shields this territory")
        state.purchases[GARDE_ROYALE_STOCK] = stock - 1
        territory.protected_until = timestamp + timedelta(hours=ROYAL_GUARD_HOURS)
        state.purchases[guard_key] = int(territory.protected_until.timestamp())
        await self._store.upsert_technology(state.to_mongo())
        await self._store.upsert_territory(territory.to_mongo())
        logger.info(
            "kingdoms: %s places a royal guard on %s until %s (%d left in stock)",
            kingdom_id,
            territory_id,
            territory.protected_until.isoformat(),
            stock - 1,
        )
        return territory

    # ------------------------------------------------------------------
    # Patrouille (remparts-v2, Drasah 14/10: slots unlocked, then placed)
    # ------------------------------------------------------------------
    async def buy_patrouille_slot(self, kingdom_id: str, slot_no: int) -> int:
        """Unlock ONE patrol slot at the market (slot 1, then slot 2).

        remparts-v2: the patrol is a SKILL, not a consumable — no more
        per-season purchase limit. The market sells the slot UNLOCK
        (2 ⚙️ each, slot 1 before slot 2); the King POSSES the daily
        window from the 🧱 Remparts salon (``place_patrouille``).
        """
        if slot_no not in (1, 2):
            raise EconomyError("the patrol slot number must be 1 or 2")
        state = await self._attacks.technology_state(kingdom_id)
        unlock_key = f"{PATROUILLE_UNLOCK_PREFIX}{slot_no}"
        if unlock_key in state.purchases:
            raise EconomyError(f"the patrol slot {slot_no} is already unlocked")
        if slot_no == 2 and PATROUILLE_UNLOCK_PREFIX + "1" not in state.purchases:
            raise EconomyError("the patrol slot 1 must be unlocked first")
        await self.spend_points(kingdom_id, self._cost(PATROUILLE))
        state.purchases[unlock_key] = 1
        await self._store.upsert_technology(state.to_mongo())
        logger.info("kingdoms: %s unlocks the patrol slot %d", kingdom_id, slot_no)
        await self._record_purchase(
            kingdom_id, PATROUILLE, f"slot {slot_no} débloqué", self._cost(PATROUILLE)
        )
        return slot_no

    async def place_patrouille(
        self,
        kingdom_id: str,
        slot_no: int,
        slot_start: int,
        *,
        now: datetime | None = None,
    ) -> int:
        """Place (or redefine) one unlocked patrol slot's daily window.

        The window is a 2h tranche applied EVERY day and stays fixed
        for the whole cycle; redefinition is possible ONLY during the
        Jour du Seigneur (Sunday 23:30 → Monday 10:00, Paris). The
        FIRST placement of a freshly unlocked slot is free of that
        constraint — an empty slot has nothing to redefine.
        """
        if slot_no not in (1, 2):
            raise EconomyError("the patrol slot number must be 1 or 2")
        if slot_start not in range(0, 24, PATROUILLE_HOURS):
            raise EconomyError("the patrol slot must start on the 2h grid (0, 2, … 22)")
        timestamp = now or datetime.now(tz=UTC)
        state = await self._attacks.technology_state(kingdom_id)
        unlock_key = f"{PATROUILLE_UNLOCK_PREFIX}{slot_no}"
        if unlock_key not in state.purchases:
            raise EconomyError(f"the patrol slot {slot_no} is not unlocked (market first)")
        slot_key = PATROUILLE_SLOT_KEYS[slot_no - 1]
        if slot_key in state.purchases:
            season = await self._kingdoms.current_season()
            if season is None:
                raise EconomyError("no season is running")
            if active_lords_day_window(self._config, season, timestamp) is None:
                raise EconomyError(
                    "the patrol window is fixed — it changes only on the Lord's Day"
                )
        other_slots = [
            value
            for key, value in state.purchases.items()
            if key in PATROUILLE_SLOT_KEYS and key != slot_key
        ]
        if slot_start in other_slots:
            raise EconomyError("another patrol slot already covers this tranche")
        state.purchases[slot_key] = slot_start
        await self._store.upsert_technology(state.to_mongo())
        logger.info(
            "kingdoms: %s places the patrol slot %d at %02dh", kingdom_id, slot_no, slot_start
        )
        return slot_start

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Chambre des comptes (marche-v2, 2026-10-13)
    # ------------------------------------------------------------------
    async def _record_purchase(self, kingdom_id: str, item: str, detail: str, cost: int) -> None:
        """Append one dated entry to the kingdom's purchase ledger.

        Best-effort: a ledger failure never breaks a successful
        purchase — the registry is the consultative Chambre des
        comptes history, not an accounting invariant.
        """
        try:
            kingdom = await self._kingdom_by_id(kingdom_id)
            registry = list(getattr(kingdom, "market_registry", None) or [])
            registry.append(
                {"item": str(item), "detail": str(detail)[:100], "cost": int(cost), "at": int(time.time())}
            )
            kingdom.market_registry = registry[-50:]
            await self._store.upsert_kingdom(kingdom.to_mongo())
        except Exception:
            logger.warning("kingdoms: ledger write failed for %s", kingdom_id, exc_info=True)

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
