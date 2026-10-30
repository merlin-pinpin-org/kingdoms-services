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
    KingdomRempartsGuardButton,
    KingdomRempartsPatrouilleButton,
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import (
    MockGuild,
    MockInteraction,
    MockTextChannel,
    MockUser,
)

@pytest.fixture(autouse=True)
def _clear_purchase_receipts() -> None:
    """The receipt registry is module-level — never leak across tests."""
    from kingdoms.mods.kingdoms import kingdom_persistent as _kp

    _kp._RECEIPT_WINDOWS.clear()


TECH_KEYS = (
    "embuscade",
    "traquenard",
    "contre_espionnage",
    "sabotage",
    "jeu_d_armes",
)
# remparts-v2: the market sells the guard TOKEN and the patrol slot UNLOCK.
ACTION_KEYS = ("explorateur", "corruption", "garde_royale", "patrouille", "mariage_arrange")


@dataclass
class FakeLord:
    id: str
    role: str
    kingdom_id: str | None = None
    left: bool = False
    display_name: str = "Lord"
    married_civilization: str | None = None


@dataclass
class FakeTerritory:
    id: str
    map_key: str
    owner_kingdom_id: str
    protected_until: Any = None


@dataclass
class FakeMapEntry:
    key: str


class _FakeTechState:
    """TechnologyState stand-in: a plain purchases dict."""

    def __init__(self) -> None:
        self.purchases: dict[str, int] = {}


class FakeAttacksService:
    """AttacksService stand-in exposing the technology state."""

    def __init__(self) -> None:
        self._states: dict[str, _FakeTechState] = {}

    async def technology_state(self, kingdom_id: str) -> _FakeTechState:
        return self._states.setdefault(kingdom_id, _FakeTechState())


class FakeKingdomsService:
    """KingdomsService stand-in: just the lord registry and the config."""

    def __init__(
        self,
        lords: list[FakeLord] | None = None,
        maps: tuple[str, ...] = (),
        civilizations: tuple[str, ...] = ("celtes", "shu"),
    ) -> None:
        self._lords = lords or []
        self.config = type(
            "FakeSeasonConfig",
            (),
            {
                "technologies": None,
                "maps": tuple(FakeMapEntry(key) for key in maps) or (),
                "civilizations": tuple(FakeMapEntry(key) for key in civilizations),
            },
        )()

    async def lords(self) -> list[FakeLord]:
        return list(self._lords)


class FakeEconomyService:
    """EconomyService stand-in recording every purchase."""

    def __init__(self, wallet: int = 5, attacks: FakeAttacksService | None = None) -> None:
        self.wallet_balance = wallet
        self.tech_bought: list[tuple[str, str]] = []
        self.explorateur_bought: list[str] = []
        self.corrupted: list[tuple[str, str]] = []
        self.guarded: list[tuple[str, str]] = []
        self.guard_tokens = 0
        self.patrols: list[tuple[str, int]] = []
        self.placed_patrols: list[tuple[str, int, int]] = []
        self.spent: list[tuple[str, int]] = []
        self.attacks = attacks

    async def wallet(self, kingdom_id: str) -> int:
        return self.wallet_balance

    async def spend_points(self, kingdom_id: str, cost: int) -> None:
        self.spent.append((kingdom_id, cost))

    async def buy_arranged_marriage_token(self, kingdom_id: str) -> object:
        self.spent.append((kingdom_id, 3))
        return object()

    async def buy_patrouille_slot(self, kingdom_id: str, slot_no: int) -> int:
        self.patrols.append((kingdom_id, slot_no))
        if self.attacks is not None:
            state = await self.attacks.technology_state(kingdom_id)
            state.purchases[f"patrouille_unlock_{slot_no}"] = 1
        return slot_no

    async def place_patrouille(self, kingdom_id: str, slot_no: int, slot_start: int) -> int:
        self.placed_patrols.append((kingdom_id, slot_no, slot_start))
        return slot_start

    async def buy_combat_technology(self, kingdom_id: str, technology: str) -> object:
        self.tech_bought.append((kingdom_id, technology))
        return object()

    async def buy_explorateur(self, kingdom_id: str, lord_id: str) -> FakeLord:
        self.explorateur_bought.append((kingdom_id, lord_id))
        return FakeLord(id=lord_id, role="lord", kingdom_id=kingdom_id)

    async def buy_corruption(self, kingdom_id: str, territory_id: str) -> FakeTerritory:
        self.corrupted.append((kingdom_id, territory_id))
        return FakeTerritory(id=territory_id, map_key="corrupted", owner_kingdom_id=kingdom_id)

    async def buy_royal_guard(self, kingdom_id: str) -> int:
        self.guard_tokens += 1
        return self.guard_tokens

    async def place_royal_guard(self, kingdom_id: str, territory_id: str) -> FakeTerritory:
        self.guarded.append((kingdom_id, territory_id))
        self.guard_tokens -= 1
        from datetime import UTC, datetime, timedelta

        return FakeTerritory(
            id=territory_id,
            map_key="guarded",
            owner_kingdom_id=kingdom_id,
            protected_until=datetime.now(tz=UTC) + timedelta(hours=12),
        )


class FakeDiplomacyService:
    """DiplomacyService stand-in recording arranged marriages."""

    def __init__(self) -> None:
        self.arranged: list[tuple[str, str]] = []

    async def arranged_marriage(self, player_id: str, civilization: str) -> str:
        self.arranged.append((player_id, civilization))
        return civilization


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
    diplomacy: FakeDiplomacyService | None = None,
    maps: tuple[str, ...] = ("arabia", "islands"),
    civilizations: tuple[str, ...] = ("celtes", "shu"),
) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=FakeKingdomsService(lords, maps, civilizations),
            territories_service=territories,
            economy_service=economy,
            diplomacy_service=diplomacy,
        )
    )


async def test_market_panel_pins_every_purchase_button() -> None:
    view = await build_market_panel("fr")
    ids = set(_custom_ids(view))
    expected = {f"kingdoms:market:tech:{key}" for key in TECH_KEYS}
    expected |= {f"kingdoms:market:action:{key}" for key in ACTION_KEYS}
    assert expected <= ids
    assert ids == expected  # no stray interactive component


async def test_market_panel_serializes_with_the_marker() -> None:
    view = await build_market_panel("fr")
    payload = view.to_components()
    assert MARKET_PANEL_MARKER in str(payload)


def _action_rows(view: discord.ui.LayoutView) -> list[discord.ui.ActionRow[discord.ui.LayoutView]]:
    rows: list[discord.ui.ActionRow[discord.ui.LayoutView]] = []
    for container in view.children:
        if isinstance(container, discord.ui.Container):
            for child in container.children:
                if isinstance(child, discord.ui.ActionRow):
                    rows.append(child)
    return rows


async def test_market_panel_never_serializes_an_empty_action_row() -> None:
    """Regression (D68): a preallocated spare tech row serialized as an
    EMPTY ActionRow, which Discord rejects with code 50035 ("Must be
    between 1 and 5 in length") — the whole panel send failed and the
    Marché channel was left without its panel."""
    view = await build_market_panel("fr")
    rows = _action_rows(view)
    assert rows, "market panel must contain at least one action row"
    for row in rows:
        assert len(row.children) >= 1


def _card_confirm(interaction: Any) -> Any:
    """The ✅ button of the revealed ephemeral article card."""
    card = interaction.response.message
    assert card is not None and card.view is not None
    return next(c for c in card.view.children if "confirm" in c.item.custom_id)


async def test_tech_button_opens_the_sheet_then_buys_through_the_economy_for_kings() -> None:
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketTechButton("embuscade", "Embuscade — 2 🔬", discord.ButtonStyle.primary)
    await button.callback(interaction)
    card = interaction.response.message
    assert card is not None
    sheet = str(card.embed.description if card.embed else "")
    assert "EFFECT" in sheet
    assert "COST" in sheet
    assert "CURRENT TREASURY" in sheet
    assert "King only" in sheet

    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _card_confirm(interaction).callback(submit)
    assert economy.tech_bought == [("k1", "embuscade")]
    assert submit.followup.messages
    assert "Purchased" in (submit.followup.messages[-1].content or "")


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
    assert interaction.response.message is not None
    assert "Only Kings" in (interaction.response.message.content or "")


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
    confirm = _card_confirm(interaction)

    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await confirm.callback(submit)
    message = submit.response.message
    assert message is not None and message.view is not None
    select = next(iter(message.view.children))
    assert [opt.value for opt in select.options] == ["t1"]  # own territory filtered out

    choice: Any = MockInteraction(user=MockUser(id=111), data={"values": ["t1"]}, locale="en-US")
    choice.message = message
    await select.callback(choice)
    assert economy.corrupted == [("k1", "t1")]
    # Single-bubble echo: the select bubble is edited, no followup stack —
    # achat-recu-v1: the purchase ALSO lands its line in the receipt.
    assert len(choice.followup.messages) == 1
    receipt = str(choice.followup.messages[-1].content or "")
    assert "Receipt" in receipt and "Purchased" in receipt
    assert "Purchased" in (choice.response.message.content if choice.response.message else "")


async def test_explorateur_button_locks_the_chosen_lord() -> None:
    """marche-v2 art. 6: the Explorateur opens a lord select (48h lock)."""
    economy = FakeEconomyService()
    _wiring(
        lords=[
            FakeLord(id="111", role="king", kingdom_id="k1"),
            FakeLord(id="222", role="lord", kingdom_id="k1"),
        ],
        economy=economy,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("explorateur", "Explorateur", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    confirm = _card_confirm(interaction)

    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await confirm.callback(submit)
    # The sheet opens the lord select instead of buying instantly.
    message = submit.response.message
    assert message is not None and message.view is not None
    select = next(iter(message.view.children))
    assert [opt.value for opt in select.options] == ["111", "222"]

    choice: Any = MockInteraction(user=MockUser(id=111), data={"values": ["222"]}, locale="en-US")
    choice.message = message
    await select.callback(choice)
    assert economy.explorateur_bought == [("k1", "222")]
    # achat-recu-v1: one receipt followup, never a stack of ✅ bubbles.
    assert len(choice.followup.messages) == 1
    receipt = str(choice.followup.messages[-1].content or "")
    assert "Receipt" in receipt and "Purchased" in receipt
    assert "Purchased" in (choice.response.message.content if choice.response.message else "")


async def test_patrouille_button_buys_the_next_slot_unlock() -> None:
    """remparts-v2: the market sells the slot UNLOCK (1 first, 2 after);
    the window itself is placed from the Remparts salon."""
    attacks = FakeAttacksService()
    economy = FakeEconomyService(attacks=attacks)
    lords = [FakeLord(id="111", role="king", kingdom_id="k1")]
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=FakeKingdomsService(lords),
            economy_service=economy,
            attacks_service=attacks,
        )
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("patrouille", "Patrol — 2 🔬", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    confirm = _card_confirm(interaction)

    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await confirm.callback(submit)
    assert economy.patrols == [("k1", 1)]  # slot 1 unlocked first
    assert submit.followup.messages  # the buy note echoes the unlock
    assert "1" in (submit.followup.messages[-1].content or "")

    # The second purchase unlocks the slot 2.
    await button.callback(interaction)
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _card_confirm(interaction).callback(submit)
    assert economy.patrols == [("k1", 1), ("k1", 2)]


async def test_two_purchases_share_one_cumulative_receipt() -> None:
    """achat-recu-v1 (Drasah 30/10): the 2nd purchase EDITS the receipt
    in place (same window, one more line) — never a new ✅ bubble."""
    economy = FakeEconomyService()
    _wiring(lords=[FakeLord(id="111", role="king", kingdom_id="k1")], economy=economy)
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("patrouille", "Patrol — 2 🔬", discord.ButtonStyle.secondary)

    await button.callback(interaction)
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _card_confirm(interaction).callback(submit)
    assert len(submit.followup.messages) == 1
    receipt = submit.followup.messages[-1]
    assert "1" in str(receipt.content or "")  # slot 1 unlocked

    await button.callback(interaction)
    submit2: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _card_confirm(interaction).callback(submit2)
    assert economy.patrols == [("k1", 1), ("k1", 2)]
    # SAME window — one followup on each interaction, and the FIRST
    # receipt message was edited in place with BOTH lines.
    assert len(submit2.followup.messages) == 0
    assert receipt.edited is True
    content = str(receipt.content or "")
    assert content.count("Purchased") == 2


async def test_expired_receipt_opens_a_fresh_one() -> None:
    """The webhook token dies after ~15 min — the registry expires the
    window and the next purchase opens a NEW receipt (no stack, no
    crash)."""
    import time as _time

    from kingdoms.mods.kingdoms import kingdom_persistent as _kp

    economy = FakeEconomyService()
    _wiring(lords=[FakeLord(id="111", role="king", kingdom_id="k1")], economy=economy)
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("patrouille", "Patrol — 2 🔬", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _card_confirm(interaction).callback(submit)
    assert len(submit.followup.messages) == 1

    # 16 minutes later the receipt is dead — a fresh one must open.
    _kp._RECEIPT_WINDOWS["111"]["opened_at"] = _time.monotonic() - 16 * 60
    await button.callback(interaction)
    submit2: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _card_confirm(interaction).callback(submit2)
    assert len(submit2.followup.messages) == 1
    content = str(submit2.followup.messages[-1].content or "")
    assert content.count("Purchased") == 1  # the fresh receipt starts over


async def test_garde_royale_market_button_buys_a_token() -> None:
    """remparts-v2: the market sells guard TOKENS (no hard limit) — the
    King places them from the Remparts salon."""
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("garde_royale", "Garde Royale — 2 🔬", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    confirm = _card_confirm(interaction)

    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await confirm.callback(submit)
    assert economy.guard_tokens == 1
    assert submit.followup.messages  # the token note echoes the stock


async def test_remparts_patrouille_button_places_the_window() -> None:
    """remparts-v2: the Remparts button places the unlocked slot's 2h window."""
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomRempartsPatrouilleButton(
        1,
        {
            "remparts_patrouille_button": "🛡️ Placer Patrouille {0}",
            "market_choose_slot": "Choose the slot:",
            "market_slot_line": "{0}h → {1}h",
            "remparts_patrouille_placed": "Patrouille {0} placée : {1}h → {2}h",
        },
    )
    await button.callback(interaction)
    message = interaction.response.message
    assert message is not None and message.view is not None
    select = next(iter(message.view.children))
    assert [opt.value for opt in select.options] == [str(h) for h in range(0, 24, 2)]

    choice: Any = MockInteraction(user=MockUser(id=111), data={"values": ["2"]}, locale="en-US")
    choice.message = message
    await select.callback(choice)
    assert economy.placed_patrols == [("k1", 1, 2)]


async def test_action_button_answers_no_options_when_empty() -> None:
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="111", role="king", kingdom_id="k1")],
        economy=economy,
        territories=FakeTerritoryService(territories=[]),
    )
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    button = KingdomMarketActionButton("corruption", "Corruption", discord.ButtonStyle.secondary)
    await button.callback(interaction)
    confirm = _card_confirm(interaction)
    submit: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await confirm.callback(submit)
    assert submit.response.sent
    message = submit.response.message
    assert message is not None
    assert "No option" in (message.content or "")


async def test_remparts_guard_button_opens_the_owned_map_select_and_places() -> None:
    """remparts-v2: the Marché sells guard TOKENS; the King POSSES them
    from the Remparts salon on one of his own maps."""
    economy = FakeEconomyService()
    economy.guard_tokens = 1  # the token was bought at the market
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
    button = KingdomRempartsGuardButton({"remparts_guard_button": "🛡️ Placer un Garde Royal"})
    await button.callback(interaction)
    message = interaction.response.message
    assert message is not None and message.view is not None
    select = next(iter(message.view.children))
    # Only the kingdom's own maps are shieldable (enemy map filtered out).
    assert [opt.value for opt in select.options] == ["t2"]

    choice: Any = MockInteraction(user=MockUser(id=111), data={"values": ["t2"]}, locale="en-US")
    choice.message = message
    await select.callback(choice)
    assert economy.guarded == [("k1", "t2")]
    assert economy.guard_tokens == 0  # the token was consumed
    assert choice.response.message is not None


async def test_remparts_guard_button_refuses_non_kings() -> None:
    economy = FakeEconomyService()
    _wiring(
        lords=[FakeLord(id="222", role="lord", kingdom_id="k1")],
        economy=economy,
        territories=FakeTerritoryService(territories=[]),
    )
    interaction: Any = MockInteraction(user=MockUser(id=222), locale="en-US")
    button = KingdomRempartsGuardButton({"remparts_guard_button": "🛡️ Placer un Garde Royal"})
    await button.callback(interaction)
    message = interaction.response.message
    assert message is not None
    assert "Kings" in (message.content or "")


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
        kingdoms_diplomacy_service = "diplomacy"

    register_kingdoms_panel_bot(_Bot())
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    assert wiring.economy_service == "economy"
    assert wiring.territories_service == "territories"
    assert wiring.attacks_service == "attacks"
    assert wiring.diplomacy_service == "diplomacy"


async def test_deploy_panels_edits_the_market_panel_in_place_idempotently() -> None:
    """vues-silencieuses-v1: a re-deployment EDITS the pinned market panel
    (identity guard → zero API call), never delete+send."""
    guild = MockGuild()
    channel = MockTextChannel(name="marché", guild=guild)
    guild._channels[channel.id] = channel

    await deploy_panels(guild, None)
    first = channel.messages[-1]
    first._edited = False  # reset the spy before the idempotent re-run

    await deploy_panels(guild, None)

    live = [m for m in channel.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1
    assert live[0] is first  # edited in place, never deleted and resent
    assert first.edited is False  # unchanged panel → zero API call
