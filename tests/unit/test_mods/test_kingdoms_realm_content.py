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
    # alliances-lisibilite-v1 T3 (Drasah 25/10): the 🤝 salon wears ONE
    # unified panel — the mercenary rubric is merged into it.
    assert results == {
        "le-royaume": True,
        "territoire": True,
        "eglise": True,
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
    # T3: the unified view reads a LEGEND (badge colour ↔ origin emoji)
    assert "L\u00e9gende" in content
    assert "\U0001f3b2 Draft starter" in content
    # alliances-panel-v3: the 💍 married line is GONE — the legend and the
    # crest's origin emoji already tell the provenance (Drasah's call)
    assert "\U0001f48d Azt\u00e8ques — Luc" not in content
    # the help line points at the Scriptorium's Diplomatie rubric
    assert "Scriptorium" in content and "Diplomatie" in content
    # alliances-panel-v3: the intro explains the hand and points at 📅
    assert "ta main" in content
    assert "8 civilisations" in content
    assert "\U0001f4c5" in content
    # alliances-panel-v4: 🤝 is GONE from the legend — after a reset
    # everything is defined and traceable
    assert "saison ant\u00e9rieure" not in content
    # alliances-panel-v4: the order Drasah asked —
    # intro → help ❓ → legend → signature
    assert (
        content.index("Ici se trouve ta main")
        < content.index("Scriptorium")
        < content.index("L\u00e9gende")
        < content.index(f"-# ||{VIEW_MARKER_PREFIX}")
    )
    # the visible « main : » line is GONE (doublon with the crest names)
    assert "\n-# main :" not in content
    # alliances-panel-v4: the signature keeps ONLY the marker + a SILENT
    # 8-hex fingerprint — no civilization names (the identity guard still
    # sees the text change when the hand changes)
    assert f"-# ||{VIEW_MARKER_PREFIX}:alliances-panel ·" in content
    assert re.search(rf"alliances-panel · [0-9a-f]{{8}}\|\|", content)
    assert "main : azteques" not in content
    # the five ephemeral answers ride the panel, restart-proof custom ids
    ids = _ids(panel.view)
    for custom_id in (
        "kingdoms:alliances:cycle",
        "kingdoms:alliances:next",
        "kingdoms:alliances:locked",
        "kingdoms:alliances:durations",
        "kingdoms:alliances:history",
    ):
        assert custom_id in ids
    # 🔤 Noms is gone — the names ride the crest mosaic now
    assert "kingdoms:alliances:names" not in ids
    # 📖 Historique rides LAST
    rows = [
        c
        for c in _view_components(panel.view)
        if type(c).__name__ == "ActionRow"
    ]
    button_ids = [
        getattr(child, "custom_id", None)
        for row in rows
        for child in (getattr(row, "children", None) or ())
    ]
    button_ids = [cid for cid in button_ids if cid]
    assert button_ids[-1] == "kingdoms:alliances:history"
    assert button_ids[0] == "kingdoms:alliances:cycle"


async def test_alliances_panel_renders_a_pre_ledger_hand() -> None:
    """A pre-ledger season (a hand older than tranche 1) still renders —
    the 🤝 fallback is now SILENT (alliances-panel-v4: 🤝
    left the legend, everything is traceable after a reset)."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])

    await _deploy(guild)

    panel = next(
        m
        for m in _live(_channel(guild, "\U0001f91d alliances"))
        if f"{VIEW_MARKER_PREFIX}:alliances-panel" in _text(m)
    )
    text = _text(panel)
    assert "saison ant\u00e9rieure" not in text


def _view_components(view: Any) -> list:
    """Walk the full Components V2 tree of a view."""
    found: list = []
    stack = [view]
    while stack:
        node = stack.pop()
        found.append(node)
        stack.extend(getattr(node, "children", ()) or ())
        stack.extend(getattr(node, "items", ()) or ())
    return found


async def test_alliances_panel_references_the_mosaic_via_a_media_gallery() -> None:
    """T3b: Components V2 hides UNREFERENCED attachments — the mosaic must
    ride the panel message AND be referenced by a MediaGallery, or the
    blasons never render."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])

    await deploy_realm_content(guild, _LedgerKingdom(), _Wiring(), "fr")

    panel = next(
        m
        for m in _live(_channel(guild, "\U0001f91d alliances"))
        if f"{VIEW_MARKER_PREFIX}:alliances-panel" in _text(m)
    )
    # the mosaic rides the message
    assert getattr(panel, "attachments", None), "the crest mosaic must be attached"
    # and is referenced by a top-level MediaGallery pointing at the attachment
    galleries = [
        c for c in _view_components(panel.view) if type(c).__name__ == "MediaGallery"
    ]
    assert galleries, "the mosaic must be referenced by a MediaGallery"
    assert any(
        str(getattr(item, "media", "")) == "attachment://alliances.png"
        for gallery in galleries
        for item in getattr(gallery, "items", ())
    )
    # the silent 8-hex fingerprint keeps the identity guard honest across
    # hand edits (alliances-panel-v4: no civ names in the signature)
    assert re.search(r"alliances-panel · [0-9a-f]{8}\|\|", _text(panel))


async def test_alliances_panel_edits_in_place_when_the_hand_grows() -> None:
    """T3b identity guard: the hand fingerprint line changes the body text,
    so a hand growth edits the marked panel (new mosaic) instead of resending."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    channel = _channel(guild, "\U0001f91d alliances")
    marker = f"{VIEW_MARKER_PREFIX}:alliances-panel"

    await deploy_realm_content(guild, _LedgerKingdom(), _Wiring(), "fr")
    before = [m for m in _live(channel) if marker in _text(m)]
    assert len(before) == 1

    _LedgerKingdom.civilizations = ["azteques", "britons"]
    _LedgerKingdom.civilization_acquisitions = {
        "azteques": {"origin": "starter", "at": 1_760_000_000},
        "britons": {"origin": "condition", "at": 1_760_000_001},
    }
    try:
        await deploy_realm_content(guild, _LedgerKingdom(), _Wiring(), "fr")
        after = [m for m in _live(channel) if marker in _text(m)]
        # one panel only, and it IS the original message, edited in place
        assert len(after) == 1
        assert after[0] is before[0]
        assert getattr(after[0], "edited", False) or after[0].edited
        assert getattr(after[0], "attachments", None)
        # the silent fingerprint changed with the hand → the edit fired
        before_fp = re.search(
            r"alliances-panel · ([0-9a-f]{8})\|\|", _text(before[0])
        )
        after_fp = re.search(
            r"alliances-panel · ([0-9a-f]{8})\|\|", _text(after[0])
        )
        assert before_fp and after_fp and before_fp.group(1) != after_fp.group(1)
    finally:
        _LedgerKingdom.civilizations = ["azteques"]
        _LedgerKingdom.civilization_acquisitions = {
            "azteques": {"origin": "starter", "at": 1_760_000_000},
        }


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
    # alliances-panel-v2: a short intro sentence frames the projection
    assert "Projection" in content


async def test_locked_button_lists_only_the_married_elsewhere_civs() -> None:
    """alliances-panel-v2: 🚫 shows ONLY the civs married to OTHER kingdoms —
    the ~48-line catalogue sheet overflowed 2000 chars and broke the button."""
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import KingdomAlliancesLockedButton

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    # a projection with one civ married to a rival kingdom
    _wire_alliances(
        _ProjectionDiplomacyService(
            {
                "held": [],
                "kept": [],
                "arriving": [],
                "dropping": [],
                "near_unlocks": [],
                "married_elsewhere": ["azteques"],
                "acquisitions": {},
            }
        )
    )

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesLockedButton("\U0001f6ab Verrouill\u00e9es").callback(interaction)

    assert interaction.response.ephemeral is True
    content = (interaction.response.message.content or "") if interaction.response.message else ""
    # alliances-panel-v4: the title says it all, the invisible 💍 rule
    # line is GONE
    assert "\U0001f6ab Verrouill\u00e9es par les mariages des autres royaumes" in content
    assert "Uniquement les civilisations mari\u00e9es" not in content
    assert "\U0001f6ab **Azt\u00e8ques** — mari\u00e9e \u00e0 un autre royaume" in content
    # the old condition-locked sheet is GONE
    assert "condition non remplie" not in content

    # ...and a hand with no rival marriages reads the none-married line
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
    content = (interaction.response.message.content or "") if interaction.response.message else ""
    assert "Aucune civilisation verrouill\u00e9e par mariage" in content


async def test_history_lists_only_the_lost_civilizations() -> None:
    """alliances-panel-v2: 📖 Historique shows ONLY the civs acquired once
    then LOST — a hand still holding its acquisitions reads "intacte"."""
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import KingdomAlliancesHistoryButton

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    _wire_alliances(None)  # the history answer never touches the diplomacy seam

    # intact hand: azteques is acquired AND still held → nothing lost
    interaction = _alliances_interaction(guild)
    await KingdomAlliancesHistoryButton("\U0001f4d6 Historique").callback(interaction)
    content = interaction.response.message.content if interaction.response.message else ""
    assert "\U0001f4d6 Historique — Avalon" in content
    assert "Aucune civilisation perdue" in content
    assert "Azt\u00e8ques" not in content

    # an emptied hand: azteques was acquired once, gone since → LOST
    saved = _LedgerKingdom.civilizations
    _LedgerKingdom.civilizations = []
    try:
        interaction = _alliances_interaction(guild)
        await KingdomAlliancesHistoryButton("\U0001f4d6 Historique").callback(interaction)
        content = interaction.response.message.content if interaction.response.message else ""
        assert "\U0001f3b2 **Azt\u00e8ques** — " in content  # the origin emoji + date
    finally:
        _LedgerKingdom.civilizations = saved


async def test_durations_button_reads_the_ledger() -> None:
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import KingdomAlliancesDurationsButton

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    _wire_alliances(None)  # the durations answer never touches the diplomacy seam

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesDurationsButton("\u23f3 Dur\u00e9es").callback(interaction)
    content = interaction.response.message.content if interaction.response.message else ""
    assert "\u23f3 Dur\u00e9es — Avalon" in content
    assert "d\u00e9tenue depuis " in content


async def test_alliances_help_line_names_the_real_scriptorium() -> None:
    """alliances-panel-v2: when the Scriptorium salon resolves, the help
    line is a REAL clickable mention instead of a static pointer."""
    from kingdoms.mods.kingdoms.kingdom_realm_content import (
        _build_alliances_panel,
        _strings,
    )

    view = _build_alliances_panel(
        _strings("fr"), _LedgerKingdom(), {"civ_names": {}, "scriptorium_mention": "<#42>"}
    )
    text = _view_text(view)
    assert "<#42>" in text
    assert "Diplomatie" in text
    # and without a resolvable salon the static fallback keeps the word
    view = _build_alliances_panel(_strings("fr"), _LedgerKingdom(), {"civ_names": {}})
    text = _view_text(view)
    assert "Scriptorium" in text and "Diplomatie" in text
    assert "<#" not in text


async def test_alliances_ephemeral_answers_self_destruct() -> None:
    """alliances-panel-v2: every ephemeral answer deletes itself after
    ALLIANCES_EPHEMERAL_TTL — the old bubbles must not pile up in the salon."""
    import asyncio

    import kingdoms.mods.kingdoms.kingdom_alliances_panel as panel_mod
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

    saved_ttl = panel_mod.ALLIANCES_EPHEMERAL_TTL
    panel_mod.ALLIANCES_EPHEMERAL_TTL = 0.0
    try:
        interaction = _alliances_interaction(guild)
        await KingdomAlliancesNextButton("\U0001f52d Prochain cycle").callback(interaction)
        assert interaction.response.ephemeral is True
        assert not getattr(interaction, "original_deleted", False)
        await asyncio.sleep(0.05)
        assert interaction.original_deleted is True
    finally:
        panel_mod.ALLIANCES_EPHEMERAL_TTL = saved_ttl


async def test_cycle_button_counts_down_to_the_next_switch() -> None:
    """alliances-panel-v3: 📅 answers with the time left before the next
    cycle switch — the beta slots override the weekly cron."""
    from datetime import timedelta

    from kingdoms.mods.kingdoms.game_clock import game_now
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import KingdomAlliancesCycleButton

    import kingdoms.mods.kingdoms.kingdom_alliances_panel as panel_mod

    # a FROZEN game clock: the countdown must read exactly 2h 5m, the
    # milliseconds between two real game_now() calls would flake it
    fixed_now = game_now()

    class _CycleSeason:
        phase = "started"

        def __init__(self) -> None:
            self.started_at = fixed_now - timedelta(days=3)
            self.beta_cycle_slots = [fixed_now + timedelta(hours=2, minutes=5)]

    class _CycleKingdomsService(_AlliancesKingdomsService):
        def __init__(self) -> None:
            self._season = _CycleSeason()

        async def current_season(self) -> object:
            return self._season

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=_CycleKingdomsService(),
            territories_service=_TerritoriesService(),
            economy_service=_EconomyService(),
            attacks_service=_AttacksService(),
            diplomacy_service=None,
        )
    )

    saved_game_now = panel_mod.game_now
    panel_mod.game_now = lambda: fixed_now
    try:
        interaction = _alliances_interaction(guild)
        await KingdomAlliancesCycleButton("\U0001f4c5").callback(interaction)
    finally:
        panel_mod.game_now = saved_game_now

    assert interaction.response.ephemeral is True
    content = (interaction.response.message.content or "") if interaction.response.message else ""
    assert "\U0001f4c5 Prochain cycle — Avalon" in content
    assert "dans **2h 5m**" in content


async def test_cycle_button_without_a_season_reads_not_scheduled() -> None:
    """alliances-panel-v3: no season (or a service without one) → the 📅
    button still answers, with the plain "not scheduled" line."""
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import KingdomAlliancesCycleButton

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    _wire_alliances(None)  # kingdoms service without current_season → no schedule

    interaction = _alliances_interaction(guild)
    await KingdomAlliancesCycleButton("\U0001f4c5").callback(interaction)
    content = (interaction.response.message.content or "") if interaction.response.message else ""
    assert "Aucune saison active" in content


async def test_alliances_answers_replace_the_prior_ephemeral() -> None:
    """alliances-panel-v3: ONE bubble per (player, salon) — a new click
    deletes the player's prior ephemeral instead of piling bubbles up."""
    import kingdoms.mods.kingdoms.kingdom_alliances_panel as panel_mod
    from kingdoms.mods.kingdoms.kingdom_alliances_panel import (
        KingdomAlliancesDurationsButton,
        KingdomAlliancesHistoryButton,
    )

    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    _wire_alliances(None)

    first = _alliances_interaction(guild)
    await KingdomAlliancesHistoryButton("\U0001f4d6 Historique").callback(first)
    assert first.response.ephemeral is True
    assert not getattr(first, "original_deleted", False)

    second = _alliances_interaction(guild)
    await KingdomAlliancesDurationsButton("\u23f3 Dur\u00e9es").callback(second)
    assert second.response.ephemeral is True
    # the prior bubble is deleted, the new one lives ALONE
    assert getattr(first, "original_deleted", False) is True
    assert not getattr(second, "original_deleted", False)
    assert len(panel_mod._ANSWER_EPHEMERAL_STATE) == 1

    # cleanup: cancel the pending auto-delete timer of the live bubble
    for task in list(panel_mod._ANSWER_EPHEMERAL_TIMERS.values()):
        task.cancel()


async def test_alliances_panel_merges_the_mercenary_contracts() -> None:
    """T3: a live mercenary contract reads as a compact ⚔️ line on the panel."""
    guild = MockGuild()

    class _MercLord(_Lord):
        def __init__(self) -> None:
            super().__init__(id="p3", display_name="Léon")
            self.mercenary_civilization = "azteques"
            self.mercenary_uses_left = 2

    class _MercKingdomsService(_KingdomsService):
        async def kingdoms(self) -> list:
            return [_LedgerKingdom()]

        async def lords(self) -> list:
            return [_MercLord()]

    class _MercWiring:
        kingdoms_service = _MercKingdomsService()
        territories_service = _TerritoriesService()
        economy_service = _EconomyService()
        attacks_service = _AttacksService()

    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    results = await deploy_realm_content(guild, _LedgerKingdom(), _MercWiring(), "fr")
    assert results["alliances-panel"] is True
    panel = next(
        m
        for m in _live(_channel(guild, "\U0001f91d alliances"))
        if f"{VIEW_MARKER_PREFIX}:alliances-panel" in _text(m)
    )
    content = _text(panel)
    assert "⚔️ **Léon** — Aztèques (2 usage(s) de draft)" in content


async def test_legacy_mercenary_rubric_is_swept_on_deploy() -> None:
    """T3: the standalone T2c rubric's marked message never survives a refresh."""
    guild = MockGuild()
    await ensure_realm_structure(guild, _LedgerKingdom(), [])
    channel = _channel(guild, "\U0001f91d alliances")
    await channel.send(f"-# {VIEW_MARKER_PREFIX}:alliances")

    results = await deploy_realm_content(guild, _LedgerKingdom(), _Wiring(), "fr")

    assert results["alliances-panel"] is True
    panel_marker = f"{VIEW_MARKER_PREFIX}:alliances-panel"
    rubric_marker = f"{VIEW_MARKER_PREFIX}:alliances"
    survivors = [
        m for m in _live(channel) if rubric_marker in _text(m) and panel_marker not in _text(m)
    ]
    assert survivors == []
    # the unified panel itself is alive
    assert any(panel_marker in _text(m) for m in _live(channel))
