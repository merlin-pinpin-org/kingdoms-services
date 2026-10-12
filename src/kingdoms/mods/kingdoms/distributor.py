"""Kingdoms mod distributor service — the admin test injector (D75).

Drasah's Distributeur salon (2026-10-12): during tests the admin hands
elements to a kingdom by hand and watches them propagate into the
game's live views. This service owns the injection rules — the panel
(``kingdom_distributor_panel.py``) is only its Discord surface.

Two design rules decided with Drasah:
1. **No costs, no limits, no locks** — the distributor injects
   straight into the season data (a test wallet of 50 🔬 never went
   through the epoch economy). The ONLY business rules kept are the
   ones Drasah spelled out for the alliances:
   - any civilization of the catalog may be handed to a kingdom
     (player kingdoms only — Gaïa never holds a hand, D27);
   - refused when the kingdom already holds it in Alliances;
   - refused when an active marriage (any kingdom) claims it (D74) —
     a NON-married civilization may legally be shared by several
     kingdoms (the admin draft ignores the draft's no-duplicate rule
     on purpose);
   - removing a civilization secured by one of the kingdom's own
     marriages is refused (the marriage survives, not the admin's
     hindsight).
2. **Everything is reversible** — every injection has its symmetric
   removal, so a test scenario can be unmade without a snapshot.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.service import KingdomNotFoundError, KingdomsModError
from kingdoms.mods.kingdoms.territories import TerritoryNotFoundError

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.attacks import AttackService
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import KingdomModel, LordModel, TerritoryModel
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.distributor")

SHIELD_HOURS = 48
"""The distributor's default territory shield (Corruption-like, D58)."""

LOCK_HOURS = 6
"""The distributor's default marriage lock window."""


class DistributorError(KingdomsModError):
    """Base of the distributor-domain errors."""

    code = "KINGDOMS_DISTRIBUTOR_ERROR"
    message_key = "kingdoms.errors.distributor"


class AllianceAlreadyInHandError(DistributorError):
    """Raised when the kingdom already holds the civilization."""

    code = "KINGDOMS_ALLIANCE_IN_HAND"
    message_key = "kingdoms.errors.alliance_in_hand"


class AllianceNotInHandError(DistributorError):
    """Raised when removing a civilization the kingdom does not hold."""

    code = "KINGDOMS_ALLIANCE_NOT_IN_HAND"
    message_key = "kingdoms.errors.alliance_not_in_hand"


class AllianceMarriedError(DistributorError):
    """Raised when an active marriage (any kingdom) claims the civilization."""

    code = "KINGDOMS_ALLIANCE_MARRIED"
    message_key = "kingdoms.errors.alliance_married"


class AllianceSecuredError(DistributorError):
    """Raised when one of the kingdom's own marriages secures the civ."""

    code = "KINGDOMS_ALLIANCE_SECURED"
    message_key = "kingdoms.errors.alliance_secured"


class GaiaAllianceError(DistributorError):
    """Raised when handing a civilization to Gaïa (no hand, D27)."""

    code = "KINGDOMS_GAIA_ALLIANCE"
    message_key = "kingdoms.errors.gaia_alliance"


class DistributorValueError(DistributorError):
    """Raised when an injected value is out of bounds."""

    code = "KINGDOMS_DISTRIBUTOR_BAD_VALUE"
    message_key = "kingdoms.errors.distributor_bad_value"


class LordNotFoundError(DistributorError):
    """Raised when the lord id matches no active lord of the season."""

    code = "KINGDOMS_DISTRIBUTOR_LORD_NOT_FOUND"
    message_key = "kingdoms.errors.distributor_lord_not_found"


class DistributorService:
    """The admin injection rules of the Distributeur salon (D75)."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
        territory_service: TerritoryService,
        attacks_service: AttackService,
    ) -> None:
        """Store the seams: persistence, config, and the read services."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._territories = territory_service
        self._attacks = attacks_service

    # ------------------------------------------------------------------
    # Lookups (the panel reads everything through these — no raw store)
    # ------------------------------------------------------------------
    async def kingdom(self, kingdom_id: str) -> KingdomModel:
        """Return the fresh kingdom document (KingdomNotFoundError otherwise)."""
        for kingdom in await self._kingdoms.kingdoms():
            if str(kingdom.id) == str(kingdom_id):
                return kingdom
        raise KingdomNotFoundError(f"no kingdom with id {kingdom_id} in the current season")

    async def list_kingdoms(self) -> list[KingdomModel]:
        """Return the season's kingdoms (the panel's kingdom pickers read this)."""
        return list(await self._kingdoms.kingdoms())

    async def lord(self, lord_id: str) -> LordModel:
        """Return the fresh lord document (LordNotFoundError otherwise)."""
        lord = next(
            (item for item in await self._kingdoms.lords() if str(item.id) == str(lord_id)),
            None,
        )
        if lord is None:
            raise LordNotFoundError(f"no lord with id {lord_id} in the current season")
        return lord

    async def married_civilizations(self) -> dict[str, str]:
        """Map every married civilization to the lord that claims it (D74)."""
        return {
            str(lord.married_civilization): str(lord.display_name)
            for lord in await self._kingdoms.lords()
            if lord.married_civilization is not None and not lord.left
        }

    async def civilization_options(self, kingdom_id: str) -> list[dict[str, str]]:
        """One row per catalog civilization: its state for THIS kingdom.

        The panel renders the whole catalog (the admin sees everything,
        nothing hidden): ``status`` reads ``free`` (injectable),
        ``hand`` (already held) or ``married`` (claimed by a marriage).
        """
        kingdom = await self.kingdom(kingdom_id)
        married = await self.married_civilizations()
        rows: list[dict[str, str]] = []
        for civ in self._config.civilizations:
            status = "free"
            detail = ""
            if civ.key in kingdom.civilizations:
                status = "hand"
            elif civ.key in married:
                status = "married"
                detail = married[civ.key]
            rows.append(
                {
                    "key": civ.key,
                    "display_name": civ.display_name,
                    "status": status,
                    "detail": detail,
                }
            )
        return rows

    async def available_maps(self) -> list[tuple[str, str]]:
        """List the catalog maps never drawn this season (attributable)."""
        drawn = await self._territories.drawn_map_keys()
        return [
            (entry.key, entry.display_name)
            for entry in self._config.maps
            if entry.key not in drawn
        ]

    async def territories_of(self, kingdom_id: str) -> list[TerritoryModel]:
        """Return the kingdom's territories (map_key + shield state)."""
        return [
            territory
            for territory in await self._territories.territories()
            if str(territory.owner_kingdom_id) == str(kingdom_id)
        ]

    async def overview(self, kingdom_id: str) -> dict[str, object]:
        """Build the compact status block of the 🧾 Statut view."""
        kingdom = await self.kingdom(kingdom_id)
        lords = [
            lord
            for lord in await self._kingdoms.lords()
            if str(lord.kingdom_id) == str(kingdom_id) and not lord.left
        ]
        territories = await self.territories_of(kingdom_id)
        state = await self._attacks.technology_state(str(kingdom_id))
        tier = self._config.parish.tier(kingdom.parish_tier)
        return {
            "kingdom": kingdom,
            "tier": tier,
            "lords": lords,
            "territories": territories,
            "purchases": dict(state.purchases),
        }

    # ------------------------------------------------------------------
    # Alliances (Drasah's 2026-10-12 rules — see the module docstring)
    # ------------------------------------------------------------------
    async def add_alliance(self, kingdom_id: str, civilization: str) -> KingdomModel:
        """Hand one catalog civilization to a kingdom's Alliances hand.

        Any catalog civilization is injectable (the acquisition
        conditions of later slices do not bind the admin) EXCEPT the
        two Drasah rules: never a duplicate of the kingdom's own hand,
        never a civilization claimed by an active marriage (D74). A
        civilization held by ANOTHER kingdom stays injectable — the
        non-married hand may be shared across kingdoms.
        """
        kingdom = await self.kingdom(kingdom_id)
        if kingdom.is_gaia:
            raise GaiaAllianceError("Gaïa holds no Alliances hand (D27)")
        if civilization not in {civ.key for civ in self._config.civilizations}:
            raise DistributorValueError(f"unknown civilization: {civilization}")
        if civilization in kingdom.civilizations:
            raise AllianceAlreadyInHandError(f"{civilization} is already in the hand")
        married = await self.married_civilizations()
        if civilization in married:
            raise AllianceMarriedError(
                f"{civilization} is claimed by the marriage of {married[civilization]} (D74)"
            )
        kingdom.civilizations = [*kingdom.civilizations, civilization]
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms distributor: %s +alliance %s", kingdom_id, civilization)
        return kingdom

    async def remove_alliance(self, kingdom_id: str, civilization: str) -> KingdomModel:
        """Remove one civilization from the kingdom's hand.

        Refused when the civilization is secured by one of the kingdom's
        own marriages — the marriage survives the admin's hindsight.
        """
        kingdom = await self.kingdom(kingdom_id)
        if civilization not in kingdom.civilizations:
            raise AllianceNotInHandError(f"{civilization} is not in the hand")
        if civilization in kingdom.secured_civilizations:
            raise AllianceSecuredError(
                f"{civilization} is secured by a marriage of this kingdom"
            )
        kingdom.civilizations = [
            civ for civ in kingdom.civilizations if civ != civilization
        ]
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms distributor: %s -alliance %s", kingdom_id, civilization)
        return kingdom

    # ------------------------------------------------------------------
    # Wallet & Paroisse (direct writes — no cost, no limit)
    # ------------------------------------------------------------------
    async def set_tech_points(self, kingdom_id: str, value: int) -> KingdomModel:
        """Set the kingdom's tech-point treasury (trésor 🔬)."""
        return await self._set_kingdom_int(kingdom_id, "tech_points_bank", value)

    async def set_marriage_points(self, kingdom_id: str, value: int) -> KingdomModel:
        """Set the kingdom's marriage-point stock (D59)."""
        return await self._set_kingdom_int(kingdom_id, "marriage_points", value)

    async def set_arranged_tokens(self, kingdom_id: str, value: int) -> KingdomModel:
        """Set the kingdom's arranged-marriage token stock (D60)."""
        return await self._set_kingdom_int(kingdom_id, "arranged_marriage_stock", value)

    async def set_parish_tier(self, kingdom_id: str, tier: int) -> KingdomModel:
        """Force the Paroisse palier (1 Chapelle · 2 Église · 3 Cathédrale)."""
        if int(tier) < 1 or int(tier) > len(self._config.parish.tiers):
            raise DistributorValueError(f"palier out of bounds: {tier}")
        return await self._set_kingdom_int(kingdom_id, "parish_tier", int(tier))

    async def _set_kingdom_int(
        self, kingdom_id: str, field: str, value: int
    ) -> KingdomModel:
        """Write one non-negative integer field of the kingdom document."""
        if int(value) < 0:
            raise DistributorValueError(f"{field} must be >= 0")
        kingdom = await self.kingdom(kingdom_id)
        setattr(kingdom, field, int(value))
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms distributor: %s %s=%s", kingdom_id, field, value)
        return kingdom

    # ------------------------------------------------------------------
    # Territories (draw / transfer / retire / replace / shield)
    # ------------------------------------------------------------------
    async def give_map(self, kingdom_id: str, map_key: str) -> TerritoryModel:
        """Draw one specific catalog map as a territory of the kingdom."""
        territory = await self._territories.draw_map_for(str(kingdom_id), map_key)
        logger.info("kingdoms distributor: %s +map %s", kingdom_id, map_key)
        return territory

    async def transfer_territory(
        self, territory_id: str, to_kingdom_id: str
    ) -> TerritoryModel:
        """Transfer one territory to another kingdom (idempotent)."""
        territory = await self._territories.transfer(str(territory_id), str(to_kingdom_id))
        logger.info(
            "kingdoms distributor: territory %s transferred to %s",
            territory_id,
            to_kingdom_id,
        )
        return territory

    async def retire_territory(self, map_key: str) -> TerritoryModel:
        """Remove a territory from the board — the map goes back to nothing.

        The drawn map is marked ``out`` for the whole season (§8) and
        the territory document is deleted: the admin un-attributes an
        element without a replacement draw (the Distributeur's
        symmetric removal of ``give_map``).
        """
        territory = next(
            (
                item
                for item in await self._territories.territories()
                if item.map_key == map_key
            ),
            None,
        )
        if territory is None:
            raise TerritoryNotFoundError("this map is not a drawn territory")
        season = await self._kingdoms.current_season()
        if season is not None and map_key not in season.out_maps:
            season.out_maps = [*season.out_maps, map_key]
            await self._store.upsert_season(season.to_mongo())
        await self._store.delete_territory(territory.id)
        logger.info("kingdoms distributor: map %s retired", map_key)
        return territory

    async def replace_territory(self, map_key: str) -> TerritoryModel:
        """Eject the drawn map — the owner receives a fresh random one."""
        territory = await self._territories.eject(map_key)
        logger.info("kingdoms distributor: map %s ejected", map_key)
        return territory

    async def shield_territory(self, map_key: str, hours: int) -> TerritoryModel:
        """Set (or clear, hours <= 0) a territory's anti-attack shield."""
        territory = next(
            (
                item
                for item in await self._territories.territories()
                if item.map_key == map_key
            ),
            None,
        )
        if territory is None:
            raise TerritoryNotFoundError("this map is not a drawn territory")
        if hours > 0:
            territory.protected_until = datetime.now(tz=UTC) + timedelta(hours=hours)
        else:
            territory.protected_until = None
        await self._store.upsert_territory(territory.to_mongo())
        logger.info(
            "kingdoms distributor: map %s shield=%sh", map_key, hours if hours > 0 else 0
        )
        return territory

    # ------------------------------------------------------------------
    # Lords (budgets & marriage lock)
    # ------------------------------------------------------------------
    async def reset_budgets(self, lord_id: str) -> LordModel:
        """Recharge the lord's weekly attack/defense budgets."""
        lord = await self.lord(lord_id)
        lord.attack_used = 0
        lord.defense_used = 0
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms distributor: %s budgets recharged", lord_id)
        return lord

    async def set_marriage_lock(self, lord_id: str, hours: int | None) -> LordModel:
        """Set (or clear, None/0) the lord's marriage combat lock."""
        lord = await self.lord(lord_id)
        if hours is None or hours <= 0:
            lord.marriage_locked_until = None
        else:
            lord.marriage_locked_until = int(
                (datetime.now(tz=UTC) + timedelta(hours=hours)).timestamp()
            )
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms distributor: %s marriage lock=%s", lord_id, hours)
        return lord

    # ------------------------------------------------------------------
    # Combat technologies (forced counters — no points, no limits)
    # ------------------------------------------------------------------
    async def set_technology_count(
        self, kingdom_id: str, technology: str, count: int
    ) -> int:
        """Force a combat technology's purchase counter (>= 0).

        ``count=0`` removes the purchase record entirely. The tech
        points are never touched — the distributor injects the EFFECT,
        not the purchase.
        """
        if count < 0:
            raise DistributorValueError("the technology count must be >= 0")
        state = await self._attacks.technology_state(str(kingdom_id))
        if count == 0:
            state.purchases.pop(technology, None)
        else:
            state.purchases[technology] = count
        await self._store.upsert_technology(state.to_mongo())
        logger.info(
            "kingdoms distributor: %s tech %s=%s", kingdom_id, technology, count
        )
        return count
