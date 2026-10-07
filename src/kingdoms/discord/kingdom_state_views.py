"""Kingdoms mod — the per-kingdom state views (tranche ② of the D70 design).

D70 (kingdoms repo DECISIONS.md): every salon of the private category
``Royaume <Nom>`` provisioned by tranche ① is a **silent state view**
the bot keeps fresh, except the Salle du Conseil (the only talking
channel). This module renders those views from the live season state:

- 🏰 Le-Royaume — the kingdom fiche (king, roster size, treasury,
  territories, alliances, marriages, Gaïa AI level, technologies,
  royal guard coverage);
- 🎖️ Seigneurs — the kingdom roster with marriage and weekly budgets;
- 🗺️ Territoire — the paginated territory list with a persistent
  pager (the page rides the custom_id) and a detail button per card;
- 📜 Alliances — the civilizations of the kingdom with an info button;
- ⛪ Église — the marriage stock and active marriages; the action
  buttons are placeholders filled by tranche ④ (D73);
- 🛡️ Patrouille — the protection window (derived from the crons),
  the purchased tranches arrive with tranche ③ (D68).

Every view is marker-based (edit the marked message when present, post
otherwise) and never raises: a missing channel or service skips the
refresh, exactly like ``kingdom_content``. Gaïa never gets views (it
has no per-kingdom category — D70).

Designer-authored texts (the Église rules summary) are French-only —
they are the designer's voice, not the bot UI.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import discord

from kingdoms.discord.kingdom_setup import _find_category, _slug
from kingdoms.discord.kingdom_structure import (
    KINGDOM_CATEGORY_PREFIX,
    KINGDOM_CHANNELS,
    _find_channel_in,
    kingdom_category_name,
)
from kingdoms.discord.ui.persistent import PersistentPagerButton, register_page_renderer

logger = logging.getLogger("kingdoms.kingdom_state_views")

__all__ = [
    "KingdomAlliancesInfoButton",
    "KingdomEgliseActionButton",
    "KingdomEgliseReglesButton",
    "KingdomTerritoryDetailButton",
    "refresh_all_kingdom_state_views",
    "refresh_kingdom_state_views",
    "register_kingdoms_state_pager",
]

STATE_CHANNEL_KEYS: tuple[str, ...] = ("royaume", "seigneurs", "territoire", "alliances", "eglise", "patrouille")
"""The salon keys holding a state view (the conseil and pigeon salons do not)."""

STATE_MARKERS: dict[str, str] = {key: f"kingdoms:state:{key}" for key in STATE_CHANNEL_KEYS}
"""The marker of each state view — one marked message per salon."""

TERRITORY_PAGE_SIZE = 5
"""The territories rendered per Territoire page (D70 display density)."""

TERRITORY_PAGER_MOD = "kingdoms_terr"
"""The persistent pager mod key of the Territoire salon."""

EGLISE_REGLES = (
    "Les règles de l'Église — Saison II :\n"
    "- 💍 Le mariage unit un Seigneur à une civilisation ; le royaume tient un stock de mariages "
    "(base + une unité par époque, D59).\n"
    "- ⛔ Une civilisation déjà mariée ne peut plus être ciblée jusqu'au Jour du Seigneur.\n"
    "- ⛪ La paroisse évolue : chapelle → église → cathédrale (D64).\n"
    "- Les actions de l'Église arrivent avec la prochaine tranche."
)
"""The designer's Église rules summary (French-only designer voice)."""

_DAY_NAMES = {
    "MON": "lundi",
    "TUE": "mardi",
    "WED": "mercredi",
    "THU": "jeudi",
    "FRI": "vendredi",
    "SAT": "samedi",
    "SUN": "dimanche",
}

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "royaume_title": "🏰 Kingdom {}",
        "royaume_king": "King",
        "royaume_lords": "Lords",
        "royaume_treasury": "Tech treasury",
        "royaume_territories": "Territories",
        "royaume_alliances": "Alliances",
        "royaume_marriages": "Marriages",
        "royaume_parish": "Parish",
        "royaume_parish_value": "—/3 (state to come)",
        "royaume_patrouille": "Patrol",
        "royaume_gaia": "Gaïa AI level",
        "royaume_techs": "Technologies",
        "royaume_guard": "Royal guard",
        "seigneurs_title": "🎖️ Lords of {}",
        "seigneurs_empty": "No member yet — the enrollments are open.",
        "seigneurs_left": "left",
        "territoire_title": "🗺️ Territories of {}",
        "territoire_empty": "No territory yet — the draw happens at the season launch.",
        "territoire_page": "Page {}/{}",
        "territoire_protected": "🛡️ protected",
        "territoire_detail_button": "Detail",
        "territoire_unknown": "This territory is not part of this kingdom anymore.",
        "territoire_owner": "Owner",
        "territoire_map": "Map",
        "territoire_status": "Status",
        "territoire_free": "free",
        "alliances_title": "📜 Alliances of {}",
        "alliances_empty": "No civilization yet.",
        "alliances_secured": "secured",
        "alliances_info_button": "Learn more",
        "alliances_unknown": "This salon does not belong to a kingdom.",
        "eglise_title": "⛪ Church of {}",
        "eglise_stock": "Marriage stock",
        "eglise_active": "Active marriages",
        "eglise_none": "none yet",
        "eglise_action_marry": "💍 Marry",
        "eglise_action_parish": "⛪ Parish",
        "eglise_action_benediction": "✨ Blessing",
        "eglise_regles_button": "🔍 Rules",
        "eglise_coming": "This action arrives with the next tranche of the mod (D73).",
        "patrouille_title": "🛡️ Patrol of {}",
        "patrouille_window": "Protection window",
        "patrouille_tranches": "Purchased patrol shifts",
        "patrouille_coming": "— (coming tranche, D68)",
        "state_no_service": "The season service is unavailable right now.",
    },
    "fr": {
        "royaume_title": "🏰 Royaume {}",
        "royaume_king": "Roi",
        "royaume_lords": "Seigneurs",
        "royaume_treasury": "Trésorerie",
        "royaume_territories": "Territoires",
        "royaume_alliances": "Alliances",
        "royaume_marriages": "Mariages",
        "royaume_parish": "Paroisse",
        "royaume_parish_value": "—/3 (état à venir)",
        "royaume_patrouille": "Patrouille",
        "royaume_gaia": "Niveau d'IA Gaïa",
        "royaume_techs": "Technologies",
        "royaume_guard": "Garde royale",
        "seigneurs_title": "🎖️ Seigneurs de {}",
        "seigneurs_empty": "Aucun membre pour le moment — les inscriptions sont ouvertes.",
        "seigneurs_left": "parti",
        "territoire_title": "🗺️ Territoires de {}",
        "territoire_empty": "Aucun territoire pour le moment — le tirage a lieu au lancement de la saison.",
        "territoire_page": "Page {}/{}",
        "territoire_protected": "🛡️ protégé",
        "territoire_detail_button": "Détail",
        "territoire_unknown": "Ce territoire n'appartient plus à ce royaume.",
        "territoire_owner": "Propriétaire",
        "territoire_map": "Carte",
        "territoire_status": "Statut",
        "territoire_free": "libre",
        "alliances_title": "📜 Alliances de {}",
        "alliances_empty": "Aucune civilisation pour le moment.",
        "alliances_secured": "sécurisée",
        "alliances_info_button": "En savoir plus",
        "alliances_unknown": "Ce salon n'appartient pas à un royaume.",
        "eglise_title": "⛪ Église de {}",
        "eglise_stock": "Stock de mariages",
        "eglise_active": "Mariages actifs",
        "eglise_none": "aucun pour le moment",
        "eglise_action_marry": "💍 Marier",
        "eglise_action_parish": "⛪ Paroisse",
        "eglise_action_benediction": "✨ Bénédiction",
        "eglise_regles_button": "🔍 Règles",
        "eglise_coming": "Cette action arrive avec la prochaine tranche du mod (D73).",
        "patrouille_title": "🛡️ Patrouille de {}",
        "patrouille_window": "Fenêtre de protection",
        "patrouille_tranches": "Tranches de patrouille achetées",
        "patrouille_coming": "— (prochaine tranche, D68)",
        "state_no_service": "Le service de saison est indisponible pour le moment.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    """Resolve the catalog for one locale (English fallback, bot UI)."""
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def _locale_of(interaction: discord.Interaction | None) -> str:
    """Resolve the interaction locale with an English fallback."""
    return str(getattr(interaction, "locale", None) or "en")


def _cron_label(cron: str) -> str:
    """Render one cron of the protection window as a French instant (D56)."""
    parts = cron.split()
    if len(parts) < 5:
        return cron
    minute, hour, _dom, _mon, dow = parts[:5]
    day = _DAY_NAMES.get(dow.upper(), "")
    label = f"{hour}h{minute}" if minute not in ("0", "00") else f"{hour}h"
    return f"{day} {label}".strip() or cron


def _protection_window_label(config: Any) -> str:
    """Derive the protection window label from the season crons (D56)."""
    protection = getattr(config, "protection", None)
    start = getattr(protection, "start_cron", "")
    end = getattr(protection, "end_cron", "")
    if not start or not end:
        return "—"
    return f"{_cron_label(start)} → {_cron_label(end)}"


async def _upsert_marked(
    channel: Any, content: str, marker: str, view: discord.ui.View | None = None
) -> bool:
    """Edit the marked message of one salon, or post it when missing."""
    for message in list(getattr(channel, "messages", [])):
        if marker in (message.content or ""):
            try:
                await message.edit(content=content, view=view)
            except Exception:
                logger.warning("KINGDOM STATE: marked refresh failed (%s)", marker, exc_info=True)
            return True
    if view is not None:
        await channel.send(content=content, view=view)
    else:
        await channel.send(content=content)
    return True


def _channel_def(key: str) -> Any:
    """Return the tranche-① channel definition of one salon key."""
    return next(defn for defn in KINGDOM_CHANNELS if defn.key == key)


async def _kingdom_channel(guild: discord.Guild, kingdom_name: str, key: str) -> Any:
    """Resolve the state salon of one kingdom, or None when absent."""
    category = _find_category(guild, kingdom_category_name(kingdom_name))
    if category is None:
        return None
    return _find_channel_in(category, _channel_def(key).display_name)


async def _kingdom_from_channel(kingdoms_service: Any, channel: Any) -> Any | None:
    """Resolve the kingdom owning the category of one channel, or None."""
    category = getattr(channel, "category", None)
    category_name = _slug(getattr(category, "name", "")) if category is not None else ""
    if not category_name.startswith(_slug(KINGDOM_CATEGORY_PREFIX)) or category_name == _slug(
        kingdom_category_name("Gaïa")
    ):
        return None
    if kingdoms_service is None:
        return None
    kingdoms = await kingdoms_service.kingdoms()
    for kingdom in kingdoms:
        if not kingdom.is_gaia and _slug(kingdom_category_name(kingdom.name)) == category_name:
            return kingdom
    return None


async def _territories_of(territory_service: Any, kingdom_id: str) -> list[Any]:
    """Read the territories owned by one kingdom (empty without a service)."""
    if territory_service is None:
        return []
    try:
        territories = await territory_service.territories()
    except Exception:
        logger.warning("KINGDOM STATE: territory read failed", exc_info=True)
        return []
    return [t for t in territories if t.owner_kingdom_id == kingdom_id]


async def _technology_state(economy_service: Any, kingdom_id: str) -> Any | None:
    """Read the technology state through the economy bundle (defensive)."""
    attacks = getattr(economy_service, "_attacks", None)
    if attacks is None:
        return None
    try:
        return await attacks.technology_state(kingdom_id)
    except Exception:
        logger.warning("KINGDOM STATE: technology read failed", exc_info=True)
        return None


def _royaume_content(
    strings: dict[str, str],
    kingdom: Any,
    members: list[Any],
    territories: list[Any],
    technology: Any | None,
    ai_level: int,
    now: datetime,
) -> str:
    """Build the Le-Royaume fiche of one kingdom."""
    king = next((m for m in members if not m.left and m.role.value == "king"), None)
    active = [m for m in members if not m.left]
    secured = getattr(kingdom, "secured_civilizations", []) or []
    capacity = 1 + getattr(kingdom, "marriage_capacity", 0)
    purchases = getattr(technology, "purchases", {}) or {}
    protected = sum(1 for t in territories if t.is_protected_at(now))
    married = sum(1 for m in active if getattr(m, "married_civilization", None))
    lines = [
        f"# {strings['royaume_title'].format(kingdom.name)}",
        f"- 👑 {strings['royaume_king']} : {king.display_name if king else '—'}",
        f"- 🎖️ {strings['royaume_lords']} : {len(active)}/{capacity}",
        f"- 🔬 {strings['royaume_treasury']} : {getattr(kingdom, 'tech_points_bank', 0)}",
        f"- 🗺️ {strings['royaume_territories']} : {len(territories)}",
        f"- 🤝 {strings['royaume_alliances']} : {len(kingdom.civilizations)} ({len(secured)} 🔒)",
        f"- 💍 {strings['royaume_marriages']} : {married}/{capacity}",
        f"- ⛪ {strings['royaume_parish']} : {strings['royaume_parish_value']}",
        f"- 🛡️ {strings['royaume_patrouille']} : {strings['patrouille_coming']}",
        f"- 🤖 {strings['royaume_gaia']} : {ai_level}/5",
        f"- 🧪 {strings['royaume_techs']} : {getattr(technology, 'tech_points', 0)} 🔬"
        f" ({sum(purchases.values())} +)",
        f"- 🏰 {strings['royaume_guard']} : {protected}/{len(territories)}",
        STATE_MARKERS["royaume"],
    ]
    return "\n".join(lines)


def _seigneurs_content(strings: dict[str, str], kingdom: Any, members: list[Any], budgets: tuple[int, int]) -> str:
    """Build the Seigneurs roster of one kingdom (weddings and budgets)."""
    attacks_per_week, defenses_per_week = budgets
    lines = [f"# {strings['seigneurs_title'].format(kingdom.name)}"]
    listed = [m for m in members if not m.in_queue]
    if not listed:
        lines.append(strings["seigneurs_empty"])
    for member in listed:
        emoji = "👑" if member.role.value == "king" else "🎖️"
        civ = f" 💍 {member.married_civilization}" if getattr(member, "married_civilization", None) else ""
        if getattr(member, "left", False):
            lines.append(f"- ❌ {emoji} {member.display_name} ({strings['seigneurs_left']}){civ}")
            continue
        attack = max(0, attacks_per_week - getattr(member, "attack_used", 0))
        defense = max(0, defenses_per_week - getattr(member, "defense_used", 0))
        lines.append(f"- {emoji} {member.display_name} — ⚔️ {attack} · 🛡️ {defense}{civ}")
    lines.append(STATE_MARKERS["seigneurs"])
    return "\n".join(lines)


def _territoire_content(
    strings: dict[str, str],
    kingdom: Any,
    territories: list[Any],
    page: int,
    now: datetime,
) -> str:
    """Build one Territoire page of one kingdom (five cards per page)."""
    pages = max(1, -(-len(territories) // TERRITORY_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    lines = [f"# {strings['territoire_title'].format(kingdom.name)}"]
    chunk = territories[page * TERRITORY_PAGE_SIZE : (page + 1) * TERRITORY_PAGE_SIZE]
    if not territories:
        lines.append(strings["territoire_empty"])
    for index, territory in enumerate(chunk, start=1):
        shield = f" {strings['territoire_protected']}" if territory.is_protected_at(now) else ""
        lines.append(f"{page * TERRITORY_PAGE_SIZE + index}. 🗺️ `{territory.map_key}`{shield}")
    lines.append(f"-# {strings['territoire_page'].format(page + 1, pages)}")
    lines.append(STATE_MARKERS["territoire"])
    return "\n".join(lines)


def _territoire_view(territories: list[Any], page: int) -> discord.ui.View:
    """Build the persistent view of one Territoire page (detail + pager)."""
    pages = max(1, -(-len(territories) // TERRITORY_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = territories[page * TERRITORY_PAGE_SIZE : (page + 1) * TERRITORY_PAGE_SIZE]
    view = discord.ui.View(timeout=None)
    for territory in chunk:
        view.add_item(KingdomTerritoryDetailButton(territory.map_key))
    if pages > 1:
        if page > 0:
            view.add_item(PersistentPagerButton(TERRITORY_PAGER_MOD, page - 1, label="◀️"))
        if page < pages - 1:
            view.add_item(PersistentPagerButton(TERRITORY_PAGER_MOD, page + 1, label="▶️"))
    return view


def _alliances_content(strings: dict[str, str], kingdom: Any) -> str:
    """Build the Alliances list of one kingdom (secured flags included)."""
    secured = set(getattr(kingdom, "secured_civilizations", []) or [])
    lines = [f"# {strings['alliances_title'].format(kingdom.name)}"]
    civilizations = list(kingdom.civilizations)
    if not civilizations:
        lines.append(strings["alliances_empty"])
    for civilization in civilizations:
        lock = f" 🔒 {strings['alliances_secured']}" if civilization in secured else ""
        lines.append(f"- 🤝 **{civilization}**{lock}")
    lines.append(STATE_MARKERS["alliances"])
    return "\n".join(lines)


def _eglise_content(strings: dict[str, str], kingdom: Any, members: list[Any]) -> str:
    """Build the Église view of one kingdom (stock + active marriages)."""
    active = [m for m in members if not m.left and not m.in_queue]
    married = [m for m in active if getattr(m, "married_civilization", None)]
    lines = [f"# {strings['eglise_title'].format(kingdom.name)}"]
    lines.append(f"- 💍 {strings['eglise_stock']} : {getattr(kingdom, 'marriage_capacity', 0)}")
    if married:
        details = ", ".join(f"{m.display_name} 💍 {m.married_civilization}" for m in married)
        lines.append(f"- {strings['eglise_active']} : {details}")
    else:
        lines.append(f"- {strings['eglise_active']} : {strings['eglise_none']}")
    lines.append(STATE_MARKERS["eglise"])
    return "\n".join(lines)


def _eglise_view(locale: str) -> discord.ui.View:
    """Build the persistent Église action row (placeholders of tranche ④)."""
    strings = _strings(locale)
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomEgliseActionButton("marier", strings["eglise_action_marry"]))
    view.add_item(KingdomEgliseActionButton("paroisse", strings["eglise_action_parish"]))
    view.add_item(KingdomEgliseActionButton("benediction", strings["eglise_action_benediction"]))
    view.add_item(KingdomEgliseReglesButton(strings["eglise_regles_button"]))
    return view


def _patrouille_content(strings: dict[str, str], kingdom: Any, config: Any) -> str:
    """Build the Patrouille view of one kingdom (window + tranches placeholder)."""
    lines = [f"# {strings['patrouille_title'].format(kingdom.name)}"]
    lines.append(f"- 🕰️ {strings['patrouille_window']} : {_protection_window_label(config)}")
    lines.append(f"- ⚔️ {strings['patrouille_tranches']} : {strings['patrouille_coming']}")
    lines.append(STATE_MARKERS["patrouille"])
    return "\n".join(lines)


class KingdomTerritoryDetailButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:terr:detail:(?P<map>[a-z0-9_-]+)",
):
    """The restart-proof detail button of one territory card."""

    def __init__(self, map_key: str, label: str | None = None) -> None:
        """Build one persistent button; the map key rides the custom_id."""
        super().__init__(
            discord.ui.Button(
                label=label or "🔍",
                style=discord.ButtonStyle.secondary,
                custom_id=f"kingdoms:terr:detail:{map_key}"[:100],
            )
        )
        self.map_key = map_key

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomTerritoryDetailButton:
        """Rebuild the detail button from the wire."""
        return cls(match.group("map"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the territory card, fresh from the season state."""
        from kingdoms.discord.kingdom_persistent import _wiring

        strings = _strings(_locale_of(interaction))
        wiring = _wiring()
        kingdom = await _kingdom_from_channel(wiring.kingdoms_service, interaction.channel)
        if kingdom is None:
            await interaction.response.send_message(strings["alliances_unknown"], ephemeral=True)
            return
        territories = await _territories_of(wiring.territory_service, str(kingdom.id))
        territory = next((t for t in territories if t.map_key == self.map_key), None)
        if territory is None:
            await interaction.response.send_message(strings["territoire_unknown"], ephemeral=True)
            return
        protected = territory.is_protected_at(datetime.now(UTC))
        status = strings["territoire_protected"] if protected else strings["territoire_free"]
        await interaction.response.send_message(
            "\n".join(
                [
                    f"🗺️ **{strings['territoire_map']}** : `{territory.map_key}`",
                    f"- 🏛️ {strings['territoire_owner']} : **{kingdom.name}**",
                    f"- ⚖️ {strings['territoire_status']} : {status}",
                ]
            ),
            ephemeral=True,
        )


class KingdomAlliancesInfoButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:alliances:info",
):
    """The restart-proof info button of the Alliances view."""

    def __init__(self, label: str) -> None:
        """Build one persistent info button."""
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.secondary,
                custom_id="kingdoms:alliances:info",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAlliancesInfoButton:
        """Rebuild the info button from the wire."""
        return cls(_strings(_locale_of(interaction))["alliances_info_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the alliance details, fresh from the season state."""
        from kingdoms.discord.kingdom_persistent import _wiring

        strings = _strings(_locale_of(interaction))
        wiring = _wiring()
        kingdom = await _kingdom_from_channel(wiring.kingdoms_service, interaction.channel)
        if kingdom is None:
            await interaction.response.send_message(strings["alliances_unknown"], ephemeral=True)
            return
        secured = set(getattr(kingdom, "secured_civilizations", []) or [])
        lines = [f"# {strings['alliances_title'].format(kingdom.name)}"]
        for civilization in kingdom.civilizations:
            lock = f" 🔒 {strings['alliances_secured']}" if civilization in secured else ""
            lines.append(f"- 🤝 **{civilization}**{lock}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class KingdomEgliseActionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:eglise:action:(?P<action>[a-z_]+)",
):
    """The restart-proof Église action button (placeholder of tranche ④)."""

    def __init__(self, action: str, label: str) -> None:
        """Build one persistent action button; the action rides the custom_id."""
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"kingdoms:eglise:action:{action}",
            )
        )
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomEgliseActionButton:
        """Rebuild the action button from the wire."""
        strings = _strings(_locale_of(interaction))
        labels = {
            "marier": strings["eglise_action_marry"],
            "paroisse": strings["eglise_action_parish"],
            "benediction": strings["eglise_action_benediction"],
        }
        action = match.group("action")
        return cls(action, labels.get(action, action))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the coming-tranche placeholder (D73)."""
        await interaction.response.send_message(_strings(_locale_of(interaction))["eglise_coming"], ephemeral=True)


class KingdomEgliseReglesButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:eglise:regles",
):
    """The restart-proof rules button of the Église view."""

    def __init__(self, label: str) -> None:
        """Build one persistent rules button."""
        super().__init__(
            discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary, custom_id="kingdoms:eglise:regles")
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomEgliseReglesButton:
        """Rebuild the rules button from the wire."""
        return cls(_strings(_locale_of(interaction))["eglise_regles_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the designer's rules summary (French-only voice)."""
        await interaction.response.send_message(EGLISE_REGLES, ephemeral=True)


async def _territory_page_renderer(interaction: discord.Interaction, page: int) -> None:
    """Render one Territoire page on a pager click (fresh from the state)."""
    from kingdoms.discord.kingdom_persistent import _wiring

    strings = _strings(_locale_of(interaction))
    wiring = _wiring()
    kingdom = await _kingdom_from_channel(wiring.kingdoms_service, interaction.channel)
    if kingdom is None:
        await interaction.response.send_message(strings["alliances_unknown"], ephemeral=True)
        return
    territories = await _territories_of(wiring.territory_service, str(kingdom.id))
    now = datetime.now(UTC)
    content = _territoire_content(strings, kingdom, territories, page, now)
    await interaction.response.edit_message(content=content, view=_territoire_view(territories, page))


def register_kingdoms_state_pager() -> None:
    """Declare the Territoire pager renderer (called at every startup)."""
    register_page_renderer(TERRITORY_PAGER_MOD, _territory_page_renderer)


async def _season_snapshot(kingdoms_service: Any, kingdom: Any) -> dict[str, Any] | None:
    """Read the season inputs of one kingdom's views; None when unavailable."""
    try:
        config = kingdoms_service.config
        season = await kingdoms_service.current_season()
        lords = await kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOM STATE: season read failed", exc_info=True)
        return None
    age_key = season.current_age_key if season is not None else ""
    age = next((entry for entry in getattr(config, "ages", ()) if entry.key == age_key), None)
    return {
        "config": config,
        "members": [lord for lord in lords if lord.kingdom_id == kingdom.id],
        "ai_level": getattr(age, "gaia_ai_level", 0),
    }


def _salon_payload(
    key: str,
    strings: dict[str, str],
    locale: str,
    kingdom: Any,
    snapshot: dict[str, Any],
    territories: list[Any],
    technology: Any | None,
    now: datetime,
) -> tuple[str, discord.ui.View | None]:
    """Build the (content, view) payload of one state salon."""
    members: list[Any] = snapshot["members"]
    if key == "royaume":
        content = _royaume_content(
            strings, kingdom, members, territories, technology, snapshot["ai_level"], now
        )
        return content, None
    if key == "seigneurs":
        attacks = getattr(snapshot["config"], "attacks", None)
        budgets = (getattr(attacks, "attacks_per_week", 1), getattr(attacks, "defenses_per_week", 1))
        return _seigneurs_content(strings, kingdom, members, budgets), None
    if key == "territoire":
        return (
            _territoire_content(strings, kingdom, territories, 0, now),
            _territoire_view(territories, 0),
        )
    if key == "alliances":
        view = discord.ui.View(timeout=None)
        view.add_item(KingdomAlliancesInfoButton(strings["alliances_info_button"]))
        return _alliances_content(strings, kingdom), view
    if key == "eglise":
        return _eglise_content(strings, kingdom, members), _eglise_view(locale)
    return _patrouille_content(strings, kingdom, snapshot["config"]), None


async def refresh_kingdom_state_views(
    guild: discord.Guild,
    locale: str,
    kingdoms_service: Any,
    economy_service: Any,
    territory_service: Any,
    kingdom: Any,
) -> dict[str, bool]:
    """Refresh every state salon of one kingdom; return a per-salon report.

    Best effort: each salon refresh is independent and never raises —
    a missing salon or service skips only its own view. Gaïa never
    gets views (no per-kingdom category — D70).
    """
    if kingdoms_service is None or kingdom is None or getattr(kingdom, "is_gaia", False):
        return {}
    snapshot = await _season_snapshot(kingdoms_service, kingdom)
    if snapshot is None:
        return {}
    strings = _strings(locale)
    territories = await _territories_of(territory_service, str(kingdom.id))
    technology = await _technology_state(economy_service, str(kingdom.id))
    now = datetime.now(UTC)
    report: dict[str, bool] = {}
    for key in STATE_CHANNEL_KEYS:
        try:
            channel = await _kingdom_channel(guild, kingdom.name, key)
            if channel is None:
                continue
            content, view = _salon_payload(
                key, strings, locale, kingdom, snapshot, territories, technology, now
            )
            report[key] = await _upsert_marked(channel, content, STATE_MARKERS[key], view)
        except Exception:
            logger.warning("KINGDOM STATE: view refresh failed (%s)", key, exc_info=True)
    return report


async def refresh_all_kingdom_state_views(
    guild: discord.Guild,
    locale: str,
    kingdoms_service: Any,
    economy_service: Any,
    territory_service: Any,
) -> dict[str, int]:
    """Refresh the state views of every player kingdom of the guild."""
    report: dict[str, int] = {}
    if kingdoms_service is None:
        return report
    try:
        kingdoms = await kingdoms_service.kingdoms()
    except Exception:
        logger.warning("KINGDOM STATE: kingdom listing failed", exc_info=True)
        return report
    for kingdom in kingdoms:
        if kingdom.is_gaia:
            continue
        refreshed = await refresh_kingdom_state_views(
            guild, locale, kingdoms_service, economy_service, territory_service, kingdom
        )
        if refreshed:
            report[kingdom.name] = len(refreshed)
    return report
