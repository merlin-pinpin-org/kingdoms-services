"""Kingdoms mod diplomacy & marriages service (kingdoms-services#160, T6).

Reference §18-§19 and decisions D32-D34, D45, D51, D53, D59-D60,
D74: the civilization-conditions engine (CIVILIZATIONS.md as admin
data), the per-kingdom playable-civilization list recomputed at every
cycle end, and the marriages (standard and arranged) that secure a
civilization.

Alliances equal civilizations (D51): the recomputed list is what the
diplomacy surface displays; this service owns the rules only.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.service import (
    KingdomNotFoundError,
    KingdomsModError,
    NoSeasonError,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import (
        CivilizationCondition,
        KingdomsSeasonConfig,
    )
    from kingdoms.mods.kingdoms.models import KingdomModel, LordModel, SeasonState
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.diplomacy")


class MarriageError(KingdomsModError):
    """Base of the marriage-domain errors."""

    code = "KINGDOMS_MARRIAGE_ERROR"
    message_key = "kingdoms.errors.marriage_unexpected"


class AlreadyMarriedError(MarriageError):
    """Raised when a lord weds twice (D34: one marriage per lord)."""

    code = "KINGDOMS_ALREADY_MARRIED"
    message_key = "kingdoms.errors.already_married"


class MarriageCapacityError(MarriageError):
    """Raised when the kingdom exceeds its marriage capacity (D53)."""

    code = "KINGDOMS_MARRIAGE_CAPACITY"
    message_key = "kingdoms.errors.marriage_capacity"


class UnknownCivilizationError(MarriageError):
    """Raised when the civilization is not in the catalog."""

    code = "KINGDOMS_UNKNOWN_CIVILIZATION"
    message_key = "kingdoms.errors.unknown_civilization"


class MarriageExclusivityError(MarriageError):
    """Raised when an active marriage already claims the civilization (D74)."""

    code = "KINGDOMS_MARRIAGE_EXCLUSIVITY"
    message_key = "kingdoms.errors.marriage_exclusivity"


class DiplomacyService:
    """Civilization conditions and marriage rules (reference §18-§19)."""

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
    # Conditions engine (D32/D33)
    # ------------------------------------------------------------------
    async def recalculate(self) -> dict[str, list[str]]:
        """Recompute every kingdom's playable civilizations (cycle end).

        Civs come from the owned territories' cadastre, the unlock
        chains, the referential name rule and the marriages that secure
        them; a lost territory drops its civ on the next pass (D32).
        A civilization drawn by the starting draft that has NO
        acquisition condition yet (CIVILIZATIONS.md conditions come
        later) stays put — the draft survives the Lord's Day
        recalculation until the conditions are authored.
        Lords of a kingdom flagged for a combat loss lose their marriage
        first (D34/D53). Returns the new lists for the diplomacy screen.
        """
        season = await self._require_season()
        kingdoms = await self._kingdoms.kingdoms()
        lords = await self._kingdoms.lords()
        reports: dict[str, list[str]] = {}
        dropped = list(season.pending_marriage_losses)
        for kingdom in kingdoms:
            if kingdom.is_gaia:
                continue
            owned = await self._owned_map_keys(kingdom.id)
            unlocked: list[str] = [
                civ.key
                for civ in self._config.civilizations
                if civ.key in kingdom.civilizations and self._is_unconditioned(civ)
            ]
            for civ in self._config.civilizations:
                if self._condition_met(civ, kingdom, owned, unlocked):
                    if civ.key not in unlocked:
                        unlocked.append(civ.key)
            secured = [
                lord.married_civilization
                for lord in lords
                if lord.kingdom_id == kingdom.id
                and not lord.left
                and lord.married_civilization is not None
                and kingdom.id not in dropped
            ]
            for key in secured:
                if key not in unlocked:
                    unlocked.append(key)
            kingdom.civilizations = unlocked
            kingdom.secured_civilizations = [c for c in secured if c]
            await self._store.upsert_kingdom(kingdom.to_mongo())
            reports[kingdom.name] = unlocked
        if dropped:
            await self._apply_marriage_losses(lords, season, dropped)
        logger.info("kingdoms: alliances recomputed - %s", reports)
        return reports

    async def _apply_marriage_losses(
        self, lords: list[LordModel], season: SeasonState, dropped: list[str]
    ) -> None:
        """Drop the marriages of the defeated kingdoms (D34/D53)."""
        for lord in lords:
            if lord.kingdom_id in dropped and lord.married_civilization is not None:
                logger.info(
                    "kingdoms: %s loses the marriage %s (combat defeat)",
                    lord.id,
                    lord.married_civilization,
                )
                lord.married_civilization = None
                await self._store.upsert_lord(lord.to_mongo())
        season.pending_marriage_losses = []
        await self._store.upsert_season(season.to_mongo())

    def _is_unconditioned(self, civ: CivilizationCondition) -> bool:
        """Whether a catalog civ has no acquisition condition attached."""
        return not (
            civ.requires_map_keys
            or civ.requires_map_types
            or civ.requires_civilization is not None
            or civ.requires_kingdom_name_pattern is not None
        )

    def _condition_met(
        self,
        civ: CivilizationCondition,
        kingdom: KingdomModel,
        owned: list[str],
        unlocked: list[str],
    ) -> bool:
        """Evaluate one unlock condition (data, never code)."""
        if civ.requires_map_keys and any(key in owned for key in civ.requires_map_keys):
            return True
        if civ.requires_map_types and self._owns_map_type(kingdom.id, civ.requires_map_types):
            return True
        if civ.requires_civilization is not None:
            base = civ.requires_civilization
            return base in unlocked or base in kingdom.civilizations
        if civ.requires_kingdom_name_pattern is not None:
            return re.fullmatch(civ.requires_kingdom_name_pattern, kingdom.name) is not None
        return False

    async def _owns_map_type(self, kingdom_id: str, types: tuple[str, ...]) -> bool:
        """Whether the kingdom owns a territory of one of the map types."""
        territories = await self._territories.territories()
        for territory in territories:
            if territory.owner_kingdom_id != kingdom_id:
                continue
            entry = next(
                (item for item in self._config.maps if item.key == territory.map_key),
                None,
            )
            if entry is None:
                continue
            flags = entry.types.model_dump()
            if any(flags.get(name, False) for name in types):
                return True
        return False

    async def _owned_map_keys(self, kingdom_id: str) -> list[str]:
        """Return the map keys owned by the kingdom."""
        return [
            territory.map_key
            for territory in await self._territories.territories()
            if territory.owner_kingdom_id == kingdom_id
        ]

    # ------------------------------------------------------------------
    # Marriages (D34/D45/D53)
    # ------------------------------------------------------------------
    async def marry(
        self,
        player_id: str,
        civilization: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Marry a lord to a civilization (D34: one marriage per lord).

        The kingdom's capacity (grown by the epochs, D53) bounds the
        marriages; the civilization becomes secured for the kingdom.
        The exclusivity is common to every marriage (D74): a
        civilization already claimed by an active marriage - any
        kingdom - is never targeted twice. A fresh marriage locks the
        lord out of combat for 24 real hours (D59).
        """
        timestamp = now or datetime.now(tz=UTC)
        season = await self._require_season()
        del season
        self._check_civilization(civilization)
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None:
            raise KingdomNotFoundError("the player is not enrolled in the current season")
        if lord.married_civilization is not None:
            raise AlreadyMarriedError("a lord weds once per season (D34)")
        if lord.kingdom_id is None:
            raise MarriageCapacityError("the lord belongs to no kingdom")
        kingdom = await self._kingdom_by_id(lord.kingdom_id)
        married = await self._kingdom_marriages_async(kingdom.id)
        if married >= kingdom.marriage_capacity:
            raise MarriageCapacityError("the kingdom reached its marriage capacity")
        await self._check_exclusivity(civilization)
        await self._commit_marriage(lord, kingdom, civilization, hours=24, now=timestamp)
        logger.info("kingdoms: %s married %s", player_id, civilization)
        return civilization

    async def arranged_marriage(
        self,
        player_id: str,
        civilization: str,
        *,
        now: datetime | None = None,
        spend_points: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> str:
        """Buy an arranged marriage (3 techs, D60/D74): instant exclusivity.

        The exclusivity is instant - the civilization is reserved to
        the kingdom right away - and the marriage capacity does NOT
        bind (D74: the stock is never consumed); the one-marriage-per-
        lord rule still applies (D45: never bypassed). Every check
        runs before the payment so a refused marriage never debits the
        treasury. A fresh arranged marriage locks the lord out of
        combat for 6 real hours (D60). The caller passes the
        ``spend_points`` seam (the economy service) so the cost lands
        in the same transaction scope.
        """
        timestamp = now or datetime.now(tz=UTC)
        self._check_civilization(civilization)
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None:
            raise KingdomNotFoundError("the player is not enrolled in the current season")
        if lord.married_civilization is not None:
            raise AlreadyMarriedError("a lord weds once per season (D45: never bypassed)")
        if lord.kingdom_id is None or lord.left:
            raise MarriageCapacityError("the lord belongs to no kingdom")
        kingdom = await self._kingdom_by_id(lord.kingdom_id)
        await self._check_exclusivity(civilization)
        if spend_points is not None:
            await spend_points(player_id, self._config.technologies.mariage_arrange)
        await self._commit_marriage(lord, kingdom, civilization, hours=6, now=timestamp)
        logger.info("kingdoms: %s arranged-married %s", player_id, civilization)
        return civilization

    async def _check_exclusivity(self, civilization: str) -> None:
        """Refuse a civilization claimed by an active marriage (D74)."""
        for lord in await self._kingdoms.lords():
            if lord.married_civilization == civilization:
                raise MarriageExclusivityError(
                    f"an active marriage already claims {civilization} (D74)"
                )

    async def _commit_marriage(
        self,
        lord: LordModel,
        kingdom: KingdomModel,
        civilization: str,
        *,
        hours: int,
        now: datetime,
    ) -> None:
        """Persist one marriage: secured civ, playable list, combat lock."""
        lord.married_civilization = civilization
        lord.marriage_locked_until = int((now + timedelta(hours=hours)).timestamp())
        await self._store.upsert_lord(lord.to_mongo())
        if civilization not in kingdom.secured_civilizations:
            kingdom.secured_civilizations = [*kingdom.secured_civilizations, civilization]
        if civilization not in kingdom.civilizations:
            kingdom.civilizations = [*kingdom.civilizations, civilization]
        await self._store.upsert_kingdom(kingdom.to_mongo())

    async def record_defeat(self, kingdom_id: str) -> None:
        """Flag a combat defeat: the marriages drop at the next cycle (D34)."""
        season = await self._require_season()
        if kingdom_id not in season.pending_marriage_losses:
            season.pending_marriage_losses = [
                *season.pending_marriage_losses,
                kingdom_id,
            ]
            await self._store.upsert_season(season.to_mongo())
        logger.info("kingdoms: %s flagged for marriage loss at next cycle", kingdom_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _check_civilization(self, civilization: str) -> None:
        """Refuse civilizations outside the admin catalog."""
        if civilization not in {civ.key for civ in self._config.civilizations}:
            raise UnknownCivilizationError(f"unknown civilization: {civilization}")

    async def _kingdom_marriages_async(self, kingdom_id: str) -> int:
        """Count the active marriages of a kingdom."""
        return len(
            [
                lord
                for lord in await self._kingdoms.lords()
                if lord.kingdom_id == kingdom_id
                and not lord.left
                and lord.married_civilization is not None
            ]
        )

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
