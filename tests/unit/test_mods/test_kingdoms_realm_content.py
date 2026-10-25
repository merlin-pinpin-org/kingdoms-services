"""Kingdoms per-realm salon state views — unit tests (v1, D70).

Each deployed view must land pinned in its salon, be
idempotent (a re-deploy edits the marked message, never a duplicate),
and render the live service data behind defensive lookups. The Salle
du Conseil stays empty by design and Alliances keeps its Draft
starter.
"""
from __future__ import annotations

import re
import time
from typing import Any, ClassVar

import discord
import pytest

from kingdoms.mods.kingdoms.diplomacy import _pick_promise
from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomRempartsOpenButton,
    KingdomSeigneurieOpenButton,
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from kingdoms.mods.kingdoms.kingdom_realm_content import (
    VIEW_MARKER_PREFIX,
    deploy_realm_content,
)
from kingdoms.mods.kingdoms.kingdom_realms import ensure_realm_structure
from kingdoms.mods.kingdoms.panel_messages import message_text
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser

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
    def __init__(self, map_key: str, protected_until: object = None, id: str | None = None) -> None:
        self.id = id or map_key
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
    # remparts-v2: patrol slot 1 unlocked + placed at 02h, one guard in stock.
    purchases: ClassVar[dict[str, int]] = {
        "patrouille_unlock_1": 1,
        "patrouille_slot_1": 2,
        "garde_royale_stock": 1,
    }


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


def _view_text(view) -> str:
    """The concatenated TextDisplay content of a Components V2 view."""
    chunks: list[str] = []
    stack = [view]
    while stack:
        node = stack.pop()
        content = getattr(node, "content", None)
        if content:
            chunks.append(str(content))
        stack.extend(getattr(node, "children", []) or [])
    return "\n".join(chunks)


async def test_every_view_lands_in_its_salon() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    results = await _deploy(guild)

    # restructuration-serveur-v1 (Drasah 19/10): Remparts and Seigneurie
    # are ephemeral BUTTONS now, the Pigeon-Voyageur salon had no wiring.
    # alliances-lisibilite-v1 T2 (Drasah 25/10): the 🤝 salon wears BOTH
    # the mercenary rubric and the read-only Alliances panel.
    assert results == {
        "le-royaume": True,
        "territoire": True,
        "eglise": True,
        "alliances": True,
        "alliances-panel": True,
    }
    for salon in ("🏰 le-royaume", "🗺️ territoire", "⛪ paroisse"):
        channel = _channel(guild, salon)
        assert len(_live(channel)) == 1, salon
    # the Salle du Conseil stays empty (the kingdom's discussion salon)
    assert len(_live(_channel(guild, "💬 salle du conseil"))) == 0
    # the overview carries the two new ephemeral open buttons
    overview = _live(_channel(guild, "🏰 le-royaume"))[0]
    ids = _ids(overview.view)
    assert "kingdoms:remparts:open" in ids
    assert "kingdoms:seigneurie:open" in ids


async def test_overview_renders_the_live_data() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "🏰 le-royaume"))[0])
    assert "Avalon" in content
    assert "Arthur + **1** seigneur" in content
    assert "7 ⚙️" in content
    assert f"{VIEW_MARKER_PREFIX}:le-royaume" in content


async def test_roster_lists_members_with_marriages_and_budgets() -> None:
    """restructuration-serveur-v1: the roster opens as an EPHEMERAL view
    from the Seigneurie button — its content is unchanged plus the
    public locks (marriage), never the Corbeau suivi."""
    from kingdoms.mods.kingdoms.kingdom_realm_content import (
        _build_roster,
        _gather_state,
        _strings,
    )

    state = await _gather_state(_Kingdom(), _Wiring())
    content = _view_text(_build_roster(_strings("fr"), _Kingdom(), state))
    assert "👑 **Arthur**" in content
    assert "🎖️ **Luc**" in content
    assert "💍 Aztèques" in content
    # Drasah 15/10: the roster shows the REMAINING weekly budgets
    # (Arthur used 1 attack and 2 defenses of his 2/3 weekly quotas).
    assert "⚔️ 1/2 · 🛡️ 1/3" in content
    # Drasah 19/10: the public marriage lock shows its end time.
    assert "🔒 verrou mariage" in content


async def test_remparts_view_shows_patrols_and_the_guard_button() -> None:
    """restructuration-serveur-v1: the Remparts view keeps the patrol
    slots, the Garde Royale lines and the King's placement buttons — it
    now opens as an EPHEMERAL view from the Le-Royaume button."""
    from kingdoms.mods.kingdoms.kingdom_realm_content import (
        _build_patrol,
        _gather_state,
        _strings,
    )

    state = await _gather_state(_Kingdom(), _Wiring())
    view = _build_patrol(_strings("fr"), _Kingdom(), state)
    content = _view_text(view)
    assert "Patrouille 1" in content
    assert "02h00 → 04h00" in content  # slot 1 placed at 02h (remparts-v2)
    assert "Patrouille 2" in content
    assert "verrouillée" in content  # slot 2 still locked (market first)
    assert "Gardes Royales" in content
    assert "En réserve : 1" in content  # one guard token in stock
    ids = _ids(view)
    assert "kingdoms:remparts:guard" in ids
    assert "kingdoms:remparts:patrouille:1" in ids
    assert "kingdoms:remparts:patrouille:2" in ids
    assert "kingdoms:attack:defense-manager" in ids


async def test_territory_view_lists_owned_maps_with_display_names() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await _deploy(guild)

    content = _text(_live(_channel(guild, "🗺️ territoire"))[0])
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

    class _GuardedTechState(_TechState):
        # remparts-v2: the placed guard leaves its per-map marker.
        purchases: ClassVar[dict[str, int]] = {
            "patrouille_unlock_1": 1,
            "patrouille_slot_1": 2,
            "garde_royale_stock": 0,
            "garde_royale_until:arabia": int(guarded_until.timestamp()),
        }

    class _GuardedAttacks(_AttacksService):
        async def technology_state(self, kingdom_id: str) -> _TechState:
            return _GuardedTechState()

    class _GuardedWiring(_Wiring):
        territories_service = _GuardedTerritories()
        attacks_service = _GuardedAttacks()

    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    await deploy_realm_content(guild, _Kingdom(), _GuardedWiring())

    content = _text(_live(_channel(guild, "🗺️ territoire"))[0])
    assert "**Arabie**" in content
    assert "🛡️" in content
    assert "protégée jusqu'à" in content

    # The ephemeral Remparts view carries the same guard line.
    from kingdoms.mods.kingdoms.kingdom_realm_content import (
        _build_patrol,
        _gather_state,
        _strings,
    )

    state = await _gather_state(_Kingdom(), _GuardedWiring())
    remparts = _view_text(_build_patrol(_strings("fr"), _Kingdom(), state))
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

    content = _text(_live(_channel(guild, "⛪ paroisse"))[0])
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

    for salon in ("🏰 le-royaume", "🗺️ territoire", "⛪ paroisse"):
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
    overview = _text(_live(_channel(guild, "🏰 le-royaume"))[0])
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
    content = _text(_live(_channel(guild, "🗺️ territoire"))[0])
    assert "tirage aléatoire" in content
    assert "Arabie" not in content  # the owned map is NOT revealed
    # the other salons render normally
    overview = _text(_live(_channel(guild, "🏰 le-royaume"))[0])
    assert "Avalon" in overview


class _ButtonKingdomsService(_KingdomsService):
    """Service behind the open buttons: one king (id 111) + his kingdom."""

    async def lords(self) -> list:
        return [_Lord(id="111", role="king", display_name="Arthur")]

    async def kingdoms(self) -> list:
        return [_Kingdom()]


def _wire_buttons(service: object | None) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=service,
            territories_service=_TerritoriesService(),
            economy_service=_EconomyService(),
            attacks_service=_AttacksService(),
        )
    )


def _open_match(custom_id: str) -> Any:
    """A truthy re.Match for the from_custom_id rebuild (pattern unused)."""
    found = re.match(r"kingdoms:[a-z]+:open", custom_id)
    assert found is not None
    return found


async def test_open_buttons_rebuild_from_the_wire() -> None:
    """restart-proof labels: 🧱 Remparts / 🛡️ Seigneurie in French,
    🧱 Ramparts / 🛡️ Lordship in English."""
    fr = await KingdomRempartsOpenButton.from_custom_id(
        MockInteraction(locale="fr"),  # type: ignore[arg-type]
        discord.ui.Button(),  # type: ignore[arg-type]
        _open_match("kingdoms:remparts:open"),  # type: ignore[arg-type]
    )
    assert fr.item.label == "🧱 Remparts"
    assert fr.item.custom_id == "kingdoms:remparts:open"

    en = await KingdomSeigneurieOpenButton.from_custom_id(
        MockInteraction(locale="en-US"),  # type: ignore[arg-type]
        discord.ui.Button(),  # type: ignore[arg-type]
        _open_match("kingdoms:seigneurie:open"),  # type: ignore[arg-type]
    )
    assert en.item.label == "🛡️ Lordship"
    assert en.item.custom_id == "kingdoms:seigneurie:open"


async def test_remparts_button_opens_the_ephemeral_patrol_view() -> None:
    _wire_buttons(_ButtonKingdomsService())
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="fr")

    await KingdomRempartsOpenButton("🧱 Remparts").callback(interaction)

    assert interaction.response.ephemeral is True
    message = interaction.response.message
    assert message is not None and message.view is not None
    content = _view_text(message.view)
    assert "Patrouille 1" in content
    assert "Gardes Royales" in content
    # the persistent patrol/guard/defense buttons ride the ephemeral view
    assert "kingdoms:remparts:patrouille:1" in _ids(message.view)
    assert "kingdoms:attack:defense-manager" in _ids(message.view)


async def test_seigneurie_button_opens_the_ephemeral_roster() -> None:
    _wire_buttons(_ButtonKingdomsService())
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="fr")

    await KingdomSeigneurieOpenButton("🛡️ Seigneurie").callback(interaction)

    assert interaction.response.ephemeral is True
    message = interaction.response.message
    assert message is not None and message.view is not None
    content = _view_text(message.view)
    assert "👑 **Arthur**" in content
    assert "⚔️ 1/2 · 🛡️ 1/3" in content  # the remaining weekly budgets


async def test_open_buttons_refuse_strangers_and_lost_services() -> None:
    """A non-lord gets the not_lord echo; a dead service gets the
    market_no_service echo — both ephemeral, never a crash."""
    _wire_buttons(_ButtonKingdomsService())
    stranger: Any = MockInteraction(user=MockUser(id=999), locale="fr")
    await KingdomRempartsOpenButton("🧱 Remparts").callback(stranger)
    assert "Tu ne fais partie d'aucun royaume." in (stranger.response.message.content or "")

    _wire_buttons(None)
    orphan: Any = MockInteraction(user=MockUser(id=111), locale="fr")
    await KingdomSeigneurieOpenButton("🛡️ Seigneurie").callback(orphan)
    assert "Le marché n'est pas disponible" in (orphan.response.message.content or "")


# ----------------------------------------------------------------------
# restructuration-serveur-v1 c (Drasah 19/10): the Gaïa category drops
# to ONE salon — the AI level rides the Territoire view.
# ----------------------------------------------------------------------


class _GaiaKingdom:
    id: ClassVar[str] = "k-gaia"
    name: ClassVar[str] = "Gaïa"
    validation: ClassVar[str] = "approved"
    is_gaia: ClassVar[bool] = True
    civilizations: ClassVar[list[str]] = []
    secured_civilizations: ClassVar[list[str]] = []
    marriage_capacity: ClassVar[int] = 0
    marriage_points: ClassVar[int] = 0
    arranged_marriage_stock: ClassVar[int] = 0
    parish_tier: ClassVar[int] = 0
    tech_points_bank: ClassVar[int] = 0


class _GaiaKingdomsService(_KingdomsService):
    async def kingdoms(self) -> list:
        return [_GaiaKingdom()]


class _Level4AttacksService(_AttacksService):
    async def base_ai_level(self) -> int:
        return 4


class _GaiaTerritory(_Territory):
    """A territory owned by Gaïa (the filter rides owner_kingdom_id)."""

    def __init__(self, map_key: str, protected_until: object = None, sacred: bool = False) -> None:
        super().__init__(map_key, protected_until=protected_until)
        self.owner_kingdom_id = "k-gaia"
        self.sacred = sacred


class _GaiaTerritoriesService:
    async def territories(self) -> list:
        return [_GaiaTerritory("arabia")]


class _GaiaWiring(_Wiring):
    kingdoms_service = _GaiaKingdomsService()
    attacks_service = _Level4AttacksService()
    territories_service = _GaiaTerritoriesService()


async def test_deploy_gaia_content_pins_the_single_territory_view() -> None:
    """restructuration-gaia-v1 (Drasah 24/10): the Territoire salon lives
    inside the renamed Kingdoms-Gaia structure category — the Gaïa
    realm-category lookup is gone with the retired category."""
    from kingdoms.mods.kingdoms.kingdom_realm_content import deploy_gaia_content

    guild = MockGuild()
    kingdoms = await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ")
    await guild.create_text_channel("🗺️ territoire", category=kingdoms)

    results = await deploy_gaia_content(guild, _GaiaWiring(), "fr")

    assert results == {"territoire-gaia": True}
    salon = next(
        c
        for c in guild.text_channels
        if c.name == "🗺️ territoire" and c.category and c.category.name == "🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ"
    )
    live = _live(salon)
    assert len(live) == 1
    content = _text(live[0])
    assert f"{VIEW_MARKER_PREFIX}:territoire-gaia" in content
    # the retired Patrouille info (AI level) is integrated, LIVE (4/5)
    assert "Niveau de l'IA Gaïa : 4/5" in content
    assert "Arabie" in content  # the owned territory line
    # idempotent: a re-deploy edits in place, never a duplicate
    assert await deploy_gaia_content(guild, _GaiaWiring(), "fr") == {
        "territoire-gaia": True
    }
    assert len(_live(salon)) == 1


async def test_deploy_gaia_content_survives_missing_surfaces() -> None:
    """No category / no Gaïa kingdom / missing salon: never raises."""
    from kingdoms.mods.kingdoms.kingdom_realm_content import deploy_gaia_content

    assert await deploy_gaia_content(MockGuild(), _GaiaWiring(), "fr") == {}
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    # a non-Gaïa kingdoms() list: no Gaïa view to deploy
    assert await deploy_gaia_content(guild, _Wiring(), "fr") == {}
    # wiring without a kingdoms service: no-op
    assert await deploy_gaia_content(guild, KingdomsPanelWiring(), "fr") == {}


async def test_deploy_gaia_content_reads_the_territory_badges() -> None:
    """The Gaïa territory lines carry the guard/sacred badges like the players'."""
    from datetime import UTC, datetime, timedelta

    until = datetime.now(UTC) + timedelta(hours=1)
    from kingdoms.mods.kingdoms.kingdom_realm_content import (
        _build_gaia_territory,
        _gather_state,
        _strings,
    )

    class _GuardedTerritories:
        async def territories(self) -> list:
            return [
                _GaiaTerritory("arabia", protected_until=until),
                _GaiaTerritory("sacred_map", sacred=True),
            ]

    class _Wiring2(_GaiaWiring):
        territories_service = _GuardedTerritories()

    state = await _gather_state(_GaiaKingdom(), _Wiring2())
    content = _view_text(_build_gaia_territory(_strings("fr"), _GaiaKingdom(), state, 2))
    assert "Niveau de l'IA Gaïa : 2/5" in content
    assert "🛡️" in content  # the guarded line
    assert "Lieu sacré" in content or "sacré" in content.lower()


async def test_views_are_edited_in_place_not_resent() -> None:
    """vues-silencieuses-v1: a redeploy EDITS the pinned message, it does
    not delete+send — the message object survives a re-deployment and the
    changed content lands in the SAME message (no new pin slot used)."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    await _deploy(guild)
    channel = _channel(guild, "🏰 le-royaume")
    first = _live(channel)[0]
    first._edited = False  # reset the spy before the content-changing re-run

    class _ChangedKingdom(_Kingdom):
        civilizations: ClassVar[list[str]] = ["azteques", "britons"]

    await deploy_realm_content(guild, _ChangedKingdom(), _Wiring())

    live = _live(channel)
    assert len(live) == 1
    assert live[0] is first  # edited in place, never deleted and resent
    assert first.edited is True  # the edit API call carried the new view


async def test_unchanged_view_costs_zero_api_calls() -> None:
    """vues-silencieuses-v1: the identity guard — a view whose text did not
    change is left untouched (no edit, no send)."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    await _deploy(guild)
    channel = _channel(guild, "🏰 le-royaume")
    first = _live(channel)[0]
    first._edited = False  # reset the spy before the idempotent re-run

    await _deploy(guild)

    assert first.edited is False  # identical content → zero API call
    assert len(_live(channel)) == 1


async def test_duplicate_marked_views_are_purged_on_redeploy() -> None:
    """vues-silencieuses-v1: legacy duplicates (e.g. a double send from an
    older incident) are swept — only the first marked message survives."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    await _deploy(guild)
    channel = _channel(guild, "🏰 le-royaume")
    first = _live(channel)[0]
    # simulate an older duplicate: the same view sent twice
    await channel.send(view=first.view)
    assert len(_live(channel)) == 2

    await _deploy(guild)

    live = _live(channel)
    assert len(live) == 1
    assert live[0] is first  # the ORIGINAL message keeps its pin slot

# alliances-lisibilite-v1 T2 (Drasah 25/10): the 🤝 salon's read-only
# panel — one origin emoji per civ, the marriage holder named, and the
# five restart-proof ephemeral answers.


class _LedgerKingdom(_Kingdom):
    """A hand with its append-only acquisition ledger (tranche 1)."""

    civilization_acquisitions: ClassVar[dict[str, dict[str, Any]]] = {
        "azteques": {"origin": "starter", "at": 1_760_000_000},
    }


class _AlliancesKingdomsService(_ButtonKingdomsService):
    async def kingdoms(self) -> list:
        return [_LedgerKingdom()]


class _ProjectionDiplomacyService:
    """The read-only alliance_projection seam (tranche 1)."""

    def __init__(self, projection: dict[str, Any]) -> None:
        self._projection = projection

    async def alliance_projection(self, kingdom_id: str) -> dict[str, Any]:
        return self._projection


def _wire_alliances(diplomacy_service: object | None) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=_AlliancesKingdomsService(),
            territories_service=_TerritoriesService(),
            economy_service=_EconomyService(),
            attacks_service=_AttacksService(),
            diplomacy_service=diplomacy_service,
        )
    )


async def test_alliances_panel_wears_the_origin_emojis_and_buttons() -> None:
    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])

    results = await deploy_realm_content(guild, _LedgerKingdom(), _Wiring(), "fr")

    assert results["alliances-panel"] is True
    panel = next(
        m
        for m in _live(_channel(guild, "\U0001f91d alliances"))
        if f"{VIEW_MARKER_PREFIX}:alliances-panel" in _text(m)
    )
    content = _text(panel)
    # one emoji per acquisition origin — 🎲 for the starter draft
    assert "\U0001f3b2 **Azt\u00e8ques**" in content
    # the marriage holder is named (💍 {lord})
    assert "— \U0001f48d Luc" in content
    # the help line points at the Scriptorium's Diplomatie rubric
    assert "Scriptorium" in content and "Diplomatie" in content
    # the five ephemeral answers ride the panel, restart-proof custom ids
    ids = _ids(panel.view)
    for custom_id in (
        "kingdoms:alliances:next",
        "kingdoms:alliances:locked",
        "kingdoms:alliances:history",
        "kingdoms:alliances:durations",
        "kingdoms:alliances:names",
    ):
        assert custom_id in ids


async def test_alliances_panel_reads_the_handshake_emoji_before_the_ledger() -> None:
    """A pre-ledger season (a hand older than tranche 1) reads 🤝."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    await _deploy(guild)

    panel = next(
        m
        for m in _live(_channel(guild, "\U0001f91d alliances"))
        if f"{VIEW_MARKER_PREFIX}:alliances-panel" in _text(m)
    )
    assert "\U0001f91d **Azt\u00e8ques**" in _text(panel)


def _alliances_interaction(guild: MockGuild) -> Any:
    return MockInteraction(
        user=MockUser(id=111),
        guild=guild,
        channel=_channel(guild, "\U0001f91d alliances"),
        locale="fr",
    )


async def test_next_button_previews_the_cycle_projection() -> None:
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import KingdomAlliancesNextButton

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    _wire_alliances(
        _ProjectionDiplomacyService(
            {
                "held": ["azteques"],
                "kept": ["azteques"],
                "arriving": [],
                "dropping": [],
                "near_unlocks": [],
                "married_elsewhere": [],
                "acquisitions": dict(_LedgerKingdom.civilization_acquisitions),
            }
        )
    )

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesNextButton("\U0001f52d Prochain cycle").callback(interaction)

    assert interaction.response.ephemeral is True
    message = interaction.response.message
    assert message is not None
    content = message.content or ""
    assert "\U0001f52d Prochain cycle — Avalon" in content
    assert "\u2705 Gard\u00e9es : Azt\u00e8ques" in content


async def test_locked_button_names_the_locked_civ_and_why() -> None:
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import KingdomAlliancesLockedButton

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    # an empty projection: the hand itself reads locked
    _wire_alliances(
        _ProjectionDiplomacyService(
            {
                "held": [],
                "kept": [],
                "arriving": [],
                "dropping": [],
                "near_unlocks": [],
                "married_elsewhere": [],
                "acquisitions": {},
            }
        )
    )

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesLockedButton("\U0001f6ab Verrouill\u00e9es").callback(interaction)

    assert interaction.response.ephemeral is True
    content = (interaction.response.message.content or "") if interaction.response.message else ""
    assert "\U0001f6ab **Azt\u00e8ques** — condition non remplie" in content


async def test_history_durations_and_names_buttons_read_the_ledger() -> None:
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import (
        KingdomAlliancesDurationsButton,
        KingdomAlliancesHistoryButton,
        KingdomAlliancesNamesButton,
    )

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    _wire_alliances(None)  # these three answers never touch the diplomacy seam

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesHistoryButton("\U0001f4d6 Historique").callback(interaction)
    content = interaction.response.message.content if interaction.response.message else ""
    assert "\U0001f4d6 Historique — Avalon" in content
    assert "\U0001f3b2 **Azt\u00e8ques** — " in content  # the origin emoji + date

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesDurationsButton("\u23f3 Dur\u00e9es").callback(interaction)
    content = interaction.response.message.content if interaction.response.message else ""
    assert "\u23f3 Dur\u00e9es — Avalon" in content
    assert "d\u00e9tenue depuis " in content

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesNamesButton("\U0001f524 Noms").callback(interaction)
    content = interaction.response.message.content if interaction.response.message else ""
    assert "\U0001f524 Noms — Avalon" in content
    assert "techtree : Aztecs" in content  # display name <-> provider name
