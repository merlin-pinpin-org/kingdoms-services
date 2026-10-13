"""Kingdoms Marché v2 tests: article sheets, single echo and ledger.

The marche-v2 spec (2026-10-13): every purchase rides a modal « fiche
produit » (effect, cost, live treasury, per-season remainder), the echo
after the purchase is ONE ephemeral note with the enriched errors, and
every kingdom can open its 🏛️ Chambre des comptes (season balance +
reverse-chronology register) from the Le-Royaume panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import discord

from kingdoms.mods.kingdoms.economy import (
    EconomyLimitReachedError,
    InsufficientPointsError,
)
from kingdoms.mods.kingdoms.kingdom_persistent import (
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


def _wire(
    *,
    economy: FakeEconomyService | None = None,
    lords: list[FakeLord] | None = None,
    kingdoms: list[FakeKingdom] | None = None,
) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=FakeKingdomsService(
                lords or [FakeLord(id="111", role="king", kingdom_id="k1")],
                kingdoms or [FakeKingdom(id="k1")],
            ),
            economy_service=economy or FakeEconomyService(),
        )
    )


async def test_sheet_carries_cost_wallet_limit_and_king_only_note() -> None:
    _wire(economy=FakeEconomyService(wallet=9))
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("jeu_d_armes", "Jeu d'armes", discord.ButtonStyle.primary)
    await button.callback(interaction)
    modal = interaction.response.modal
    assert modal is not None
    sheet = str(modal.fiche.default or "")
    assert "EFFECT" in sheet
    assert "COST — 1 ⚙️" in sheet
    assert "CURRENT TREASURY — 9 ⚙️" in sheet
    # jeu_d_armes is limited (1/season): the remainder rides the sheet.
    assert "LEFT THIS SEASON — 1/1" in sheet
    assert "King only" in sheet


async def test_insufficient_treasury_error_names_the_missing_amount() -> None:
    _wire(economy=FakeEconomyService(wallet=1, error=InsufficientPointsError("short")))
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("embuscade", "Embuscade", discord.ButtonStyle.primary)
    await button.callback(interaction)
    modal = interaction.response.modal
    assert modal is not None
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await modal.on_submit(submit)
    assert submit.followup.messages
    # embuscade costs 2, treasury holds 1 → missing 1 ⚙️.
    assert "1 ⚙️ short" in (submit.followup.messages[-1].content or "")


async def test_limit_error_names_the_season_counters() -> None:
    _wire(economy=FakeEconomyService(wallet=50, error=EconomyLimitReachedError("capped")))
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("jeu_d_armes", "Jeu d'armes", discord.ButtonStyle.primary)
    await button.callback(interaction)
    modal = interaction.response.modal
    assert modal is not None
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await modal.on_submit(submit)
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


async def test_ledger_modal_shows_balance_and_reverse_chronology() -> None:
    kingdom = FakeKingdom(
        id="k1",
        name="Merlinia",
        tech_points_bank=7,
        market_registry=[
            {"item": "corruption", "detail": "arabia — owner k2", "cost": 4, "at": 100},
            {"item": "explorateur", "detail": "islands — owner k1", "cost": 2, "at": 900},
        ],
    )
    _wire(kingdoms=[kingdom])
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await KingdomMarketLedgerButton("🏛️ Chambre des comptes").callback(interaction)
    modal = interaction.response.modal
    assert modal is not None
    assert "Merlinia" in str(modal.title)
    register = str(modal.register.default or "")
    assert "spent: 6 ⚙️" in register
    assert "treasury: 7 ⚙️" in register
    # Reverse chronology: the newest purchase (at=900) comes first.
    assert register.index("Explorer") < register.index("Corruption")
    assert "4 ⚙️" in register
    assert "/2" in register  # corruption counter against its 2/season limit


async def test_ledger_modal_lists_the_empty_state() -> None:
    _wire(kingdoms=[FakeKingdom(id="k1", market_registry=[])])
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await KingdomMarketLedgerButton("Ledger").callback(interaction)
    modal = interaction.response.modal
    assert modal is not None
    assert "No purchase yet." in str(modal.register.default or "")


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
