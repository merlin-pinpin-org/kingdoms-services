"""Kingdoms mod distributor — unit tests (D75, 2026-10-12).

Drasah's Distributeur: the admin injects elements into a kingdom by
hand and watches them propagate into the live views. The alliance
rules come from Drasah directly: any catalog civilization, never a
duplicate of the kingdom's own hand, never a civilization claimed by
an active marriage — a NON-married civilization may be shared by
several kingdoms. The removal of a civilization secured by one of the
kingdom's own marriages is refused. Everything else (wallet, parish,
territories, lords, tech counters) is a cost-free direct write.
"""
from __future__ import annotations

from typing import Any

import pytest

from kingdoms.mods.kingdoms.attacks import COMBAT_TECHNOLOGIES
from kingdoms.mods.kingdoms.config import (
    CivilizationCondition,
    KingdomsSeasonConfig,
    default_map_catalog,
    default_season_config,
)
from kingdoms.mods.kingdoms.distributor import (
    AllianceAlreadyInHandError,
    AllianceMarriedError,
    AllianceNotInHandError,
    AllianceSecuredError,
    DistributorService,
    DistributorValueError,
    GaiaAllianceError,
)
from kingdoms.mods.kingdoms.territories import MapPoolExhaustedError

from .test_kingdoms_attacks import Bundle as _AttackBundle


def _config(civ_count: int = 6) -> KingdomsSeasonConfig:
    """A config with a catalog of civilizations and the full map catalog."""
    civs = tuple(
        CivilizationCondition(key=f"civ-{index}", display_name=f"Civ {index}")
        for index in range(civ_count)
    )
    return default_season_config().model_copy(
        deep=True, update={"maps": default_map_catalog(), "civilizations": civs}
    )


class Bundle(_AttackBundle):
    """The attack bundle extended with the distributor service."""

    def __init__(self, config: KingdomsSeasonConfig | None = None) -> None:
        super().__init__(config or _config())
        self.distributor = DistributorService(  # type: ignore[arg-type]
            self.store, self.config, self.kingdoms, self.territories, self.attacks
        )

    async def kingdom_of(self, name: str):
        """Return the fresh kingdom record by display name."""
        kid = await self.kingdom_id(name)
        return next(k for k in await self.kingdoms.kingdoms() if k.id == kid)

    async def patch_lord(self, lord_id: str, **fields: Any) -> None:
        """Patch one lord field straight in the store (test setup)."""
        lord = next(item for item in await self.kingdoms.lords() if item.id == lord_id)
        for key, value in fields.items():
            setattr(lord, key, value)
        await self.store.upsert_lord(lord.to_mongo())


# ----------------------------------------------------------------------
# Alliances (Drasah's rules)
# ----------------------------------------------------------------------
async def test_add_alliance_hands_any_catalog_civ_and_shares_it() -> None:
    """Any catalog civ is injectable — and a NON-married civ may be shared."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.distributor.add_alliance(aquitaine, "civ-0")
    # Sharing: the same civilization in a second kingdom's hand is legal.
    kingdom = await bundle.distributor.add_alliance(bourgogne, "civ-0")
    assert "civ-0" in kingdom.civilizations
    assert "civ-0" in (await bundle.kingdom_of("Aquitaine")).civilizations


async def test_add_alliance_refusals() -> None:
    """Duplicate of the hand, married civ, unknown civ, Gaïa: all refused."""
    bundle = Bundle()
    _, _, gaia = await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.distributor.add_alliance(aquitaine, "civ-0")
    with pytest.raises(AllianceAlreadyInHandError):
        await bundle.distributor.add_alliance(aquitaine, "civ-0")
    # A marriage (any kingdom) claims the civilization (D74).
    await bundle.patch_lord("lord-a", married_civilization="civ-1")
    with pytest.raises(AllianceMarriedError):
        await bundle.distributor.add_alliance(aquitaine, "civ-1")
    with pytest.raises(DistributorValueError):
        await bundle.distributor.add_alliance(aquitaine, "not-a-civ")
    with pytest.raises(GaiaAllianceError):
        await bundle.distributor.add_alliance(gaia, "civ-2")


async def test_remove_alliance_and_the_secured_refusal() -> None:
    """Removing works, unless one of the kingdom's own marriages secures it."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.distributor.add_alliance(aquitaine, "civ-0")
    await bundle.distributor.add_alliance(aquitaine, "civ-1")
    kingdom = await bundle.distributor.remove_alliance(aquitaine, "civ-0")
    assert kingdom.civilizations == ["civ-1"]
    with pytest.raises(AllianceNotInHandError):
        await bundle.distributor.remove_alliance(aquitaine, "civ-0")
    # A married lord of THIS kingdom secures the civ — removal refused.
    await bundle.patch_lord("lord-a", married_civilization="civ-1")
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.secured_civilizations = ["civ-1"]
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    with pytest.raises(AllianceSecuredError):
        await bundle.distributor.remove_alliance(aquitaine, "civ-1")


async def test_civilization_options_show_the_whole_catalog() -> None:
    """The panel sees every catalog civ with its state (nothing hidden)."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.distributor.add_alliance(aquitaine, "civ-0")
    await bundle.patch_lord("lord-b", married_civilization="civ-1")
    rows = await bundle.distributor.civilization_options(aquitaine)
    assert len(rows) == 6
    states = {row["key"]: row["status"] for row in rows}
    assert states["civ-0"] == "hand"
    assert states["civ-1"] == "married"
    assert states["civ-2"] == "free"


# ----------------------------------------------------------------------
# Wallet & Paroisse
# ----------------------------------------------------------------------
async def test_wallet_and_parish_direct_writes() -> None:
    """Treasury, points, tokens and palier are cost-free direct writes."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    kingdom = await bundle.distributor.set_tech_points(aquitaine, 50)
    assert kingdom.tech_points_bank == 50
    kingdom = await bundle.distributor.set_marriage_points(aquitaine, 3)
    assert kingdom.marriage_points == 3
    kingdom = await bundle.distributor.set_arranged_tokens(aquitaine, 2)
    assert kingdom.arranged_marriage_stock == 2
    kingdom = await bundle.distributor.set_parish_tier(aquitaine, 3)
    assert kingdom.parish_tier == 3
    with pytest.raises(DistributorValueError):
        await bundle.distributor.set_tech_points(aquitaine, -1)
    with pytest.raises(DistributorValueError):
        await bundle.distributor.set_parish_tier(aquitaine, 0)
    with pytest.raises(DistributorValueError):
        await bundle.distributor.set_parish_tier(aquitaine, 9)


# ----------------------------------------------------------------------
# Territories
# ----------------------------------------------------------------------
async def test_give_then_retire_then_refuse_the_same_map() -> None:
    """give_map attributes, retire_territory un-attributes (out for §8)."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    territory = await bundle.distributor.give_map(aquitaine, "arabia")
    assert territory.owner_kingdom_id == aquitaine
    retired = await bundle.distributor.retire_territory("arabia")
    assert retired.map_key == "arabia"
    assert await bundle.distributor.territories_of(aquitaine) == []
    with pytest.raises(MapPoolExhaustedError):
        await bundle.distributor.give_map(aquitaine, "arabia")


async def test_transfer_and_shield_territory() -> None:
    """Transfer moves the territory; the shield sets and clears."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(7)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    map_key = (await bundle.owner_maps(aquitaine))[0]
    territory = next(
        item
        for item in await bundle.territories.territories()
        if item.map_key == map_key
    )
    moved = await bundle.distributor.transfer_territory(territory.id, bourgogne)
    assert moved.owner_kingdom_id == bourgogne
    shielded = await bundle.distributor.shield_territory(map_key, 48)
    assert shielded.protected_until is not None
    cleared = await bundle.distributor.shield_territory(map_key, 0)
    assert cleared.protected_until is None


# ----------------------------------------------------------------------
# Lords & combat technologies
# ----------------------------------------------------------------------
async def test_reset_budgets_and_marriage_lock() -> None:
    """Budgets recharge; the marriage lock sets and clears."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.patch_lord("lord-a", attack_used=1, defense_used=1)
    lord = await bundle.distributor.reset_budgets("lord-a")
    assert lord.attack_used == 0
    assert lord.defense_used == 0
    lord = await bundle.distributor.set_marriage_lock("lord-a", 6)
    assert lord.marriage_locked_until is not None
    lord = await bundle.distributor.set_marriage_lock("lord-a", None)
    assert lord.marriage_locked_until is None


async def test_forced_technology_counters_never_touch_the_wallet() -> None:
    """set_technology_count injects the effect, not the purchase."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.distributor.set_tech_points(aquitaine, 0)
    assert await bundle.distributor.set_technology_count(aquitaine, "embuscade", 2) == 2
    state = await bundle.attacks.technology_state(aquitaine)
    assert state.purchases["embuscade"] == 2
    assert state.tech_points == 0  # the wallet never paid
    assert await bundle.distributor.set_technology_count(aquitaine, "embuscade", 0) == 0
    state = await bundle.attacks.technology_state(aquitaine)
    assert "embuscade" not in state.purchases
    with pytest.raises(DistributorValueError):
        await bundle.distributor.set_technology_count(aquitaine, "embuscade", -1)


# ----------------------------------------------------------------------
# Panel — strings, pagination, deployment
# ----------------------------------------------------------------------
class _FakeMessage:
    """A sent message stand-in (content only)."""

    def __init__(self, content: str) -> None:
        self.content = content

    async def delete(self) -> None:
        return None


class _FakeChannel:
    """discord.TextChannel stand-in: history + send."""

    def __init__(self) -> None:
        self.history_list: list[_FakeMessage] = []
        self.sent: list[_FakeMessage] = []

    def history(self, limit: int = 50) -> Any:
        class _History:
            def __init__(self, items: list[_FakeMessage]) -> None:
                self._items = items

            async def __aiter__(self) -> Any:
                for item in self._items:
                    yield item

        return _History(self.history_list)

    async def send(self, content: str = "", view: Any = None) -> _FakeMessage:
        del view
        message = _FakeMessage(content)
        self.sent.append(message)
        return message


def test_distributor_button_rebuilds_from_the_wire_in_both_locales() -> None:
    """Check 30's lesson: every persistent-item label exists in both locales."""
    from kingdoms.mods.kingdoms.kingdom_distributor_panel import (
        DISTRIBUTOR_PANEL_MARKER,
        KingdomDistributorButton,
        _strings,
        panel_text,
    )

    for locale in ("fr", "en"):
        strings = _strings(locale)
        button = KingdomDistributorButton(strings["open_button"])
        assert button.item.custom_id == "kingdoms:distributor:open"
        assert DISTRIBUTOR_PANEL_MARKER in panel_text(strings)


async def test_deploy_distributor_panel_pins_the_marker_message() -> None:
    """The salon deployment pins one panel message with the marker."""
    from kingdoms.mods.kingdoms.kingdom_distributor_panel import (
        DISTRIBUTOR_PANEL_MARKER,
        deploy_distributor_panel,
    )

    channel = _FakeChannel()
    channel.history_list = [_FakeMessage("old panel <!-- kingdoms-distributor -->")]
    await deploy_distributor_panel(channel, "fr")
    assert len(channel.sent) == 1
    assert DISTRIBUTOR_PANEL_MARKER in channel.sent[0].content


async def test_civ_view_paginates_the_catalog() -> None:
    """56 civs → 3 pages of 25 (Discord's select ceiling)."""
    from kingdoms.mods.kingdoms.kingdom_distributor_panel import PAGE_SIZE, _CivView

    bundle = Bundle(_config(civ_count=56))
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(distributor_service=bundle.distributor)
    )
    view = _CivView("fr", aquitaine, mode="add", page=0)
    await view.refresh()
    assert view.pages == 3
    assert len(view.select.options) == PAGE_SIZE
    assert view.prev_button.disabled
    assert not view.next_button.disabled
    view.page = 2
    await view.refresh()
    assert len(view.select.options) == 6
    assert view.next_button.disabled
    # The remove mode lists only the hand — empty here → one empty page.
    view = _CivView("fr", aquitaine, mode="remove", page=0)
    await view.refresh()
    assert view.pages == 1
    assert view.rows == []


def test_combat_technology_catalog_is_the_panel_select() -> None:
    """The panel offers exactly the combat technologies of the season."""
    assert "embuscade" in COMBAT_TECHNOLOGIES
    assert "traquenard" in COMBAT_TECHNOLOGIES
