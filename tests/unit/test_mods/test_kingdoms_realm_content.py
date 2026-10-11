"""Kingdoms per-realm salon state views — unit tests (v1, D70).

Each of the 6 deployed views must land pinned in its salon, be
idempotent (a re-deploy edits the marked message, never a duplicate),
and render the live service data behind defensive lookups. The Salle
du Conseil stays empty by design and Alliances keeps its Draft
starter.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from kingdoms.mods.kingdoms.kingdom_realm_content import (
    VIEW_MARKER_PREFIX,
    deploy_realm_content,
)
from kingdoms.mods.kingdoms.kingdom_realms import ensure_realm_structure
from kingdoms.mods.kingdoms.panel_messages import message_text
from tests.mocks.discord_mock import MockGuild

pytestmark = pytest.mark.asyncio


class _Civ:
    def __init__(self, key: str, display_name: str) -> None:
        self.key = key
        self.display_name = display_name


class _Map:
    def __init__(self, key: str, display_name: str) -> None:
        self.key = key
        self.display_name = display_name


class _Limits:
    limits: ClassVar[dict[str, int]] = {"patrouille": 2}


class _Config:
    civilizations = (_Civ("azteques", "Aztèques"),)
    maps = (_Map("arabia", "Arabie"),)


class _Lord:
    def __init__(self, **kwargs: object) -> None:
        self.id = kwargs.get("id", "p1")
        self.kingdom_id = "k-1"
        self.role = kwargs.get("role", "lord")
        self.display_name = kwargs.get("display_name", "Luc")
        self.left = False
        self.attack_used = kwargs.get("attack_used", 1)
        self.defense_used = kwargs.get("defense_used", 2)
        self.married_civilization = kwargs.get("married_civilization")
        self.marriage_locked_until = None


class _KingdomsService:
    config = _Config()

    async def lords(self) -> list:
        return [
            _Lord(id="p1", role="king", display_name="Arthur"),
            _Lord(id="p2", display_name="Luc", married_civilization="azteques"),
        ]

    async def kingdoms(self) -> list:
        return []


class _Territory:
    def __init__(self, map_key: str) -> None:
        self.map_key = map_key
        self.owner_kingdom_id = "k-1"


class _TerritoriesService:
    async def territories(self) -> list:
        return [_Territory("arabia")]


class _EconomyService:
    async def wallet(self, kingdom_id: str) -> int:
        return 7


class _TechState:
    purchases: ClassVar[dict[str, int]] = {"patrouille_slot_1": 2, "garde_royale": 1}


class _AttacksService:
    async def technology_state(self, kingdom_id: str) -> _TechState:
        return _TechState()


class _Wiring:
    kingdoms_service = _KingdomsService()
    territories_service = _TerritoriesService()
    economy_service = _EconomyService()
    attacks_service = _AttacksService()


class _Kingdom:
    id: ClassVar[str] = "k-1"
    name: ClassVar[str] = "Avalon"
    validation: ClassVar[str] = "approved"
    is_gaia: ClassVar[bool] = False
    civilizations: ClassVar[list[str]] = ["azteques"]
    secured_civilizations: ClassVar[list[str]] = []
    marriage_capacity: ClassVar[int] = 3
    marriage_points: ClassVar[int] = 3
    arranged_marriage_stock: ClassVar[int] = 1
    parish_tier: ClassVar[int] = 1
    tech_points_bank: ClassVar[int] = 5


async def _deploy(guild: MockGuild) -> dict[str, bool]:
    return await deploy_realm_content(guild, _Kingdom(), _Wiring())


def _channel(guild: MockGuild, name: str):
    category = guild.categories[0]
    return next(c for c in category.channels if c.name == name)


def _live(channel) -> list:
    return [m for m in channel.messages if not getattr(m, "deleted", False)]


def _text(message) -> str:
    """The full text of a message (Components V2 views carry the text)."""
    return message_text(message)


async def test_all_six_views_land_in_their_salons() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    results = await _deploy(guild)

    assert results == {
        "le-royaume": True,
        "seigneurs": True,
        "patrouille": True,
        "territoire": True,
        "eglise": True,
        "pigeon-voyageur": True,
    }
    for salon in ("Le-Royaume", "Seigneurs", "Patrouille", "Territoire", "Paroisse", "Pigeon-Voyageur"):
        channel = _channel(guild, salon)
        assert len(_live(channel)) == 1, salon
    # the Salle du Conseil stays empty (the kingdom's discussion salon)
    assert len(_live(_channel(guild, "Salle du Conseil"))) == 0


async def test_overview_renders_the_live_data() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Le-Royaume"))[0])
    assert "Avalon" in content
    assert "Arthur + **1** seigneur" in content
    assert "7 🔬" in content
    assert f"{VIEW_MARKER_PREFIX}:le-royaume" in content


async def test_roster_lists_members_with_marriages_and_budgets() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Seigneurs"))[0])
    assert "👑 **Arthur**" in content
    assert "🎖️ **Luc**" in content
    assert "💍 Aztèques" in content
    assert "⚔️ 1 · 🛡️ 2" in content


async def test_patrol_view_shows_bought_slots_and_remaining() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Patrouille"))[0])
    assert "02h00 → 04h00" in content  # slot 2 from patrouille_slot_1
    assert "1/2" in content  # one slot bought, limit 2


async def test_territory_view_lists_owned_maps_with_display_names() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Territoire"))[0])
    assert "**Arabie**" in content
    assert "1 territoire" in content


async def test_church_view_shows_the_household_and_the_palier_ladder() -> None:
    """The Paroisse v2 (Drasah 2026-10-11): the palier block, the
    Maisonnée (one line per member, married or single), the stocks
    with their meaning, and the full palier ladder for everyone."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Paroisse"))[0])
    # ✨ the palier block — every kingdom sees its current tier
    assert "Notre Chapelle" in content
    assert "Palier : Chapelle (1/3) — Offrande : 0 🔬" in content
    assert "Verrou mariage classique : 24h" in content
    # 💒 the Maisonnée — one line per member with their state
    assert "La Maisonnée" in content
    assert "💍 Luc — Aztèques (verrou" in content  # married, lock remaining
    assert "🎖️ Arthur — célibataire · prêt à se marier 💍" in content
    # 💰 the stocks and their semantics
    assert "Points de mariage** : 3" in content
    assert "Mariages arrangés en stock** : 1" in content
    # 🔭 the ladder — every palier visible with cost and perks
    assert "Aperçu des paliers" in content
    assert "● Chapelle — 0 🔬 · verrou 24h · offrande +0 🔬" in content
    assert "▸ Église — 2 🔬 · verrou 12h · offrande +1 🔬" in content
    assert "▸ Cathédrale — 4 🔬 · verrou 6h · offrande +2 🔬" in content


async def test_views_are_idempotent_no_duplicates_on_redeploy() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    await _deploy(guild)
    await _deploy(guild)

    for salon in ("Le-Royaume", "Seigneurs", "Patrouille", "Territoire", "Paroisse", "Pigeon-Voyageur"):
        channel = _channel(guild, salon)
        assert len(_live(channel)) == 1, salon


async def test_deploy_survives_missing_services() -> None:
    class _BareWiring:
        kingdoms_service = None
        territories_service = None
        economy_service = None
        attacks_service = None

    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    results = await deploy_realm_content(guild, _Kingdom(), _BareWiring())

    assert all(results.values())
    overview = _text(_live(_channel(guild, "Le-Royaume"))[0])
    assert "Avalon" in overview  # the sheet renders from the kingdom alone
    assert "5 🔬" in overview  # wallet falls back to tech_points_bank


async def test_deploy_without_category_is_a_no_op() -> None:
    guild = MockGuild()  # no realm category provisioned
    assert await deploy_realm_content(guild, _Kingdom(), _Wiring()) == {}


class _SetupSeason:
    phase = "setup"


class _SetupKingdomsService(_KingdomsService):
    async def current_season(self) -> _SetupSeason:
        return _SetupSeason()


class _SetupWiring(_Wiring):
    kingdoms_service = _SetupKingdomsService()


async def test_setup_phase_hides_the_territories() -> None:
    """Drasah's phase rule: while the season is in setup, the
    Territoire salon shows the distribution placeholder — the owned
    maps stay secret until the admin starts the game."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    results = await deploy_realm_content(guild, _Kingdom(), _SetupWiring())

    assert results["territoire"] is True
    content = _text(_live(_channel(guild, "Territoire"))[0])
    assert "tirage aléatoire" in content
    assert "Arabie" not in content  # the owned map is NOT revealed
    # the other salons render normally
    overview = _text(_live(_channel(guild, "Le-Royaume"))[0])
    assert "Avalon" in overview
