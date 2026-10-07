"""Unit tests for the Marché panel (kingdoms#161, Roi-only boutique)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.discord.kingdom_market import (
    MARKET_PANEL_MARKER,
    KingdomMarketButton,
    build_market_panel,
    deploy_market_panel,
)
from kingdoms.discord.kingdom_persistent import KingdomsPanelWiring, register_kingdoms_panel_wiring
from kingdoms.mods.kingdoms.config import default_season_config

from tests.mocks.discord_mock import (
    MockInteraction,
    MockMember,
    MockMessage,
    MockTextChannel,
)


class _FakeKingdom:
    """A kingdoms() item double with the wallet read."""

    def __init__(self, name: str, bank: int, *, is_gaia: bool = False) -> None:
        self.name = name
        self.tech_points_bank = bank
        self.is_gaia = is_gaia


class _FakeLord:
    """A lords() item double with the King/Lord shape."""

    def __init__(self, player_id: str, role: str, kingdom_id: str | None = "k-1") -> None:
        self.id = player_id
        self.left = False
        self.kingdom_id = kingdom_id

        class _Role:
            value = role

        self.role = _Role()


class _FakeKingdoms:
    """KingdomsService double: config, kingdoms and lords."""

    def __init__(self, kingdoms: list[Any], lords: list[Any]) -> None:
        self.config = default_season_config()
        self._kingdoms = kingdoms
        self._lords = lords

    async def kingdoms(self) -> list[Any]:
        return self._kingdoms

    async def lords(self) -> list[Any]:
        return self._lords


class _FakeEconomy:
    """EconomyService double recording the purchases."""

    def __init__(self) -> None:
        self.purchases: list[tuple[str, str, str]] = []

    async def buy_combat_technology(self, kingdom_id: str, tech: str) -> str:
        self.purchases.append((kingdom_id, tech, ""))
        return tech

    async def buy_explorateur(self, kingdom_id: str, map_key: str) -> str:
        self.purchases.append((kingdom_id, "explorateur", map_key))
        return map_key

    async def buy_corruption(self, kingdom_id: str, territory_id: str) -> str:
        self.purchases.append((kingdom_id, "corruption", territory_id))
        return territory_id

    async def buy_royal_guard(self, kingdom_id: str, territory_id: str) -> str:
        self.purchases.append((kingdom_id, "garde_royale", territory_id))
        return territory_id


def _kingdoms(kingdoms: list[Any] | None = None, lords: list[Any] | None = None) -> _FakeKingdoms:
    return _FakeKingdoms(kingdoms or [], lords or [])


def _interaction(member_id: int = 7) -> MockInteraction:
    return MockInteraction(user=MockMember(id=member_id, name="roi"), guild=None)


def _wiring(kingdoms: Any = None, economy: Any = None) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(kingdoms_service=kingdoms, economy_service=economy)
    )


async def test_market_panel_lists_wallets_and_buy_buttons() -> None:
    """The panel shows the wallets, ten buy buttons and the marker."""
    kingdoms = _kingdoms(
        kingdoms=[
            _FakeKingdom("Aquitaine", 3),
            _FakeKingdom("Gaïa", 0, is_gaia=True),
        ]
    )
    view = await build_market_panel(kingdoms, "fr")
    texts = " ".join(
        str(getattr(child, "content", "")) for child in view.walk_children()
    )
    buttons = [
        child
        for child in view.walk_children()
        if str(getattr(child, "custom_id", "")).startswith("kingdoms:market:buy:")
    ]
    assert "Aquitaine" in texts
    assert "Gaïa" not in texts
    assert MARKET_PANEL_MARKER in texts
    assert len(buttons) == 10


async def test_deploy_market_panel_replaces_the_stale_message() -> None:
    """A stale marked panel is deleted; exactly one panel remains."""
    channel = MockTextChannel(name="marché")
    channel.messages.append(
        MockMessage(content=f"old\n-# {MARKET_PANEL_MARKER}", view=None)
    )
    kingdoms = _kingdoms(kingdoms=[_FakeKingdom("Aquitaine", 5)])
    assert await deploy_market_panel(channel, kingdoms, "fr") is True
    assert len(channel.messages) == 1


async def test_market_button_answers_closed_without_service() -> None:
    """Without the economy service the button answers 'market closed'."""
    _wiring(None, None)
    interaction = _interaction()
    button = KingdomMarketButton("embuscade", "Embuscade")
    await button.callback(interaction)
    assert interaction.response.sent is True
    assert interaction.response.message is not None
    assert "market" in str(interaction.response.message.content).lower()


async def test_market_button_refuses_non_king() -> None:
    """A Lord or an unenrolled player cannot buy."""
    kingdoms = _kingdoms(lords=[_FakeLord("7", "lord", "k-1")])
    economy = _FakeEconomy()
    _wiring(kingdoms, economy)
    interaction = _interaction()
    button = KingdomMarketButton("embuscade", "Embuscade")
    await button.callback(interaction)
    assert economy.purchases == []
    assert interaction.response.message is not None
    assert "king" in str(interaction.response.message.content).lower()


async def test_market_button_buys_for_the_king() -> None:
    """The King buys a combat technology through the economy service."""
    kingdoms = _kingdoms(lords=[_FakeLord("7", "king", "k-1")])
    economy = _FakeEconomy()
    _wiring(kingdoms, economy)
    interaction = _interaction()
    button = KingdomMarketButton("embuscade", "Embuscade")
    await button.callback(interaction)
    assert economy.purchases == [("k-1", "embuscade", "")]
    assert interaction.followup.messages, "the purchase must be acknowledged"


async def test_market_button_opens_the_target_modal_for_corruption() -> None:
    """A targeted special action opens the target modal instead of buying."""
    kingdoms = _kingdoms(lords=[_FakeLord("7", "king", "k-1")])
    economy = _FakeEconomy()
    _wiring(kingdoms, economy)
    interaction = _interaction()
    button = KingdomMarketButton("corruption", "Corruption")
    await button.callback(interaction)
    assert economy.purchases == []
    assert interaction.response.modal is not None
