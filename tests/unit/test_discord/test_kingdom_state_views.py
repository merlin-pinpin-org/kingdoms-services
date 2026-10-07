"""Unit tests for the per-kingdom state views (D70, tranche ②)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from kingdoms.discord.kingdom_setup import _slug
from kingdoms.discord.kingdom_state_views import (
    EGLISE_REGLES,
    STATE_MARKERS,
    STRINGS,
    KingdomAlliancesInfoButton,
    KingdomEgliseActionButton,
    KingdomEgliseReglesButton,
    KingdomTerritoryDetailButton,
    _cron_label,
    _protection_window_label,
    _seigneurs_content,
    _territoire_content,
    _territoire_view,
    refresh_kingdom_state_views,
)
from kingdoms.discord.kingdom_structure import ensure_kingdom_structure
from kingdoms.discord.ui.persistent import _page_renderers
from tests.mocks.discord_mock import MockGuild, MockMember


class _FakeLord:
    """A lords() item double with the LordModel shape."""

    def __init__(
        self,
        player_id: str,
        kingdom_id: str,
        *,
        role: str = "lord",
        display_name: str = "Lord",
        left: bool = False,
        in_queue: bool = False,
        attack_used: int = 0,
        defense_used: int = 0,
        married_civilization: str | None = None,
    ) -> None:
        self.id = player_id
        self.kingdom_id = kingdom_id
        self.role = SimpleNamespace(value=role)
        self.display_name = display_name
        self.left = left
        self.in_queue = in_queue
        self.attack_used = attack_used
        self.defense_used = defense_used
        self.married_civilization = married_civilization


class _FakeKingdom:
    """A kingdoms() item double with the KingdomModel shape."""

    def __init__(
        self,
        kingdom_id: str,
        name: str,
        *,
        is_gaia: bool = False,
        civilizations: list[str] | None = None,
        secured: list[str] | None = None,
        tech_bank: int = 0,
        marriage_capacity: int = 0,
    ) -> None:
        self.id = kingdom_id
        self.name = name
        self.is_gaia = is_gaia
        self.civilizations = civilizations or []
        self.secured_civilizations = secured or []
        self.tech_points_bank = tech_bank
        self.marriage_capacity = marriage_capacity


class _FakeTerritory:
    """A territories() item double with the TerritoryModel shape."""

    def __init__(self, territory_id: str, map_key: str, owner: str, *, protected: bool = False) -> None:
        self.id = territory_id
        self.map_key = map_key
        self.owner_kingdom_id = owner
        self._protected = protected

    def is_protected_at(self, now: datetime) -> bool:
        """Mirror the anti-attack shield of the real model."""
        del now
        return self._protected


class _FakeProtection:
    """The protection crons of the season configuration."""

    start_cron = "30 23 * * SUN"
    end_cron = "0 10 * * MON"


class _FakeAttacks:
    """The attacks seam double behind the economy service."""

    def __init__(self, state: Any) -> None:
        self._state = state

    async def technology_state(self, kingdom_id: str) -> Any:
        """Answer with the recorded technology state."""
        del kingdom_id
        return self._state


class _FakeConfig:
    """The season configuration double."""

    def __init__(self) -> None:
        self.ages = (SimpleNamespace(key="dark_age", gaia_ai_level=2),)
        self.attacks = SimpleNamespace(attacks_per_week=1, defenses_per_week=1)
        self.protection = _FakeProtection()


class _FakeKingdomsService:
    """A kingdoms service double answering from lists."""

    def __init__(
        self,
        kingdoms: list[_FakeKingdom],
        lords: list[_FakeLord],
        season: Any | None = None,
    ) -> None:
        self._kingdoms = kingdoms
        self._lords = lords
        self._season = season or SimpleNamespace(current_age_key="dark_age")
        self.config = _FakeConfig()

    async def kingdoms(self) -> list[_FakeKingdom]:
        """Return the recorded kingdoms."""
        return self._kingdoms

    async def lords(self) -> list[_FakeLord]:
        """Return the recorded lords."""
        return self._lords

    async def current_season(self) -> Any:
        """Return the recorded season."""
        return self._season


class _FakeTerritoryService:
    """A territory service double answering from a list."""

    def __init__(self, territories: list[_FakeTerritory]) -> None:
        self._territories = territories

    async def territories(self) -> list[_FakeTerritory]:
        """Return the recorded territories."""
        return self._territories


async def _provisioned_guild(name: str, lord: _FakeLord) -> MockGuild:
    """Create a guild with the full kingdom structure of tranche ①."""
    guild = MockGuild()
    member = MockMember(name="roi", guild=guild)
    guild.add_member(member)
    lord.id = str(member.id)
    lord.kingdom_id = "k-1"
    provisioning = _FakeKingdomsService([_FakeKingdom("k-1", name)], [lord])
    await ensure_kingdom_structure(guild, provisioning, "k-1", name)
    return guild


def _kingdom() -> _FakeKingdom:
    """The reference kingdom used by the refresh tests."""
    return _FakeKingdom(
        "k-1",
        "Aquitaine",
        civilizations=["Francs", "Bretons"],
        secured=["Bretons"],
        tech_bank=3,
        marriage_capacity=1,
    )


async def _refresh(guild: MockGuild, service: _FakeKingdomsService, kingdom: Any) -> dict[str, bool]:
    """Run one refresh with the reference territory/economy doubles."""
    territories = _FakeTerritoryService(
        [
            _FakeTerritory("t-1", "arabia", "k-1", protected=True),
            _FakeTerritory("t-2", "megarandom", "k-1"),
        ]
    )
    economy = SimpleNamespace(_attacks=_FakeAttacks(SimpleNamespace(tech_points=2, purchases={"sabotage": 1})))
    return await refresh_kingdom_state_views(guild, "fr", service, economy, territories, kingdom)


def _channel_messages(guild: MockGuild, display_name: str) -> list[Any]:
    """The messages of one kingdom salon, by display name."""
    for category in guild.categories:
        for channel in category.channels:
            if _slug(channel.name) == _slug(display_name):
                return list(channel.messages)
    return []


async def test_refresh_posts_one_marked_view_per_salon() -> None:
    lord = _FakeLord("1", "k-1", role="king", display_name="Roi", married_civilization="Francs")
    guild = await _provisioned_guild("Aquitaine", lord)
    service = _FakeKingdomsService([_FakeKingdom("k-1", "Gaïa", is_gaia=True), _kingdom()], [lord])
    report = await _refresh(guild, service, _kingdom())
    assert set(report) == {"royaume", "seigneurs", "territoire", "alliances", "eglise", "patrouille"}
    # the marker contract holds on every salon: one marked message each
    salons = {
        "royaume": "🏰 Le-Royaume",
        "seigneurs": "🎖️ Seigneurs",
        "territoire": "🗺️ Territoire",
        "alliances": "📜 Alliances",
        "eglise": "⛪ Église",
        "patrouille": "🛡️ Patrouille",
    }
    for salon, display in salons.items():
        messages = _channel_messages(guild, display)
        assert len(messages) == 1, salon
        assert STATE_MARKERS[salon] in (messages[0].content or "")


async def test_refresh_is_idempotent_and_edits_in_place() -> None:
    lord = _FakeLord("1", "k-1", role="king", display_name="Roi")
    guild = await _provisioned_guild("Aquitaine", lord)
    service = _FakeKingdomsService([_kingdom()], [lord])
    await _refresh(guild, service, _kingdom())
    before = _channel_messages(guild, "🏰 Le-Royaume")[0]
    await _refresh(guild, service, _kingdom())
    messages = _channel_messages(guild, "🏰 Le-Royaume")
    assert len(messages) == 1
    assert messages[0] is before
    assert before.edited is True


async def test_gaia_never_gets_state_views() -> None:
    guild = MockGuild()
    service = _FakeKingdomsService([], [])
    report = await refresh_kingdom_state_views(
        guild, "fr", service, None, None, _FakeKingdom("k-gaia", "Gaïa", is_gaia=True)
    )
    assert report == {}


async def test_missing_structure_is_a_noop_report() -> None:
    lord = _FakeLord("1", "k-1", role="king", display_name="Roi")
    guild = MockGuild()  # no category provisioned
    service = _FakeKingdomsService([_kingdom()], [lord])
    report = await _refresh(guild, service, _kingdom())
    assert report == {}


def test_territoire_view_carries_detail_and_pager_buttons() -> None:
    territories = [_FakeTerritory(f"t-{i}", f"map-{i}", "k-1") for i in range(7)]
    view = _territoire_view(territories, 0)
    custom_ids = [item.custom_id for item in view.children]
    assert sum(1 for cid in custom_ids if cid.startswith("kingdoms:terr:detail:")) == 5
    assert "kingdoms_terr:page:1" in custom_ids
    assert not any(cid == "kingdoms_terr:page:0" for cid in custom_ids)


def test_territoire_content_pages_five_cards() -> None:
    strings = STRINGS["fr"]
    territories = [_FakeTerritory(f"t-{i}", f"map-{i}", "k-1", protected=i == 0) for i in range(7)]
    content = _territoire_content(strings, _kingdom(), territories, 0, datetime.now(UTC))
    assert "map-0" in content and "map-4" in content
    assert "map-5" not in content
    assert "🛡️" in content
    assert "Page 1/2" in content


def test_seigneurs_content_shows_budgets_and_departed() -> None:
    strings = STRINGS["fr"]
    members = [
        _FakeLord("1", "k-1", role="king", display_name="Roi", married_civilization="Francs"),
        _FakeLord("2", "k-1", display_name="Vavasseur", attack_used=1),
        _FakeLord("3", "k-1", display_name="Gone", left=True),
    ]
    content = _seigneurs_content(strings, _kingdom(), members, (1, 1))
    assert "⚔️ 1 · 🛡️ 1" in content
    assert "⚔️ 0" in content
    assert "parti" in content
    assert "💍 Francs" in content


def test_cron_label_renders_the_protection_window() -> None:
    assert _cron_label("30 23 * * SUN") == "dimanche 23h30"
    assert _cron_label("0 10 * * MON") == "lundi 10h"
    label = _protection_window_label(_FakeConfig())
    assert label == "dimanche 23h30 → lundi 10h"


def test_eglise_regles_is_the_designer_french_voice() -> None:
    assert "D59" in EGLISE_REGLES
    assert "D64" in EGLISE_REGLES


@pytest.mark.asyncio
async def test_dynamic_buttons_rebuild_from_custom_id() -> None:
    match = re.fullmatch(r"kingdoms:terr:detail:(?P<map>[a-z0-9_-]+)", "kingdoms:terr:detail:arabia")
    assert match is not None
    rebuilt = await KingdomTerritoryDetailButton.from_custom_id(None, None, match)  # type: ignore[arg-type]
    assert rebuilt.map_key == "arabia"
    assert rebuilt.item.custom_id == "kingdoms:terr:detail:arabia"

    match = re.fullmatch(r"kingdoms:alliances:info", "kingdoms:alliances:info")
    assert match is not None
    info = await KingdomAlliancesInfoButton.from_custom_id(None, None, match)  # type: ignore[arg-type]
    assert info.item.custom_id == "kingdoms:alliances:info"

    match = re.fullmatch(r"kingdoms:eglise:action:(?P<action>[a-z_]+)", "kingdoms:eglise:action:marier")
    assert match is not None
    action = await KingdomEgliseActionButton.from_custom_id(None, None, match)  # type: ignore[arg-type]
    assert action.action == "marier"

    match = re.fullmatch(r"kingdoms:eglise:regles", "kingdoms:eglise:regles")
    assert match is not None
    regles = await KingdomEgliseReglesButton.from_custom_id(None, None, match)  # type: ignore[arg-type]
    assert regles.item.custom_id == "kingdoms:eglise:regles"


def test_registration_declares_the_territory_pager_renderer() -> None:
    from kingdoms.discord.kingdom_state_views import register_kingdoms_state_pager

    register_kingdoms_state_pager()
    assert "kingdoms_terr" in _page_renderers()
