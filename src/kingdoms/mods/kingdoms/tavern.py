"""Kingdoms Taverne service (taverne-v1, Drasah 24/10/2026) — tranche 1.

The Taverne is the lords' purse-and-drinks economy: every lord owns a
PERSONAL purse in écus (10 at enrollment, ``TavernSettings.purse_start``)
and spends it on drinks. Tranche 1 wires the pour itself — the price,
the combat lock and the GLOBAL 24h anti-spam cooldown:

- a drinking lord is locked out of combat exactly like the battle lock
  (``LordModel.tavern_locked_until`` — enforced by ``AttackService``
  on the declaration, the planning, the defense and the Embuscade);
- the cooldown separates two drinks whatever they are
  (``LordModel.tavern_next_drink_at``);
- the drinks (prices, locks, labels) are DATA in ``TavernSettings`` —
  this service reads the catalog, never hardcodes it.

The effects (🍺 rumor window, 🍷 next FFA hour, 🥃 forced map), the
Renseignement archive, the bets and the Mercenary Chief land with
their own tranches — the tranche-1 pour debits the purse and poses the
locks, nothing else.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.attacks import NotEnrolledError
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.models import LordModel
from kingdoms.mods.kingdoms.service import KingdomsModError, NoSeasonError

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, TavernDrink
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.tavern")


class TavernError(KingdomsModError):
    """Base of the Taverne errors (taverne-v1)."""

    code = "KINGDOMS_TAVERN_ERROR"
    message_key = "kingdoms.errors.tavern_unexpected"


class TavernSeasonClosedError(TavernError):
    """Raised when a pour is attempted on a finished season."""

    code = "KINGDOMS_TAVERN_SEASON_CLOSED"
    message_key = "kingdoms.errors.tavern_season_closed"


class TavernDrinkNotFoundError(TavernError):
    """Raised when the asked drink is not in the catalog."""

    code = "KINGDOMS_TAVERN_DRINK_NOT_FOUND"
    message_key = "kingdoms.errors.tavern_drink_not_found"


class TavernCooldownError(TavernError):
    """Raised inside the GLOBAL 24h cooldown between two drinks."""

    code = "KINGDOMS_TAVERN_COOLDOWN"
    message_key = "kingdoms.errors.tavern_cooldown"


class TavernInsufficientPurseError(TavernError):
    """Raised when the purse cannot afford the drink."""

    code = "KINGDOMS_TAVERN_INSUFFICIENT_PURSE"
    message_key = "kingdoms.errors.tavern_insufficient_purse"


class TavernService:
    """The purse-and-drinks economy over the lord documents."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms: KingdomsService,
    ) -> None:
        """Store the seams: the lord documents and the season config."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def lord(self, player_id: str) -> LordModel:
        """Return the player's ACTIVE lord document or raise."""
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None or lord.left or lord.kingdom_id is None:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        return lord

    async def purse(self, player_id: str) -> int:
        """Return the lord's purse in écus (10 by default)."""
        return int((await self.lord(player_id)).purse)

    def drink(self, key: str) -> TavernDrink:
        """Return the catalog entry of a drink key or raise."""
        entry = self._config.tavern.drink(key)
        if entry is None:
            raise TavernDrinkNotFoundError(f"unknown drink: {key}")
        return entry

    def is_locked(self, lord: LordModel, timestamp: datetime) -> bool:
        """Whether the drinking lord's combat lock is still running."""
        return (
            lord.tavern_locked_until is not None
            and lord.tavern_locked_until > int(timestamp.timestamp())
        )

    # ------------------------------------------------------------------
    # The pour (tranche 1)
    # ------------------------------------------------------------------

    async def buy_drink(
        self,
        player_id: str,
        drink_key: str,
        *,
        now: datetime | None = None,
    ) -> tuple[LordModel, TavernDrink]:
        """Pour one drink: debit the purse, pose the combat lock and the
        GLOBAL 24h cooldown.

        Tranche 1 semantics (Drasah 24/10): the price and the lock are
        the catalog's FIXED values; the cooldown is counted from the
        pour whatever the drink; a lord may drink while engaged (the
        drink only ADDS a combat lock) — but never inside the cooldown.
        """
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        if season.finished:
            raise TavernSeasonClosedError("the season is over — the Taverne is closed")
        lord = await self.lord(player_id)
        drink = self.drink(drink_key)
        timestamp = now or game_now()
        now_ts = int(timestamp.timestamp())
        if (
            lord.tavern_next_drink_at is not None
            and lord.tavern_next_drink_at > now_ts
            and not _bypass(f"tavern cooldown of {lord.display_name}")
        ):
            raise TavernCooldownError(
                "the GLOBAL 24h drink cooldown is still running",
                next_at=str(lord.tavern_next_drink_at),
            )
        if int(lord.purse) < int(drink.price):
            raise TavernInsufficientPurseError(
                "the purse cannot afford this drink",
                price=str(drink.price),
                purse=str(lord.purse),
            )
        lord.purse = int(lord.purse) - int(drink.price)
        lord.tavern_locked_until = now_ts + int(drink.lock_minutes) * 60
        lord.tavern_next_drink_at = now_ts + int(self._config.tavern.drink_cooldown_hours) * 3600
        await self._store.upsert_lord(lord.to_mongo())
        logger.info(
            "kingdoms: %s drank %s (purse %s, lock until %s)",
            player_id,
            drink.key,
            lord.purse,
            str(lord.tavern_locked_until),
        )
        return lord, drink


def _bypass(guard: str) -> bool:
    """The admin debug bypass seam (same guard as the combat locks)."""
    from kingdoms.mods.kingdoms.admin_debug import bypass_guard

    return bypass_guard(guard)
