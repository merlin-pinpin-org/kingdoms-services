"""Kingdoms mod expedition (exploration-expedition-v1) - unit tests.

Drasah 30/10/2026: the Explorateur token funds a whole EXPEDITION now —
the 🧭 salon boards a CHEF (one AVAILABLE defence point, never
consumed) and a self-enrolled CREW (each −4h of travel, floor 24h);
« Lâcher les amarres » is the single token-consuming step and the tick
resolves the return (a fresh map, or the compensation civ + 2 ⚙️ when
the map pool ran dry)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.economy import (
    EXPEDITION_MIN_HOURS,
    EXPLORATEUR_STOCK,
    EconomyError,
    EconomyService,
)
from kingdoms.mods.kingdoms.models import LordRole, TechnologyState
from tests.mocks.discord_mock import MockGuild

from .test_kingdoms_attacks import Bundle as _AttackBundle

_CIVS = ("bretons", "perses", "byzantins")
_MONDAY = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _config_with_civs(*civs: str):
    """A default config carrying a civilization catalog (compensation)."""
    from kingdoms.mods.kingdoms.config import (
        CivilizationCondition,
        KingdomsSeasonConfig,
    )

    config = default_season_config()
    data = config.model_dump()
    data["civilizations"] = [
        CivilizationCondition(key=key, display_name=key.title()).model_dump()
        for key in civs
    ]
    return KingdomsSeasonConfig.model_validate(data)


class Bundle(_AttackBundle):
    """The attack bundle extended with the economy service."""

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.economy = EconomyService(  # type: ignore[arg-type]
            self.store, self.config, self.kingdoms, self.territories, self.attacks
        )

    async def token(self, kingdom_id: str, count: int = 1) -> None:
        """Put Explorateur tokens in the kingdom's reserve."""
        state = TechnologyState(
            _id=kingdom_id, season_id="s", tech_points=0, purchases={}
        )
        state.purchases[EXPLORATEUR_STOCK] = count
        await self.store.upsert_technology(state.to_mongo())

    async def stock_left(self, kingdom_id: str) -> int:
        """Read the kingdom's remaining Explorateur tokens."""
        state = await self.attacks.technology_state(kingdom_id)
        return int(state.purchases.get(EXPLORATEUR_STOCK, 0) or 0)

    async def fund(self, kingdom_id: str, points: int) -> None:
        await self.economy.grant_tech_points(kingdom_id, points)


async def test_boarding_requires_a_token_and_is_idempotent() -> None:
    """No token → no boarding; a live boarding re-opens untouched."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    with pytest.raises(EconomyError):
        await bundle.economy.begin_expedition(aquitaine)
    await bundle.token(aquitaine)
    doc = await bundle.economy.begin_expedition(aquitaine)
    assert doc["state"] == "boarding"
    assert doc["chef_lord_id"] is None
    assert doc["crew"] == []
    # The token is NOT consumed by the boarding — only the launch does.
    assert await bundle.stock_left(aquitaine) == 1
    again = await bundle.economy.begin_expedition(aquitaine)
    assert again == doc
    assert await bundle.stock_left(aquitaine) == 1


async def test_chef_needs_an_available_defence_point_never_consumed() -> None:
    """The defence point is a PREREQUISITE — enrolling never spends it."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.token(aquitaine)
    await bundle.economy.begin_expedition(aquitaine)
    # lord-a already spent his weekly defence: refused.
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "lord-a")
    lord.defense_used = 1
    await bundle.store.upsert_lord(lord.to_mongo())
    with pytest.raises(EconomyError):
        await bundle.economy.toggle_expedition_chef(aquitaine, "lord-a")
    # king-a still has his: enrolled — and the point stays.
    doc = await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    assert doc["chef_lord_id"] == "king-a"
    king = next(l for l in await bundle.kingdoms.lords() if l.id == "king-a")
    assert king.defense_used == 0
    # Toggle off: the chef steps down.
    doc = await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    assert doc["chef_lord_id"] is None


async def test_crew_cuts_the_travel_but_never_the_chef() -> None:
    """Each self-enrolled crew member cuts 4h; the chef never cuts it."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.token(aquitaine)
    await bundle.economy.begin_expedition(aquitaine)
    await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    doc = await bundle.economy.toggle_expedition_crew(aquitaine, "lord-a")
    assert doc["crew"] == ["lord-a"]
    # Toggling twice leaves the crew.
    doc = await bundle.economy.toggle_expedition_crew(aquitaine, "lord-a")
    assert doc["crew"] == []
    await bundle.economy.toggle_expedition_crew(aquitaine, "lord-a")
    # The chef cannot embark as a mate.
    with pytest.raises(EconomyError):
        await bundle.economy.toggle_expedition_crew(aquitaine, "king-a")
    # A locked lord cannot embark.
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "lord-b")
    lord.kingdom_id = aquitaine
    lord.marriage_locked_until = int((_MONDAY + timedelta(hours=2)).timestamp())
    await bundle.store.upsert_lord(lord.to_mongo())
    with pytest.raises(EconomyError):
        await bundle.economy.toggle_expedition_crew(aquitaine, "lord-b")


async def test_launch_consumes_the_token_and_locks_chef_and_crew() -> None:
    """48h − 4h per mate, floor 24h — the locks ride the existing guard."""
    bundle = Bundle()
    await bundle.launch_season()
    for name in ("lord-c", "lord-d", "lord-e", "lord-f", "lord-g", "lord-h"):
        await bundle.kingdoms.enroll(
            name, name.title(), LordRole.LORD, kingdom_name="Aquitaine"
        )
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.token(aquitaine)
    await bundle.economy.begin_expedition(aquitaine)
    await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    # One crew member: 48h − 4h = 44h.
    await bundle.economy.toggle_expedition_crew(aquitaine, "lord-a")
    doc = await bundle.economy.launch_expedition(aquitaine, now=_MONDAY)
    assert doc["state"] == "sailing"
    assert await bundle.stock_left(aquitaine) == 0
    ends = int((_MONDAY + timedelta(hours=44)).timestamp())
    assert doc["ends_at"] == ends
    king = next(l for l in await bundle.kingdoms.lords() if l.id == "king-a")
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "lord-a")
    assert king.exploration_locked_until == ends
    assert lord.exploration_locked_until == ends
    # A second expedition cannot start while one sails.
    await bundle.token(aquitaine)
    with pytest.raises(EconomyError):
        await bundle.economy.begin_expedition(aquitaine)
    # Floor: 7 mates would cut to 20h — the plancher holds 24h.
    await bundle.economy.resolve_due_expeditions(
        now=_MONDAY + timedelta(hours=45)
    )
    await bundle.token(aquitaine)
    doc = await bundle.economy.begin_expedition(aquitaine)
    for name in ("lord-a", "lord-c", "lord-d", "lord-e", "lord-f", "lord-g", "lord-h"):
        await bundle.economy.toggle_expedition_crew(aquitaine, name)
    await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    doc = await bundle.economy.launch_expedition(aquitaine, now=_MONDAY)
    assert doc["ends_at"] == int(
        (_MONDAY + timedelta(hours=EXPEDITION_MIN_HOURS)).timestamp()
    )


async def test_launch_without_chef_or_token_is_refused() -> None:
    """Refusals never consume anything (anti-abus invariant)."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.token(aquitaine)
    await bundle.economy.begin_expedition(aquitaine)
    with pytest.raises(EconomyError):
        await bundle.economy.launch_expedition(aquitaine)
    await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    # The token vanished between boarding and launch: refused.
    state = await bundle.attacks.technology_state(aquitaine)
    state.purchases[EXPLORATEUR_STOCK] = 0
    await bundle.store.upsert_technology(state.to_mongo())
    with pytest.raises(EconomyError):
        await bundle.economy.launch_expedition(aquitaine)
    doc = await bundle.economy.expedition(aquitaine)
    assert doc["state"] == "boarding"


async def test_resolve_draws_a_map_and_clears_every_lock() -> None:
    """The return draws a brand-new map and clears chef + crew locks."""
    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.token(aquitaine)
    await bundle.economy.begin_expedition(aquitaine)
    await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    await bundle.economy.toggle_expedition_crew(aquitaine, "lord-a")
    doc = await bundle.economy.launch_expedition(aquitaine, now=_MONDAY)
    ends = int(doc["ends_at"])
    # Before the return: nothing resolves.
    assert (
        await bundle.economy.resolve_due_expeditions(
            now=_MONDAY + timedelta(hours=43)
        )
    ) == []
    resolved = await bundle.economy.resolve_due_expeditions(
        now=_MONDAY + timedelta(hours=45)
    )
    assert len(resolved) == 1
    kingdom, kind, value, salon_id = resolved[0]
    assert kingdom.id == aquitaine
    assert kind == "map"
    assert value in [
        entry.key for entry in bundle.config.maps
    ]
    assert salon_id == 0
    territory = next(
        t for t in await bundle.territories.territories() if t.map_key == value
    )
    assert territory.owner_kingdom_id == aquitaine
    assert await bundle.economy.expedition(aquitaine) == {}
    king = next(l for l in await bundle.kingdoms.lords() if l.id == "king-a")
    lord = next(l for l in await bundle.kingdoms.lords() if l.id == "lord-a")
    assert king.exploration_locked_until is None
    assert lord.exploration_locked_until is None
    # Idempotent: a second pass resolves nothing.
    assert (
        await bundle.economy.resolve_due_expeditions(
            now=_MONDAY + timedelta(hours=46)
        )
    ) == []


async def test_exhausted_pool_pays_a_civ_and_two_points() -> None:
    """Pool dry at the return → one NEW civ (origin exploration) + 2 ⚙️."""
    bundle = Bundle(_config_with_civs(*_CIVS))
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.token(aquitaine)
    await bundle.economy.begin_expedition(aquitaine)
    await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    doc = await bundle.economy.launch_expedition(aquitaine, now=_MONDAY)
    # Drain the whole map catalog before the return.
    for entry in bundle.config.maps:
        await bundle.territories.draw_map_for(bourgogne, entry.key)
    resolved = await bundle.economy.resolve_due_expeditions(
        now=_MONDAY + timedelta(hours=49)
    )
    assert len(resolved) == 1
    kingdom, kind, value, _ = resolved[0]
    assert kind == "civ"
    assert value in _CIVS
    assert value in kingdom.civilizations
    assert kingdom.civilization_acquisitions[value]["origin"] == "exploration"
    assert await bundle.economy.wallet(aquitaine) == 2


async def test_exhausted_pool_without_civs_pays_points_only() -> None:
    """No map AND no civ left: the compensation falls back to 2 ⚙️."""
    bundle = Bundle()  # the default config carries NO civilizations
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.token(aquitaine)
    await bundle.economy.begin_expedition(aquitaine)
    await bundle.economy.toggle_expedition_chef(aquitaine, "king-a")
    await bundle.economy.launch_expedition(aquitaine, now=_MONDAY)
    for entry in bundle.config.maps:
        await bundle.territories.draw_map_for(bourgogne, entry.key)
    resolved = await bundle.economy.resolve_due_expeditions(
        now=_MONDAY + timedelta(hours=49)
    )
    assert len(resolved) == 1
    kingdom, kind, value, _ = resolved[0]
    assert kind == "tech"
    assert value == ""
    assert await bundle.economy.wallet(aquitaine) == 2


async def test_the_salon_opens_in_the_realm_category_with_its_panel() -> None:
    """The 🧭 salon lives in the kingdom's category, gated, panel pinned."""
    from kingdoms.mods.kingdoms import kingdom_expedition_panel as panel

    bundle = Bundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.token(aquitaine)
    guild = MockGuild()
    await guild.create_category("🏰 Aquitaine")
    wiring = _PanelWiring(bundle)
    kingdom = next(
        k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine
    )
    channel = await panel.open_expedition_salon_safe(guild, wiring, kingdom)
    assert channel is not None
    assert channel.category is not None
    doc = await bundle.economy.expedition(aquitaine)
    assert int(doc["salon_id"]) == int(channel.id)
    assert doc["panel_message_id"] is not None
    # The panel carries the two boarding toggles — and ⛵ stays hidden
    # until a chef is on board.
    message = await channel.fetch_message(int(doc["panel_message_id"]))
    view = message.view
    assert view is not None
    custom_ids = [item.custom_id for item in view.children]
    assert "kingdoms:expedition:chef" in custom_ids
    assert "kingdoms:expedition:crew" in custom_ids
    assert "kingdoms:expedition:launch" not in custom_ids
    # The overwrites carry the INVARIANT: @everyone denied, the bot in.
    overwrites = channel.overwrites
    assert not overwrites[guild.default_role].view_channel
    assert overwrites[guild.me].view_channel


class _PanelWiring:
    """The minimal wiring the expedition panel needs (the real shape)."""

    def __init__(self, bundle: Bundle) -> None:
        self.kingdoms_service = bundle.kingdoms
        self.economy_service = bundle.economy
