"""Kingdoms per-realm salon state views — unit tests (v1, D70).

Each of the 6 deployed views must land pinned in its salon, be
idempotent (a re-deploy edits the marked message, never a duplicate),
and render the live service data behind defensive lookups. The Salle
du Conseil stays empty by design and Alliances keeps its Draft
starter.
"""
from __future__ import annotations

import time
from typing import ClassVar

import pytest

from kingdoms.mods.kingdoms.diplomacy import _pick_promise
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

    class attacks:  # noqa: N801 - the season's AttackSettings seam
        attacks_per_week = 2
        defenses_per_week = 3


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
        self.marriage_locked_until = kwargs.get("marriage_locked_until")


class _KingdomsService:
    config = _Config()

    async def lords(self) -> list:
        return [
            _Lord(id="p1", role="king", display_name="Arthur"),
            _Lord(
                id="p2",
                display_name="Luc",
                married_civilization="azteques",
                marriage_locked_until=int(time.time()) + 3600,
            ),
        ]

    async def kingdoms(self) -> list:
        return []


class _Territory:
    def __init__(self, map_key: str, protected_until: object = None) -> None:
        self.map_key = map_key
        self.owner_kingdom_id = "k-1"
        self.protected_until = protected_until


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


def _ids(view) -> set:
    """Every custom_id of a view, walking the Components V2 tree."""
    found: set = set()
    stack = [view]
    while stack:
        node = stack.pop()
        custom_id = getattr(node, "custom_id", None)
        if custom_id:
            found.add(str(custom_id))
        stack.extend(getattr(node, "children", []) or [])
    return found


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
    for salon in (
        "Le-Royaume",
        "Seigneurs",
        "🧱 Remparts",
        "Territoire",
        "Paroisse",
        "Pigeon-Voyageur",
    ):
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
    assert "7 ⚙️" in content
    assert f"{VIEW_MARKER_PREFIX}:le-royaume" in content


async def test_roster_lists_members_with_marriages_and_budgets() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Seigneurs"))[0])
    assert "👑 **Arthur**" in content
    assert "🎖️ **Luc**" in content
    assert "💍 Aztèques" in content
    # Drasah 15/10: the roster shows the REMAINING weekly budgets
    # (Arthur used 1 attack and 2 defenses of his 2/3 weekly quotas).
    assert "⚔️ 1/2 · 🛡️ 1/3" in content


async def test_remparts_view_shows_patrols_and_the_guard_button() -> None:
    """marche-v2 article 7: the renamed 🧱 Remparts salon keeps the patrol
    slots and gains the Garde Royale line + the King's buy button."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    channel = _channel(guild, "🧱 Remparts")
    content = _text(_live(channel)[0])
    assert "02h00 → 04h00" in content  # slot 2 from patrouille_slot_1
    assert "1/2" in content  # one slot bought, limit 2
    assert "🧱 Remparts" in content
    assert "Garde Royale" in content
    message = _live(channel)[0]
    assert "kingdoms:remparts:guard" in _ids(message.view)


async def test_territory_view_lists_owned_maps_with_display_names() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Territoire"))[0])
    assert "**Arabie**" in content
    assert "1 territoire" in content
    # No active guard: the map wears no 🛡️ badge.
    assert "protégée" not in content


async def test_territory_view_shields_the_guarded_map_with_its_end_time() -> None:
    """marche-v2 article 7: the guarded map shows 🛡️ + the protection
    END TIME next to its name."""
    from datetime import UTC, datetime, timedelta

    guarded_until = datetime.now(tz=UTC) + timedelta(hours=12)

    class _GuardedTerritories:
        async def territories(self) -> list:
            return [_Territory("arabia", protected_until=guarded_until)]

    class _GuardedWiring(_Wiring):
        territories_service = _GuardedTerritories()

    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await deploy_realm_content(guild, _Kingdom(), _GuardedWiring())

    content = _text(_live(_channel(guild, "Territoire"))[0])
    assert "**Arabie**" in content
    assert "🛡️" in content
    assert "protégée jusqu'à" in content

    # The Remparts view carries the same guard line.
    remparts = _text(_live(_channel(guild, "🧱 Remparts"))[0])
    assert "🛡️ **Arabie**" in remparts
    assert "protégée jusqu'à" in remparts


async def test_church_view_shows_the_fidèles_the_palier_ladder_and_the_legend() -> None:
    """The Paroisse v3 (Drasah 2026-10-11): the palier block with the
    royal lock line, Les Fidèles du Royaume (one line per member, the
    married ones with the lock END TIME — never a stale duration), the
    stocks in the title, the palier ladder with the Cathédrale's Lieu
    Sacrée, and the legend (verrou, offrande, jeton, sacrée)."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "Paroisse"))[0])
    # ⛪ the stocks live in the title — palier, marriage points, tokens
    assert "⛪ Paroisse — Avalon · ⛪ Chapelle · 💍 3 · 💰 1" in content
    # ✨ the palier block — every kingdom sees its current tier
    assert "Notre Chapelle" in content
    assert "Palier : Chapelle (1/3) — Offrande : 0 ⚙️" in content
    assert "Verrou mariage classique : 24h" in content
    assert "Verrou mariage royal (Roi) : 48h" in content
    # 💒 Les Fidèles du Royaume — one line per member with their state
    assert "Les Fidèles du Royaume" in content
    # Drasah 2026-10-12: the badge names the promise herself —
    # « 💍 Luc — Atotoztli (Aztèques) » — never a lone civ key
    epouse = _pick_promise("p2:Aztèques", "Aztèques")
    assert f"💍 Luc — {epouse} (Aztèques) · 🔒 verrou jusqu'à" in content
    assert "🎖️ Arthur — célibataire · prêt à se marier 💍" in content
    # 🔭 the ladder — every palier visible with cost, perks and Sacrée
    assert "Aperçu des paliers" in content
    assert "● Chapelle — 0 ⚙️ · verrou 24h · offrande +0 ⚙️" in content
    assert "▸ Église — 2 ⚙️ · verrou 12h · offrande +1 ⚙️" in content
    assert "▸ Cathédrale — 4 ⚙️ · verrou 6h · offrande +2 ⚙️ · ⛪ Lieu Sacrée" in content
    # 📖 the legend — verrou, offrande, jeton, lieu sacrée
    assert "-# 🔒 Verrou :" in content
    assert "-# 🕯️ Offrande :" in content
    assert "-# 💰 Jeton :" in content
    assert "-# ⛪ Lieu Sacrée (Cathédrale) :" in content
    # the retired stocks block and help line are gone
    assert "Points de mariage" not in content
    assert "Mariages arrangés en stock" not in content


async def test_views_are_idempotent_no_duplicates_on_redeploy() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    await _deploy(guild)
    await _deploy(guild)

    for salon in (
        "Le-Royaume",
        "Seigneurs",
        "🧱 Remparts",
        "Territoire",
        "Paroisse",
        "Pigeon-Voyageur",
    ):
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
    assert "5 ⚙️" in overview  # wallet falls back to tech_points_bank


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
