"""Kingdoms Chef Mercenaire service (chef-mercenaire-v1, Drasah 24/10).

The Chef Mercenaire is the Taverne's evening auction: between 20h00
and 21h00 REAL Paris time (never the offset game clock) the Chief
offers FIVE civilizations drawn from the catalogue minus the married
ones — each carrying a random, VISIBLE number of draft uses (1-3).
The bids open at 20h15, the base close sits at 20h30, an anti-snipe
bid inside the last two minutes pushes the close one minute further
and the HARD close is 20h45; the results stay displayed until the
Chief leaves at 21h00.

Rules frozen by Drasah (24/10 evening, taverne-v1-spec):
- pool = catalogue HORS civs mariées — the duplicates with the
  kingdoms' HANDS are allowed (draft starter, conquête, volière);
- the window is REAL wall-clock Paris on a classic season — in beta
  a console button calls the Chief at ANY moment (`force=True`
  skips the window check and draws a fresh market at once);
- a mercenary civilization is SABOTABLE like any hand civilization;
- ONE civilization per lord at a time, starting bid 1 écu, ONE lot
  at a time (a bid on a new lot MOVES the previous one — the écus
  are re-injected by design), the withdrawal refunds, and when the
  highest bidder withdraws the SECOND highest takes the lot;
- one DRAFT consumes one use — the civilization disappears when the
  uses run out;
- a civilization acquired by MARRIAGE strips the holder of his
  mercenary copy and refunds him a FIXED 1 écu (config).

Tranche 1 of the patch: THIS module is the engine — the market state
rides the season document (`SeasonState.mercenary_market`) exactly
like the Corbeau theft rides the kingdom (plain data, restart-safe).
The Discord surface (the Chief's taverne panel with the per-lot bid
buttons), the daily scheduler, the beta console command and the
draft/Alliances wiring land with the next tranches.
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from kingdoms.mods.kingdoms.attacks import NotEnrolledError
from kingdoms.mods.kingdoms.service import KingdomsModError, NoSeasonError

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, MercenarySettings
    from kingdoms.mods.kingdoms.models import LordModel
    from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.mercenary")

DEFAULT_TZ = "Europe/Paris"
"""The Chief's wall clock — the window is REAL Paris time (Drasah
24/10), never the offset game clock."""

_TZ = ZoneInfo(DEFAULT_TZ)
_rng = random.SystemRandom()


class MercenaryError(KingdomsModError):
    """Base of the Chef Mercenaire errors (chef-mercenaire-v1)."""

    code = "KINGDOMS_MERCENARY_ERROR"
    message_key = "kingdoms.errors.mercenary_unexpected"


class MercenaryDisabledError(MercenaryError):
    """Raised when the Chef Mercenaire is disabled in the config."""

    code = "KINGDOMS_MERCENARY_DISABLED"
    message_key = "kingdoms.errors.mercenary_disabled"


class MercenarySeasonClosedError(MercenaryError):
    """Raised when the season is over — the Chief is gone."""

    code = "KINGDOMS_MERCENARY_SEASON_CLOSED"
    message_key = "kingdoms.errors.mercenary_season_closed"


class MercenaryWindowError(MercenaryError):
    """Raised when the Chief is called outside his 20h-21h window."""

    code = "KINGDOMS_MERCENARY_WINDOW"
    message_key = "kingdoms.errors.mercenary_window"


class MercenaryNoMarketError(MercenaryError):
    """Raised when no live market exists (nothing was drawn)."""

    code = "KINGDOMS_MERCENARY_NO_MARKET"
    message_key = "kingdoms.errors.mercenary_no_market"


class MercenaryNotOpenYetError(MercenaryError):
    """Raised when the bids are placed before the 20h15 opening."""

    code = "KINGDOMS_MERCENARY_NOT_OPEN_YET"
    message_key = "kingdoms.errors.mercenary_not_open_yet"


class MercenaryMarketClosedError(MercenaryError):
    """Raised when the market is closed (base/hard close passed)."""

    code = "KINGDOMS_MERCENARY_CLOSED"
    message_key = "kingdoms.errors.mercenary_closed"


class MercenaryLotNotFoundError(MercenaryError):
    """Raised when the asked civilization is not in today's pool."""

    code = "KINGDOMS_MERCENARY_LOT_NOT_FOUND"
    message_key = "kingdoms.errors.mercenary_lot_not_found"


class MercenaryBidTooLowError(MercenaryError):
    """Raised when the bid does not strictly exceed the highest."""

    code = "KINGDOMS_MERCENARY_BID_TOO_LOW"
    message_key = "kingdoms.errors.mercenary_bid_too_low"


class MercenaryInsufficientPurseError(MercenaryError):
    """Raised when the purse cannot cover the escrowed bid."""

    code = "KINGDOMS_MERCENARY_INSUFFICIENT_PURSE"
    message_key = "kingdoms.errors.mercenary_insufficient_purse"


class MercenaryAlreadyHeldError(MercenaryError):
    """Raised when the lord already holds a live mercenary civ."""

    code = "KINGDOMS_MERCENARY_ALREADY_HELD"
    message_key = "kingdoms.errors.mercenary_already_held"


class MercenaryService:
    """The Chef Mercenaire auction engine over the season document."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms: Any,
    ) -> None:
        """Store the seams: the season documents and the config."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @property
    def settings(self) -> MercenarySettings:
        """Return the Chef Mercenaire config section."""
        return self._config.mercenary

    async def market(self) -> dict[str, Any] | None:
        """Return the live-or-closed market document (None when absent)."""
        season = await self._kingdoms.current_season()
        if season is None:
            return None
        doc = season.mercenary_market
        return dict(doc) if doc else None

    @staticmethod
    def lot(doc: dict[str, Any], civ_key: str) -> dict[str, Any] | None:
        """Return the market's lot of a civilization (None when absent)."""
        return next(
            (lot for lot in doc.get("lots", []) if str(lot.get("civ")) == civ_key),
            None,
        )

    async def lord(self, player_id: str) -> LordModel:
        """Return the player's ACTIVE lord document or raise."""
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None or lord.left or lord.kingdom_id is None:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        return lord

    # ------------------------------------------------------------------
    # The market
    # ------------------------------------------------------------------

    def inside_window(self, moment: datetime) -> bool:
        """Whether the REAL Paris wall clock sits inside the window."""
        local = moment.astimezone(_TZ)
        return int(self.settings.window_start_hour) <= local.hour < int(
            self.settings.window_end_hour
        )

    async def open_market(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Open (or read back) the market — idempotent while it lives.

        The window guard is REAL Paris time; `force` (the beta
        console button) skips it and opens at any moment. A closed
        market never blocks a new one — the Chief sells again.
        """
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        if season.finished:
            raise MercenarySeasonClosedError("the season is over — the Chief is gone")
        settings = self.settings
        if not settings.enabled and not force:
            raise MercenaryDisabledError("the Chef Mercenaire is disabled")
        moment = now or datetime.now(UTC)
        existing = season.mercenary_market
        if existing and not existing.get("closed", False):
            return dict(existing)
        if not force and not self.inside_window(moment):
            raise MercenaryWindowError("the Chief only stands 20h-21h Paris")
        married = {
            lord.married_civilization
            for lord in await self._kingdoms.lords()
            if not lord.left and lord.married_civilization
        }
        pool = [civ.key for civ in self._config.civilizations if civ.key not in married]
        drawn = _rng.sample(pool, min(int(settings.pool_size), len(pool)))
        lots: list[dict[str, Any]] = [
            {
                "civ": civ,
                "uses": _rng.randint(int(settings.min_uses), int(settings.max_uses)),
                "bids": [],
                "winner": None,
            }
            for civ in drawn
        ]
        opened = int(moment.timestamp())
        local = moment.astimezone(_TZ)
        doc: dict[str, Any] = {
            "id": "m-" + local.strftime("%Y%m%d-%H%M%S"),
            "date": local.strftime("%Y-%m-%d"),
            "forced": bool(force),
            "opened_at": opened,
            "bids_open_at": opened + int(settings.bids_open_minutes) * 60,
            "closes_at": opened + int(settings.base_close_minutes) * 60,
            "hard_close_at": opened + int(settings.hard_close_minutes) * 60,
            "closed": False,
            "closed_at": None,
            "lots": lots,
        }
        season.mercenary_market = doc
        await self._store.upsert_season(season.to_mongo())
        logger.info(
            "kingdoms: mercenary market %s opened (%s lots)", doc["id"], len(lots)
        )
        return dict(doc)

    def _require_live(
        self, doc: dict[str, Any] | None, now_ts: int, *, for_bid: bool
    ) -> dict[str, Any]:
        """Validate the market against the live-bid rules or raise."""
        if doc is None:
            raise MercenaryNoMarketError("no live market — the Chief is not here")
        if doc.get("closed", False):
            raise MercenaryMarketClosedError("the market is closed")
        if for_bid:
            if now_ts < int(doc["bids_open_at"]):
                raise MercenaryNotOpenYetError("the bids open at 20h15")
            if now_ts >= int(doc["closes_at"]):
                raise MercenaryMarketClosedError("the market is closed")
        return doc

    @staticmethod
    def _own_bid(
        doc: dict[str, Any], lord_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Return the (lot, bid) a lord currently holds, None when none."""
        for lot in doc.get("lots", []):
            bid = next(
                (b for b in lot.get("bids", []) if str(b.get("lord")) == lord_id),
                None,
            )
            if bid is not None:
                return lot, bid
        return None

    async def place_bid(
        self,
        lord_id: str,
        civ_key: str,
        amount: int,
        *,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], LordModel]:
        """Escrow one bid — ONE lot at a time (a new bid MOVES the old).

        The escrow debits the purse at once; a moved or withdrawn bid
        refunds it (Drasah: « les écus sont réinjectables sur une autre
        civ »). A bid strictly exceeds the current highest; inside the
        last two minutes it pushes the close +1 minute, capped at the
        HARD close.
        """
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        moment = now or datetime.now(UTC)
        now_ts = int(moment.timestamp())
        doc = self._require_live(season.mercenary_market, now_ts, for_bid=True)
        lot = self.lot(doc, civ_key)
        if lot is None:
            raise MercenaryLotNotFoundError("no lot for civilization " + str(civ_key))
        lord = await self.lord(lord_id)
        if lord.mercenary_civilization and int(lord.mercenary_uses_left) > 0:
            raise MercenaryAlreadyHeldError(
                "one mercenary civilization per lord at a time",
                civ=str(lord.mercenary_civilization),
            )
        settings = self.settings
        value = int(amount)
        highest = max(
            (int(bid["amount"]) for bid in lot.get("bids", [])), default=0
        )
        if value < int(settings.starting_bid) or value <= highest:
            raise MercenaryBidTooLowError(
                "the bid must strictly exceed the current highest",
                current=str(highest),
                starting=str(settings.starting_bid),
            )
        own = self._own_bid(doc, lord_id)
        available = int(lord.purse) + (int(own[1]["amount"]) if own else 0)
        if value > available:
            raise MercenaryInsufficientPurseError(
                "the purse cannot cover the escrowed bid",
                price=str(value),
                purse=str(lord.purse),
            )
        if own is not None:
            # ONE lot at a time: the previous bid re-enters the purse.
            own[0]["bids"] = [
                bid for bid in own[0].get("bids", []) if str(bid.get("lord")) != lord_id
            ]
            lord.purse += int(own[1]["amount"])
        lot["bids"] = [*lot.get("bids", []), {"lord": lord_id, "amount": value}]
        lord.purse -= value
        closes = int(doc["closes_at"])
        if closes - now_ts <= int(settings.antisnipe_trigger_minutes) * 60:
            doc["closes_at"] = min(
                closes + int(settings.antisnipe_extension_minutes) * 60,
                int(doc["hard_close_at"]),
            )
        season.mercenary_market = doc
        await self._store.upsert_lord(lord.to_mongo())
        await self._store.upsert_season(season.to_mongo())
        logger.info(
            "kingdoms: %s bid %s écu(s) on %s (market %s)",
            lord_id,
            value,
            civ_key,
            doc["id"],
        )
        return dict(doc), lord

    async def withdraw_bid(
        self, lord_id: str, *, now: datetime | None = None
    ) -> tuple[dict[str, Any], LordModel] | None:
        """Withdraw the lord's bid and refund the escrow (None when no bid).

        The SECOND highest naturally takes the lot — the close always
        awards the current highest, whatever left the table before.
        """
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        moment = now or datetime.now(UTC)
        doc = self._require_live(
            season.mercenary_market, int(moment.timestamp()), for_bid=False
        )
        own = self._own_bid(doc, lord_id)
        if own is None:
            return None
        lot, bid = own
        lot["bids"] = [
            item for item in lot.get("bids", []) if str(item.get("lord")) != lord_id
        ]
        lord = await self.lord(lord_id)
        lord.purse += int(bid["amount"])
        season.mercenary_market = doc
        await self._store.upsert_lord(lord.to_mongo())
        await self._store.upsert_season(season.to_mongo())
        logger.info(
            "kingdoms: %s withdrew his bid on %s (market %s)",
            lord_id,
            lot["civ"],
            doc["id"],
        )
        return dict(doc), lord

    async def close_if_due(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Close the due market: award the lots, refund every loser.

        Idempotent and replay-safe: a closed (or absent) market returns
        an empty list. A winner holds ONE civ with its uses; a lord who
        left the season meanwhile is refunded instead of awarded.
        """
        season = await self._kingdoms.current_season()
        if season is None:
            return []
        doc = season.mercenary_market
        if not doc or doc.get("closed", False):
            return []
        moment = now or datetime.now(UTC)
        now_ts = int(moment.timestamp())
        if now_ts < int(doc["closes_at"]):
            return []
        lords = {lord.id: lord for lord in await self._kingdoms.lords()}
        awards: list[dict[str, Any]] = []
        refunds: dict[str, int] = {}
        touched: set[str] = set()
        for lot in doc.get("lots", []):
            bids = sorted(
                lot.get("bids", []),
                key=lambda bid: int(bid.get("amount", 0)),
                reverse=True,
            )
            winner_id: str | None = None
            for bid in bids:
                lord = lords.get(str(bid.get("lord")))
                if lord is None or lord.left or lord.kingdom_id is None:
                    refunds[str(bid.get("lord"))] = (
                        refunds.get(str(bid.get("lord")), 0) + int(bid.get("amount", 0))
                    )
                    continue
                winner_id = str(bid.get("lord"))
                break
            lot["winner"] = winner_id
            if winner_id is None:
                for bid in bids:
                    refunds[str(bid.get("lord"))] = (
                        refunds.get(str(bid.get("lord")), 0) + int(bid.get("amount", 0))
                    )
                continue
            winner = lords[winner_id]
            winner.mercenary_civilization = str(lot["civ"])
            winner.mercenary_uses_left = int(lot["uses"])
            touched.add(winner.id)
            awards.append(
                {"lord_id": winner.id, "civ": str(lot["civ"]), "uses": int(lot["uses"])}
            )
            for bid in bids:
                if str(bid.get("lord")) != winner_id:
                    refunds[str(bid.get("lord"))] = (
                        refunds.get(str(bid.get("lord")), 0) + int(bid.get("amount", 0))
                    )
        for lord_id, amount in refunds.items():
            lord = lords.get(lord_id)
            if lord is not None:
                lord.purse += amount
                touched.add(lord.id)
        doc["closed"] = True
        doc["closed_at"] = now_ts
        season.mercenary_market = doc
        await self._store.upsert_season(season.to_mongo())
        for lord_id in touched:
            await self._store.upsert_lord(lords[lord_id].to_mongo())
        if awards:
            logger.info(
                "kingdoms: mercenary market %s closed — %s award(s)",
                doc["id"],
                len(awards),
            )
        return awards

    # ------------------------------------------------------------------
    # The holdings
    # ------------------------------------------------------------------

    async def consume_draft_use(self, lord_id: str, civ_key: str) -> int | None:
        """Consume ONE draft use of the lord's mercenary civilization.

        Returns the uses left after the consumption, None when the
        lord holds no mercenary copy of the asked civilization (the
        hand civilizations never pass here).
        """
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == lord_id),
            None,
        )
        if (
            lord is None
            or lord.mercenary_civilization != civ_key
            or int(lord.mercenary_uses_left) <= 0
        ):
            return None
        lord.mercenary_uses_left -= 1
        if int(lord.mercenary_uses_left) <= 0:
            lord.mercenary_civilization = None
            lord.mercenary_uses_left = 0
        await self._store.upsert_lord(lord.to_mongo())
        logger.info(
            "kingdoms: %s consumed a draft use of %s (%s left)",
            lord_id,
            civ_key,
            lord.mercenary_uses_left,
        )
        return int(lord.mercenary_uses_left)

    async def resolve_marriage_acquired(
        self, civ_key: str
    ) -> list[tuple[LordModel, str]]:
        """Strip every holder of a civ acquired by MARRIAGE — fixed refund.

        Drasah 24/10: the marriage strips the mercenary holder of his
        copy and refunds him a FIXED 1 écu (config
        `marriage_refund_ecus`, whatever he paid at auction). The
        surface turns each returned (lord, civ) into the « Remboursement
        de 1 écu » DM.
        """
        stripped: list[tuple[LordModel, str]] = []
        for lord in await self._kingdoms.lords():
            if lord.mercenary_civilization == civ_key:
                lord.mercenary_civilization = None
                lord.mercenary_uses_left = 0
                lord.purse += int(self.settings.marriage_refund_ecus)
                await self._store.upsert_lord(lord.to_mongo())
                stripped.append((lord, civ_key))
        if stripped:
            logger.info(
                "kingdoms: %s mercenary holder(s) of %s stripped by a marriage",
                len(stripped),
                civ_key,
            )
        return stripped
