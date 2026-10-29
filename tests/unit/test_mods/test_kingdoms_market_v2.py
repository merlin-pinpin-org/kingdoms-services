"""Kingdoms Marché v2 tests: article cards, single echo and ledger.

The cartes-tcg-v2 spec (Drasah 26/10): every purchase flows through
the revealed ephemeral CARD (effect, cost, live treasury, per-season
remainder + ✅ Acheter / ❌ Annuler), the echo after the purchase is
ONE ephemeral note with the enriched errors, and every kingdom can
open its 🏛️ Chambre des comptes from the Le-Royaume panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import discord

from kingdoms.mods.kingdoms.economy import (
    EconomyLimitReachedError,
    InsufficientPointsError,
)
from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomMarketCancelButton,
    KingdomMarketLedgerButton,
    KingdomMarketTechButton,
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import MockInteraction, MockUser


@dataclass
class FakeLord:
    id: str
    role: str
    kingdom_id: str | None = None
    left: bool = False


@dataclass
class FakeKingdom:
    id: str
    name: str = "Merlinia"
    tech_points_bank: int = 7
    market_registry: list[dict[str, Any]] = field(default_factory=list)


class FakeKingdomsService:
    """KingdomsService stand-in: lords + kingdom docs with a registry."""

    def __init__(self, lords: list[FakeLord], kingdoms: list[FakeKingdom]) -> None:
        self._lords = lords
        self._kingdoms = kingdoms

    config = type("FakeSeasonConfig", (), {"technologies": None})()

    async def lords(self) -> list[FakeLord]:
        return list(self._lords)

    async def kingdoms(self) -> list[FakeKingdom]:
        return list(self._kingdoms)


class FakeEconomyService:
    """EconomyService stand-in with a raisable purchase hook."""

    def __init__(self, wallet: int = 10, error: Exception | None = None) -> None:
        self.wallet_balance = wallet
        self.error = error
        self.tech_bought: list[tuple[str, str]] = []

    async def wallet(self, kingdom_id: str) -> int:
        return self.wallet_balance

    async def spend_points(self, kingdom_id: str, cost: int) -> None:
        pass

    async def buy_combat_technology(self, kingdom_id: str, technology: str) -> object:
        if self.error is not None:
            raise self.error
        self.tech_bought.append((kingdom_id, technology))
        return object()


class FakeAttacksService:
    """AttacksService stand-in: the card stock counters (``purchases``)."""

    def __init__(self, purchases: dict[str, int] | None = None) -> None:
        self._purchases = dict(purchases or {})

    async def technology_state(self, kingdom_id: str) -> object:
        return SimpleNamespace(purchases=dict(self._purchases))


def _wire(
    *,
    economy: FakeEconomyService | None = None,
    lords: list[FakeLord] | None = None,
    kingdoms: list[FakeKingdom] | None = None,
    attacks: FakeAttacksService | None = None,
) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=FakeKingdomsService(
                lords or [FakeLord(id="111", role="king", kingdom_id="k1")],
                kingdoms or [FakeKingdom(id="k1")],
            ),
            economy_service=economy or FakeEconomyService(),
            attacks_service=attacks,
        )
    )


async def test_card_carries_fiche_and_confirm_cancel_buttons() -> None:
    _wire(economy=FakeEconomyService(wallet=9))
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("jeu_d_armes", "Jeu d'armes", discord.ButtonStyle.primary)
    await button.callback(interaction)
    assert interaction.response.ephemeral is True
    card = interaction.response.message
    assert card is not None
    sheet = str(card.embed.description if card.embed else "")
    assert "EFFECT" in sheet
    assert "COST — 1 ⚙️" in sheet
    assert "CURRENT TREASURY — 9 ⚙️" in sheet
    # jeu_d_armes is limited (1/season): the remainder rides the card.
    assert "LEFT THIS SEASON — 1/1" in sheet
    assert "King only" in sheet
    custom_ids = [child.item.custom_id for child in card.view.children]
    assert "kingdoms:market:confirm:tech:jeu_d_armes" in custom_ids
    assert "kingdoms:market:cancel" in custom_ids


async def test_insufficient_treasury_error_names_the_missing_amount() -> None:
    _wire(economy=FakeEconomyService(wallet=1, error=InsufficientPointsError("short")))
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("embuscade", "Embuscade", discord.ButtonStyle.primary)
    await button.callback(interaction)
    card = interaction.response.message
    assert card is not None
    confirm = next(c for c in card.view.children if "confirm" in c.item.custom_id)
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await confirm.callback(submit)
    assert submit.followup.messages
    # embuscade costs 2, treasury holds 1 → missing 1 ⚙️.
    assert "1 ⚙️ short" in (submit.followup.messages[-1].content or "")


async def test_limit_error_names_the_season_counters() -> None:
    _wire(economy=FakeEconomyService(wallet=50, error=EconomyLimitReachedError("capped")))
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("jeu_d_armes", "Jeu d'armes", discord.ButtonStyle.primary)
    await button.callback(interaction)
    card = interaction.response.message
    assert card is not None
    confirm = next(c for c in card.view.children if "confirm" in c.item.custom_id)
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await confirm.callback(submit)
    assert submit.followup.messages
    assert "Season limit reached (0/1)" in (submit.followup.messages[-1].content or "")


async def test_ledger_button_rebuilds_from_custom_id() -> None:
    button = await KingdomMarketLedgerButton.from_custom_id(
        MockInteraction(locale="en-US"),  # type: ignore[arg-type]
        discord.ui.Button(),  # type: ignore[arg-type]
        _LEDGER_MATCH,  # type: ignore[arg-type]
    )
    assert button.item.label == "🏛️ Chambre des comptes"
    assert button.item.custom_id == "kingdoms:market:ledger"


async def test_ledger_window_shows_balance_purchases_and_cards() -> None:
    """chambre-des-comptes-v2 (Drasah 29/10): the button opens ONE
    ephemeral window (never a modal) carrying the balance, the
    purchases in reverse chronology and the CARDS IN HAND — one line
    per card type with the REMAINING stock."""
    kingdom = FakeKingdom(
        id="k1",
        name="Merlinia",
        tech_points_bank=7,
        market_registry=[
            {"item": "corruption", "detail": "arabia — owner k2", "cost": 4, "at": 100},
            {"item": "explorateur", "detail": "islands — owner k1", "cost": 2, "at": 900},
        ],
    )
    _wire(
        kingdoms=[kingdom],
        attacks=FakeAttacksService({"sabotage": 2, "embuscade": 1, "traquenard": 0}),
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await KingdomMarketLedgerButton("🏛️ Chambre des comptes").callback(interaction)
    # chambre-des-comptes-fix-v1: the heavy mosaic paint must not race
    # Discord's 3s window — the callback DEFERS first, the sheet rides
    # a followup.
    assert interaction.response.deferred is True
    assert interaction.response.ephemeral is True
    assert interaction.followup.messages, "the sheet must reach the player"
    window = interaction.followup.messages[-1]
    sheet = str(window.content or "")
    assert "Merlinia" in sheet
    assert "spent: 6 ⚙️" in sheet
    assert "treasury: 7 ⚙️" in sheet
    assert "## Purchases" in sheet
    # Reverse chronology: the newest purchase (at=900) comes first.
    assert sheet.index("Explorer") < sheet.index("Corruption")
    assert "4 ⚙️" in sheet
    assert "/2" in sheet  # corruption counter against its 2/season limit
    # Cards in hand — deduplicated, one line per type + remaining stock.
    assert "## Cards in hand" in sheet
    assert "**Sabotage** — ×2" in sheet
    assert "**Embuscade** — ×1" in sheet
    assert "Traquenard" not in sheet  # 0 stock → not listed
    # v4: the held cards ride as ONE small-tile mosaic attachment
    # (v3's giant per-card posters drowned the sheet, Drasah 29/10) —
    # a 0-stock card ships no tile.
    files = list(window.files or [])
    filenames = [f.filename for f in files]
    assert filenames == ["cartes_en_main.png"]
    png = files[0].fp.read()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    from kingdoms.mods.kingdoms.civ_crests import _decode_png_rgba
    from kingdoms.mods.kingdoms.marche_art import HELD_CARD_H, HELD_CARD_W

    width, height, _pixels = _decode_png_rgba(png) or (0, 0, b"")
    assert (width, height) == (5 * HELD_CARD_W, HELD_CARD_H + 24)
    # no service wired for the modal anymore — the window is THE answer
    assert interaction.response.modal is None


async def test_ledger_window_lists_both_empty_states() -> None:
    _wire(kingdoms=[FakeKingdom(id="k1", market_registry=[])])
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await KingdomMarketLedgerButton("Ledger").callback(interaction)
    assert interaction.followup.messages, "the sheet must reach the player"
    sheet = str(interaction.followup.messages[-1].content or "")
    assert "No purchase yet." in sheet
    assert "No card in hand right now." in sheet
    # the followup window exists but carries NO art — empty hand.
    assert not (interaction.followup.messages[-1].files or [])


async def test_ledger_second_click_closes_the_previous_window() -> None:
    """fenetre-unique-v2 rule: ONE ephemeral window per player — the
    second ledger click closes the first sheet instead of stacking."""
    _wire(kingdoms=[FakeKingdom(id="k1", market_registry=[])])
    first: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await KingdomMarketLedgerButton("Ledger").callback(first)
    second: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await KingdomMarketLedgerButton("Ledger").callback(second)
    assert first.original_deleted is True
    assert second.original_deleted is not True
    from kingdoms.mods.kingdoms.kingdom_persistent import _EPHEMERAL_WINDOWS

    entry = _EPHEMERAL_WINDOWS.get("111")
    assert entry is not None
    assert entry[0] is second  # the registry holds the LAST window


async def test_overview_panel_carries_the_ledger_button() -> None:
    from kingdoms.mods.kingdoms import kingdom_realm_content

    kingdom = FakeKingdom(id="k1", name="Merlinia", tech_points_bank=7)
    view = kingdom_realm_content._build_overview(
        kingdom_realm_content._strings("en-US"),
        kingdom,
        {
            "members": [],
            "wallet": 7,
            "territories": [],
            "purchases": {},
            "patrol": [],
        },
    )
    payload = str(view.to_components())
    assert "kingdoms:market:ledger" in payload


_LEDGER_MATCH: Any = __import__("re").match(r"kingdoms:market:ledger", "kingdoms:market:ledger")

async def test_cancel_button_closes_the_card_window() -> None:
    """bourse-v1 (Drasah 26/10): the ❌ CLOSES the ephemeral window
    (defer + delete_original_response) — no leftover « Purchase
    cancelled » note."""
    from kingdoms.mods.kingdoms import kingdom_panels

    strings = kingdom_panels._strings("en")
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    cancel = KingdomMarketCancelButton(strings)
    await cancel.callback(interaction)
    assert interaction.response.deferred is True
    assert interaction.original_deleted is True
