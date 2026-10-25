"""Kingdoms mod chef-mercenaire-v1 — unit tests (Drasah 24/10/2026).

The Chef Mercenaire auction engine behind an in-memory store: the
market opens inside the REAL 20h-21h Paris window (or forced by the
beta console), draws five lots from the catalogue minus the married
civilizations, escrows the bids (ONE lot at a time — a new bid moves
the old one), extends the close under the anti-snipe rule (capped at
the hard close), awards the highest bidder at the close, refunds the
losers, consumes one use per draft and strips the holders of a
civilization acquired by marriage (fixed 1-écu refund).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.config import (
    CivilizationCondition,
    KingdomsSeasonConfig,
    MercenarySettings,
    default_season_config,
)
from kingdoms.mods.kingdoms.mercenary import (
    MercenaryAlreadyHeldError,
    MercenaryBidTooLowError,
    MercenaryInsufficientPurseError,
    MercenaryLotNotFoundError,
    MercenaryMarketClosedError,
    MercenaryNotOpenYetError,
    MercenaryService,
    MercenaryWindowError,
)
from kingdoms.mods.kingdoms.models import LordRole

from .test_kingdoms_attacks import Bundle as _AttackBundle

_OPEN = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
"""Tuesday 20h00 Paris (CEST, UTC+2) — the Chief's window opens."""

_BID = _OPEN + timedelta(minutes=16)
"""Past the 20h15 bids opening."""

_CLOSE = _OPEN + timedelta(minutes=31)
"""Past the base 20h30 close."""

_POOL = ("britons", "franks", "goths", "huns", "mongols", "persians", "teutons")


def _config() -> KingdomsSeasonConfig:
    """The default config carrying a small civilization catalogue."""
    config = default_season_config()
    config.civilizations = tuple(
        CivilizationCondition(key=key, display_name=key.title()) for key in _POOL
    )
    return config


class Bundle(_AttackBundle):
    """The attack bundle extended with the Chef Mercenaire service."""

    def __init__(self, config: KingdomsSeasonConfig | None = None) -> None:
        super().__init__(config)
        self.mercenary = MercenaryService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]


async def _launched() -> Bundle:
    bundle = Bundle(_config())
    await bundle.kingdoms.launch()
    await bundle.kingdoms.enroll("king-a", "King A", LordRole.KING, proposed_name="Aquitaine")
    await bundle.kingdoms.enroll("lord-a", "Lord A", LordRole.LORD, kingdom_name="Aquitaine")
    return bundle


def test_the_default_settings_carry_drasahs_frozen_values() -> None:
    settings = MercenarySettings()
    assert settings.enabled is True
    assert settings.window_start_hour == 20
    assert settings.window_end_hour == 21
    assert settings.bids_open_minutes == 15
    assert settings.base_close_minutes == 30
    assert settings.hard_close_minutes == 45
    assert settings.pool_size == 5
    assert settings.min_uses == 1
    assert settings.max_uses == 3
    assert settings.starting_bid == 1
    assert settings.antisnipe_trigger_minutes == 2
    assert settings.antisnipe_extension_minutes == 1
    assert settings.marriage_refund_ecus == 1


async def test_open_draws_five_unmarried_lots_with_visible_uses() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN)
    assert len(doc["lots"]) == 5
    civs = [lot["civ"] for lot in doc["lots"]]
    assert len(set(civs)) == 5
    assert all(1 <= int(lot["uses"]) <= 3 for lot in doc["lots"])
    assert doc["bids_open_at"] == doc["opened_at"] + 15 * 60
    assert doc["closes_at"] == doc["opened_at"] + 30 * 60
    assert doc["hard_close_at"] == doc["opened_at"] + 45 * 60
    assert doc["closed"] is False


async def test_the_pool_excludes_the_married_civilizations() -> None:
    bundle = await _launched()
    lord = await bundle.mercenary.lord("lord-a")
    lord.married_civilization = "britons"
    await bundle.store.upsert_lord(lord.to_mongo())
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civs = [lot["civ"] for lot in doc["lots"]]
    assert "britons" not in civs


async def test_the_window_is_real_paris_time_and_force_bypasses_it() -> None:
    bundle = await _launched()
    with pytest.raises(MercenaryWindowError):
        await bundle.mercenary.open_market(now=_OPEN - timedelta(hours=3))
    doc = await bundle.mercenary.open_market(now=_OPEN - timedelta(hours=3), force=True)
    assert doc["forced"] is True


async def test_opening_is_idempotent_while_the_market_lives() -> None:
    bundle = await _launched()
    first = await bundle.mercenary.open_market(now=_OPEN)
    second = await bundle.mercenary.open_market(now=_OPEN + timedelta(minutes=5))
    assert first["id"] == second["id"]


async def test_bids_escrow_move_with_one_lot_and_refund_on_withdraw() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civ = doc["lots"][0]["civ"]
    other = doc["lots"][1]["civ"]
    with pytest.raises(MercenaryNotOpenYetError):
        await bundle.mercenary.place_bid("lord-a", civ, 1, now=_OPEN + timedelta(minutes=5))
    _, lord = await bundle.mercenary.place_bid("lord-a", civ, 1, now=_BID)
    assert lord.purse == 9
    with pytest.raises(MercenaryBidTooLowError):
        await bundle.mercenary.place_bid("king-a", civ, 1, now=_BID)
    _, king = await bundle.mercenary.place_bid("king-a", civ, 3, now=_BID)
    assert king.purse == 7
    # ONE lot at a time: lord-a MOVES to the other lot — the 1 écu is back.
    _, lord = await bundle.mercenary.place_bid("lord-a", other, 4, now=_BID)
    assert lord.purse == 6
    market = await bundle.mercenary.market()
    assert market is not None
    first_lot = bundle.mercenary.lot(market, civ)
    assert first_lot is not None
    assert {bid["lord"] for bid in first_lot["bids"]} == {"king-a"}
    with pytest.raises(MercenaryLotNotFoundError):
        await bundle.mercenary.place_bid("lord-a", "incas", 2, now=_BID)
    # the withdrawal refunds the escrow — the SECOND highest stands alone.
    withdrawn = await bundle.mercenary.withdraw_bid("king-a", now=_BID)
    assert withdrawn is not None
    assert (await bundle.mercenary.lord("king-a")).purse == 10


async def test_a_cold_purse_refuses_the_bid_without_touching_it() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civ = doc["lots"][0]["civ"]
    lord = await bundle.mercenary.lord("lord-a")
    lord.purse = 1
    await bundle.store.upsert_lord(lord.to_mongo())
    with pytest.raises(MercenaryInsufficientPurseError):
        await bundle.mercenary.place_bid("lord-a", civ, 2, now=_BID)
    reloaded = await bundle.mercenary.lord("lord-a")
    assert reloaded.purse == 1


async def test_an_antisnipe_bid_extends_the_close_capped_at_the_hard_close() -> None:
    config = _config()
    config.tavern.purse_start = 50
    bundle = Bundle(config)
    await bundle.kingdoms.launch()
    await bundle.kingdoms.enroll("king-a", "King A", LordRole.KING, proposed_name="A")
    await bundle.kingdoms.enroll("lord-a", "Lord A", LordRole.LORD, kingdom_name="A")
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civ = doc["lots"][0]["civ"]
    base_close = int(doc["closes_at"])
    # a bid inside the last two minutes pushes the close +1 minute.
    doc, _ = await bundle.mercenary.place_bid("lord-a", civ, 1, now=_OPEN + timedelta(minutes=29))
    assert int(doc["closes_at"]) == base_close + 60
    # repeated sniping is capped at the HARD close (20h45).
    bidders = ("king-a", "lord-a")
    minute = 30
    amount = 2
    while int(doc["closes_at"]) < int(doc["hard_close_at"]):
        doc, _ = await bundle.mercenary.place_bid(
            bidders[minute % 2],
            civ,
            amount,
            now=_OPEN + timedelta(minutes=minute),
        )
        amount += 1
        minute += 1
    assert int(doc["closes_at"]) == int(doc["hard_close_at"])


async def test_the_close_awards_the_highest_and_refunds_the_losers() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civ = doc["lots"][0]["civ"]
    quiet = doc["lots"][1]["civ"]
    uses = int(doc["lots"][0]["uses"])
    await bundle.mercenary.place_bid("lord-a", civ, 1, now=_BID)
    await bundle.mercenary.place_bid("king-a", civ, 3, now=_BID)
    # before the close nothing happens — the replay is idempotent.
    assert await bundle.mercenary.close_if_due(now=_OPEN + timedelta(minutes=25)) == []
    awards = await bundle.mercenary.close_if_due(now=_CLOSE)
    assert awards == [{"lord_id": "king-a", "civ": civ, "uses": uses}]
    assert await bundle.mercenary.close_if_due(now=_CLOSE) == []
    king = await bundle.mercenary.lord("king-a")
    assert king.mercenary_civilization == civ
    assert king.mercenary_uses_left == uses
    assert king.purse == 7
    lord = await bundle.mercenary.lord("lord-a")
    assert lord.mercenary_civilization is None
    assert lord.purse == 10
    market = await bundle.mercenary.market()
    assert market is not None
    assert market["closed"] is True
    quiet_lot = bundle.mercenary.lot(market, quiet)
    assert quiet_lot is not None
    assert quiet_lot["winner"] is None
    with pytest.raises(MercenaryMarketClosedError):
        await bundle.mercenary.place_bid("lord-a", quiet, 1, now=_CLOSE)


async def test_force_close_awards_at_once_outside_the_close_timestamp() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN, force=True)
    civ = doc["lots"][0]["civ"]
    uses = int(doc["lots"][0]["uses"])
    await bundle.mercenary.place_bid("lord-a", civ, 2, now=_OPEN + timedelta(minutes=16))
    # the beta console closes the LIVE market at once, whatever the close says.
    awards = await bundle.mercenary.close_if_due(
        now=_OPEN + timedelta(minutes=20), force=True
    )
    assert awards == [{"lord_id": "lord-a", "civ": civ, "uses": uses}]
    market = await bundle.mercenary.market()
    assert market is not None
    assert market["closed"] is True
    # replay (with or without force) stays idempotent.
    assert await bundle.mercenary.close_if_due(now=_OPEN + timedelta(minutes=21), force=True) == []


async def test_one_draft_consumes_one_use_and_the_civ_disappears() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civ = doc["lots"][0]["civ"]
    uses = int(doc["lots"][0]["uses"])
    await bundle.mercenary.place_bid("lord-a", civ, 1, now=_BID)
    await bundle.mercenary.close_if_due(now=_CLOSE)
    for left in range(uses - 1, -1, -1):
        assert await bundle.mercenary.consume_draft_use("lord-a", civ) == left
    lord = await bundle.mercenary.lord("lord-a")
    assert lord.mercenary_civilization is None
    assert lord.mercenary_uses_left == 0
    assert await bundle.mercenary.consume_draft_use("lord-a", civ) is None
    # a hand civilization never passes here.
    assert await bundle.mercenary.consume_draft_use("lord-a", "franks") is None


async def test_a_married_civ_strips_the_holder_with_the_fixed_refund() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civ = doc["lots"][0]["civ"]
    await bundle.mercenary.place_bid("lord-a", civ, 2, now=_BID)
    await bundle.mercenary.close_if_due(now=_CLOSE)
    stripped = await bundle.mercenary.resolve_marriage_acquired(civ)
    assert len(stripped) == 1
    assert stripped[0][1] == civ
    lord = await bundle.mercenary.lord("lord-a")
    assert lord.mercenary_civilization is None
    assert lord.mercenary_uses_left == 0
    assert lord.purse == 9  # 10 - 2 (auction) + 1 (fixed refund)


async def test_a_holder_cannot_bid_while_he_holds_a_live_civ() -> None:
    bundle = await _launched()
    doc = await bundle.mercenary.open_market(now=_OPEN)
    civ = doc["lots"][0]["civ"]
    uses = int(doc["lots"][0]["uses"])
    await bundle.mercenary.place_bid("lord-a", civ, 1, now=_BID)
    await bundle.mercenary.close_if_due(now=_CLOSE)
    # the window still runs — a NEW market opens after the closed one.
    second_open = _OPEN + timedelta(minutes=50)
    second = await bundle.mercenary.open_market(now=second_open)
    assert second["id"] != doc["id"]
    bid_time = second_open + timedelta(minutes=16)
    other = second["lots"][0]["civ"]
    with pytest.raises(MercenaryAlreadyHeldError):
        await bundle.mercenary.place_bid("lord-a", other, 1, now=bid_time)
    # once the uses ran out the lord may buy again (the re-buy rule).
    for _ in range(uses):
        await bundle.mercenary.consume_draft_use("lord-a", civ)
    _, lord = await bundle.mercenary.place_bid("lord-a", other, 1, now=bid_time)
    assert lord.mercenary_civilization is None
    assert lord.mercenary_uses_left == 0
