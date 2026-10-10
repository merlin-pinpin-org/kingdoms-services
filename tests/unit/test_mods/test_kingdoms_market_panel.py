"""Kingdoms Marché panel tests: the King's shop wiring (§20, Lot C).

The regression this guards against: a market panel that renders but
buys nothing (the pre-wiring ``build_diplomacy_layout`` preview with
disabled buttons). These tests pin the purchase path end to end —
the persistent buttons answer through the live wiring, only Kings
buy, the economy service is actually debited, and the ephemeral
special-action selects resolve real territories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord
import pytest

from kingdoms.mods.kingdoms.kingdom_panels import (
    MARKET_PANEL_MARKER,
    build_market_panel,
    deploy_panels,
)
from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomMarketActionButton,
    KingdomMarketTechButton,
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import (
    MockGuild,
    MockInteraction,
    MockTextChannel,
    MockUser,
)

TECH_KEYS = (
    "embuscade",
    "traquenard",
    "patrouille",
    "contre_espionnage",
    "sabotage",
    "jeu_d_armes",
)


@dataclass
class FakeLord:
    id: str
    role: str
    kingdom_id: str | None = None
    left: bool = False


@dataclass
class FakeTerritory:
    id: str
    map_key: str
    owner_kingdom_id: str
    protected_until: Any = None


@dataclass
class FakeMapEntry:
    key: str


class FakeKingdomsService:
    """KingdomsService stand-in: just the lord registry and the config."""

    def __init__(self, lords: list[FakeLord] | None = None, maps: tuple[str, ...] = ()) -> None:
        self._lords = lords or []
        self.config = type(
            "FakeSeasonConfig",
            (),
            {
                "technologies": None,
                "maps": tuple(FakeMapEntry(key) for key in maps) or (),
            },
        )()

    async def lords(self) -> list[FakeLord]:
        return list(self._lords)


class FakeEconomyService:
    """EconomyService stand-in recording every purchase."""

    def __init__(self, wallet: int = 5) -> None:
        self.wallet_balance = wallet
        self.tech_bought: list[tuple[str, str]] = []
        self.explorateur_bought: list[str] = []
        self.corrupted: list[tuple[str, str]] = []
        self.guarded: list[tuple[str, str]] = []

    async def wallet(self, kingdom_id: str) -> int:
        return self.wallet_balance

    async def buy_combat_technology(self, kingdom_id: str, technology: str) -> object:
        self.tech_bought.append((kingdom_id, technology))
        return object()

    async def buy_explorateur(self, kingdom_id: str) -> FakeTerritory:
        self.explorateur_bought.append(kingdom_id)
        return FakeTerritory(
            id=f"{kingdom_id}-explored", map_key="islands", owner_kingdom_id=kingdom_id
        )

    async def buy_corruption(self, kingdom_id: str, territory_id: str) -> FakeTerritory:
        self.corrupted.append((kingdom_id, territory_id))
        return FakeTerritory(id=territory_id, map_key="corrupted", owner_kingdom_id=kingdom_id)

    async def buy_royal_guard(self, kingdom_id: str, territory_id: str) -> FakeTerritory:
        self.guarded.append((kingdom_id, territory_id))
        return FakeTerritory(id=territory_id, map_key="guarded", owner_kingdom_id=kingdom_id)


class FakeTerritoryService:
    """TerritoryService stand-in over a static territory list."""

    def __init__(self, territories: list[FakeTerritory] | None = None, drawn: set[str] | None = None) -> None:
        self._territories = territories or []
        self._drawn = drawn or set()

    async def territories(self) -> list[FakeTerritory]:
        return list(self._territories)

    async def drawn_map_keys(self) -> set[str]:
        return set(self._drawn)


def _custom_ids(item: Any) -> list[str]:
    """Collect every custom_id of a LayoutView tree (depth-first)."""
    ids: list[str] = []
    for child in getattr(item, "children", None) or []:
        custom_id = getattr(child, "custom_id", None)
        if custom_id:
            ids.append(custom_id)
        ids.extend(_custom_ids(child))
    return ids


def _wiring(
    *,
    lords: list[FakeLord] | None = None,
    economy: FakeEconomyService | None = None,
    territories: FakeTerritoryService | None = None,
    maps: tuple[str, ...] = ("arabia", "islands"),
) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=FakeKingdomsService(lords, maps),
            territories_service=territories,
            economy_service=economy,
        )
    )


async def test_market_panel_pins_every_purchase_button() -> None:
    view = await build_market_panel("fr")
    ids = set(_custom_ids(view))
    expected = {f"kingdoms:market:tech:{key}" for key in TECH_KEYS}
    expected |= {f"kingdoms:market:action:{key}" for key in ("explorateur", "corruption", "garde_royale")}
    assert expected <= ids
    assert ids == expected  # no stray interactive component


async def test_market_panel_serializes_with_the_marker() -> None:
    view = await build_market_panel("fr")
    payload = view.to_components()
    assert MARKET_PANEL_MARKER in str(payload)


async def test_tech_button_buys_through_the_economy_for_kings() -> None:
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("embuscade", "Embuscade — 2 🔬", discord.ButtonStyle.primary)
    await button.callback(interaction)
    assert economy.tech_bought == [("k1", "embuscade")]
    assert interaction.followup.messages
    assert "Purchased" in (interaction.followup.messages[-1].content or "")


async def test_tech_button_refuses_non_kings() -> None:
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="lord", kingdom_id="k1")],
        economy=economy,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("embuscade", "Embuscade", discord.ButtonStyle.primary)
    await button.callback(interaction)
    assert economy.tech_bought == []
    assert "Only Kings" in (interaction.followup.messages[-1].content or "")


async def test_action_button_opens_the_territory_select_and_buys() -> None:
    economy = FakeEconomyService()
    territories = FakeTerritoryService(
        territories=[
            FakeTerritory(id="t1", map_key="arabia", owner_kingdom_id="k2"),
            FakeTerritory(id="t2", map_key="islands", owner_kingdom_id="k1"),
        ]
    )
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
        territories=territories,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("corruption", "Corruption — 4 🔬", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    assert interaction.response.sent
    message = interaction.response.message
    assert message is not None and message.view is not None
    select = next(iter(message.view.children))
    assert [opt.value for opt in select.options] == ["t1"]  # own territory filtered out

    choice: Any = MockInteraction(user=MockUser(id=111), data={"values": ["t1"]}, locale="en-US")
    await select.callback(choice)
    assert economy.corrupted == [("k1", "t1")]
    assert "Purchased" in (choice.followup.messages[-1].content or "")


async def test_explorateur_button_buys_a_random_map_directly() -> None:
    """D48: the Explorateur needs no select - the map is drawn on click."""
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("explorateur", "Explorateur", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    assert economy.explorateur_bought == ["k1"]
    assert "Purchased" in (interaction.followup.messages[-1].content or "")


async def test_action_button_answers_no_options_when_empty() -> None:
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
        territories=FakeTerritoryService(territories=[]),
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("garde_royale", "Garde Royale", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    assert interaction.response.sent
    assert "No option" in (interaction.response.message.content if interaction.response.message else "")


async def test_deploy_panels_pins_the_market_panel_in_the_marche_channel() -> None:
    guild = MockGuild()
    channel = MockTextChannel(name="marché", guild=guild)
    guild._channels[channel.id] = channel
    report = await deploy_panels(guild, None)
    assert report.get("marché") == "deployed"
    assert channel.messages
    view = channel.messages[-1].view
    assert view is not None
    ids = set(_custom_ids(view))
    assert "kingdoms:market:tech:embuscade" in ids


@pytest.mark.parametrize(
    ("locale", "key"),
    [("fr", "market_title"), ("en-US", "market_title"), ("fr", "market_not_king")],
)
def test_market_strings_exist_in_both_languages(locale: str, key: str) -> None:
    from kingdoms.mods.kingdoms.kingdom_panels import _strings

    assert key in _strings(locale)


def test_wiring_resolves_the_market_services_from_the_bot() -> None:
    from kingdoms.mods.kingdoms.kingdom_persistent import register_kingdoms_panel_bot

    class _Bot:
        kingdoms_service = "kingdoms"
        kingdoms_territories_service = "territories"
        kingdoms_attacks_service = "attacks"
        kingdoms_economy_service = "economy"

    register_kingdoms_panel_bot(_Bot())
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    assert wiring.economy_service == "economy"
    assert wiring.territories_service == "territories"
    assert wiring.attacks_service == "attacks"
