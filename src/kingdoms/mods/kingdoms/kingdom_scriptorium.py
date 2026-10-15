"""Kingdoms mod — 📜 Scriptorium salon (patch restructuration-royaume).

The Scriptorium carries a pinned sommaire plus three restart-proof
persistent buttons (🗺️ Cadastre / 🤝 Diplomatie / 🛡️ Seigneurie). Every
rubric is an EPHEMERAL view (option A — public, any member may consult
it, only the clicker sees the answer) with the standard market TTL.

The rubrics are read-only views over the T2 services and the declared
config: the Cadastre lists the map catalog with its tags and textual
bonus (cadastre v1), the Diplomatie lists the civilizations with a
placeholder for Drasah's unlock conditions (document to come), and
the Seigneurie lists every lord per kingdom with the REMAINING
attack/defense budgets of the current cycle (Check 46 format).
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

import discord

from kingdoms.mods.kingdoms import chronicle
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.kingdom_scriptorium")

SCRIPTORIUM_MARKER = "kingdoms:scriptorium:summary"
SCRIPTORIUM_CHANNEL = "📜 Scriptorium"

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "summary_title": "# 📜 Scriptorium",
        "summary_line": "The archive of the realm — consult the rubrics below.",
        "topic_cadastre": "🗺️ Cadastre",
        "topic_diplomatie": "🤝 Diplomatie",
        "topic_seigneurie": "🛡️ Seigneurie",
        "cadastre_title": "# 🗺️ Cadastre — territories and their effects",
        "cadastre_empty": "The map catalog is empty.",
        "diplomatie_title": "# 🤝 Diplomatie — civilizations and their conditions",
        "diplomatie_empty": "No civilization declared yet.",
        "diplomatie_condition_map": "own the map {maps}",
        "diplomatie_condition_map_type": "own at least {count} map(s) of type {types}",
        "diplomatie_condition_min_maps": "own at least {count} map(s)",
        "diplomatie_condition_max_maps": "own at most {count} map(s)",
        "diplomatie_condition_civ": "hold the {civ} civilization",
        "diplomatie_condition_pattern": "belong to the {pattern} kingdom",
        "diplomatie_condition_tech": "hold at least {count} tech point(s)",
        "diplomatie_condition_marriages": "have at least {count} married lord(s)",
        "diplomatie_condition_civ_count": "hold at least {count} civilization(s)",
        "seigneurie_title": "# 🛡️ Seigneurie — lords and remaining budgets",
        "seigneurie_empty": "No kingdom yet — enrollments are open in Postuler.",
        "seigneurie_king": "King",
        "seigneurie_lords": "Lords",
        "seigneurie_pending": "Waiting for a kingdom",
        "fiche_choose": "🛡️ Which lord's fiche do you consult?",
        "fiche_title": "## 🛡️ Fiche — {name}",
        "fiche_identity": "👤 {name} · Insight: {insight} · {kingdom} ({role})",
        "fiche_status_title": "📊 Status",
        "fiche_status_married": "💍 Married civilization: {civ}",
        "fiche_status_secured": "🔒 Secured civilizations: {civs}",
        "fiche_status_territories": "🗺️ Territories held by the kingdom: {count}",
        "fiche_status_parish": "⛪ Parish tier: {tier}",
        "fiche_chronicle_title": "📜 Chronicle of the deeds (last 3)",
        "fiche_chronicle_more": "… and {count} more in the full history.",
        "fiche_history_button": "📜 Full history",
        "fiche_history_title": "📜 Full chronicle of {name}",
        "fiche_tracked": "👁️ Followed by your Corbeau — status and chronicle visible for 24h.",
        "fiche_gated": (
            "🔒 Only the identity is visible — a Corbeau `corbeau_track` "
            "(24h) opens the status and the chronicle."
        ),
        "fiche_parish_tiers": "1 Chapel · 2 Église · 3 Cathédrale",
        "unknown": "unknown",
    },
    "fr": {
        "summary_title": "# 📜 Scriptorium",
        "summary_line": "L'archive du royaume — consultez les rubriques ci-dessous.",
        "topic_cadastre": "🗺️ Cadastre",
        "topic_diplomatie": "🤝 Diplomatie",
        "topic_seigneurie": "🛡️ Seigneurie",
        "cadastre_title": "# 🗺️ Cadastre — territoires et leurs effets",
        "cadastre_empty": "Le catalogue de maps est vide.",
        "diplomatie_title": "# 🤝 Diplomatie — civilisations et leurs conditions",
        "diplomatie_empty": "Aucune civilisation déclarée pour le moment.",
        "diplomatie_condition_map": "posséder la map {maps}",
        "diplomatie_condition_map_type": "posséder au moins {count} map(s) de type {types}",
        "diplomatie_condition_min_maps": "posséder au moins {count} map(s)",
        "diplomatie_condition_max_maps": "posséder au plus {count} map(s)",
        "diplomatie_condition_civ": "posséder la civilisation {civ}",
        "diplomatie_condition_pattern": "appartenir au royaume {pattern}",
        "diplomatie_condition_tech": "posséder au moins {count} point(s) technologique(s)",
        "diplomatie_condition_marriages": "avoir au moins {count} seigneur(s) marié(s)",
        "diplomatie_condition_civ_count": "posséder au moins {count} civilisation(s)",
        "seigneurie_title": "# 🛡️ Seigneurie — seigneurs et budgets restants",
        "seigneurie_empty": "Aucun royaume pour le moment — les inscriptions sont ouvertes dans Postuler.",
        "seigneurie_king": "Roi",
        "seigneurie_lords": "Seigneurs",
        "seigneurie_pending": "En attente d'un royaume",
        "fiche_choose": "🛡️ Quelle fiche de seigneur consulter ?",
        "fiche_title": "## 🛡️ Fiche — {name}",
        "fiche_identity": "👤 {name} · Insight : {insight} · {kingdom} ({role})",
        "fiche_status_title": "📊 Statut",
        "fiche_status_married": "💍 Civilisation mariée : {civ}",
        "fiche_status_secured": "🔒 Civilisations sécurisées : {civs}",
        "fiche_status_territories": "🗺️ Territoires détenus par le royaume : {count}",
        "fiche_status_parish": "⛪ Palier Paroisse : {tier}",
        "fiche_chronicle_title": "📜 Chronique des faits d'armes (3 dernières)",
        "fiche_chronicle_more": "… et {count} de plus dans l'historique complet.",
        "fiche_history_button": "📜 Historique complet",
        "fiche_history_title": "📜 Chronique complète de {name}",
        "fiche_tracked": "👁️ Suivi par votre Corbeau — statut et chronique visibles pendant 24h.",
        "fiche_gated": (
            "🔒 Seule l'identité est visible — un Corbeau `corbeau_track` "
            "(24h) ouvre le statut et la chronique."
        ),
        "fiche_parish_tiers": "1 Chapelle · 2 Église · 3 Cathédrale",
        "unknown": "inconnu",
    },
}


def _strings(locale: str) -> dict[str, str]:
    """Return the module strings for one locale (fr by default)."""
    return _STRINGS["fr" if str(locale).lower().startswith("fr") else "en"]


def _locale(interaction: discord.Interaction) -> str:
    """Extract the interaction locale (en fallback)."""
    value = str(getattr(interaction, "locale", "") or "")
    return value if value else "en"


# ----------------------------------------------------------------------
# The three rubrics
# ----------------------------------------------------------------------


def _cadastre_content(strings: dict[str, str], maps: list[Any]) -> str:
    """Build the Cadastre rubric: the map catalog, tags and textual bonus."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _map_description

    lines = [strings["cadastre_title"], ""]
    if not maps:
        lines.append(strings["cadastre_empty"])
        return "\n".join(lines)
    for entry in maps:
        name = str(getattr(entry, "display_name", getattr(entry, "key", "?")))
        description = _map_description(entry, strings)
        lines.append(f"- **{name}**" + (f" — {description}" if description else ""))
    return "\n".join(lines)


def _diplomatie_condition_text(civ: Any, strings: dict[str, str], maps: list[Any]) -> str:
    """Render one civilization's official acquisition condition (V1).

    cadastre-diplomatie-v1 (Drasah 15/10): the Diplomatie is the
    SOURCE of truth for civ acquisition — the maps' cadastre bonus
    texts are display-only and may diverge. Map keys read as their
    display names.
    """
    by_key = {str(getattr(entry, "key", "")): str(getattr(entry, "display_name", "?")) for entry in maps}
    conditions: list[str] = []
    if getattr(civ, "requires_map_keys", None):
        names = ", ".join(by_key.get(key, key) for key in civ.requires_map_keys)
        conditions.append(strings["diplomatie_condition_map"].format(maps=names))
    if getattr(civ, "requires_map_types", None):
        conditions.append(
            strings["diplomatie_condition_map_type"].format(
                count=int(getattr(civ, "min_map_type_count", 1) or 1),
                types=", ".join(civ.requires_map_types),
            )
        )
    if getattr(civ, "min_map_count", None) is not None:
        conditions.append(
            strings["diplomatie_condition_min_maps"].format(count=int(civ.min_map_count))
        )
    if getattr(civ, "max_map_count", None) is not None:
        conditions.append(
            strings["diplomatie_condition_max_maps"].format(count=int(civ.max_map_count))
        )
    if getattr(civ, "requires_civilization", None):
        conditions.append(
            strings["diplomatie_condition_civ"].format(civ=str(civ.requires_civilization))
        )
    if getattr(civ, "requires_kingdom_name_pattern", None):
        conditions.append(
            strings["diplomatie_condition_pattern"].format(
                pattern=str(civ.requires_kingdom_name_pattern)
            )
        )
    if getattr(civ, "requires_tech_points", None) is not None:
        conditions.append(
            strings["diplomatie_condition_tech"].format(count=int(civ.requires_tech_points))
        )
    if getattr(civ, "requires_marriages", None) is not None:
        conditions.append(
            strings["diplomatie_condition_marriages"].format(count=int(civ.requires_marriages))
        )
    if getattr(civ, "requires_civilization_count", None) is not None:
        conditions.append(
            strings["diplomatie_condition_civ_count"].format(
                count=int(civ.requires_civilization_count)
            )
        )
    return " · ".join(conditions)


def _diplomatie_content(strings: dict[str, str], civilizations: list[Any]) -> str:
    """Build the Diplomatie rubric: civilizations + official conditions."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    maps: list[Any] = []
    try:
        wiring = _wiring()
        service = wiring.kingdoms_service
        config = getattr(service, "config", None) if service is not None else None
        maps = list(getattr(config, "maps", ()) or ())
    except Exception:
        maps = []

    lines = [strings["diplomatie_title"], ""]
    if not civilizations:
        lines.append(strings["diplomatie_empty"])
        return "\n".join(lines)
    for civ in civilizations:
        name = str(getattr(civ, "display_name", getattr(civ, "key", "?")))
        condition = _diplomatie_condition_text(civ, strings, maps)
        lines.append(f"- **{name}** — {condition}" if condition else f"- **{name}**")
    return "\n".join(lines)


def _seigneurie_content(
    strings: dict[str, str],
    kingdoms: list[Any],
    lords: list[Any],
    attack_budget: int,
    defense_budget: int,
) -> str:
    """Build the Seigneurie rubric: every lord per kingdom, remaining budgets."""

    def _budget_line(member: Any) -> str:
        attack_left = max(0, attack_budget - int(getattr(member, "attack_used", 0) or 0))
        defense_left = max(0, defense_budget - int(getattr(member, "defense_used", 0) or 0))
        return f" — ⚔️ {attack_left}/{attack_budget} · 🛡️ {defense_left}/{defense_budget}"

    lines = [strings["seigneurie_title"], ""]
    active = [lord for lord in lords if not getattr(lord, "left", False)]
    named = [k for k in kingdoms if not getattr(k, "is_gaia", False)]
    if not named:
        lines.append(strings["seigneurie_empty"])
        return "\n".join(lines)
    for kingdom in sorted(named, key=lambda k: str(k.name)):
        members = [
            lord
            for lord in active
            if getattr(lord, "kingdom_id", None) == kingdom.id and not lord.in_queue
        ]
        king = next((m for m in members if str(getattr(m.role, "value", m.role)) == "king"), None)
        head = (
            f"- 👑 {strings['seigneurie_king']} : {king.display_name}{_budget_line(king)}"
            if king is not None
            else f"- 👑 {strings['seigneurie_king']} : —"
        )
        lines.append(f"**{kingdom.name}**\n{head}")
        for member in members:
            if king is not None and member.id == king.id:
                continue
            lines.append(f"  - 🎖️ {member.display_name}{_budget_line(member)}")
        lines.append("")
    waiting = [lord for lord in active if getattr(lord, "in_queue", False)]
    if waiting:
        names = ", ".join(lord.display_name for lord in waiting)
        lines.append(f"⏳ {strings['seigneurie_pending']} : {names}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# The fiche seigneur (Seigneurie V2, Drasah 15/10/2026)
# ----------------------------------------------------------------------

_CHRONICLE_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "left": "leaves the season.",
        "crowned": "👑 Crowned King of {kingdom}.",
        "enrolled_kingdom": "🎬 Joined {kingdom} as a lord.",
        "enrolled_queue": "⏳ Joined the waiting queue.",
        "marriage": "💍 Married the civilization {civ} (Parish tier {tier}).",
        "arranged_marriage": "💍 Arranged marriage with {civ} (Parish tier {tier}).",
        "royal_marriage": "💍 Royal wedding (Reine) with {civ} (Parish tier {tier}).",
        "attack_declared": "⚔️ Declared an attack on {map} ({target}).",
        "embuscade": "⚔️ Declared an Embuscade on {map} ({target}).",
        "defense": "🛡️ Took the defense of {map}.",
        "pickban": "♟️ Pick & ban ({side}) on {map}.",
        "attack_result": "⚔️ Attack on {map}: {outcome}.",
        "defense_result": "🛡️ Defense of {map}: {outcome}.",
        "restitution": "↩️ Attack budget restituted ({map}).",
        "ffa_joined": "🎲 Enrolled in the FFA Exploration of {date}.",
        "ffa_left": "🎲 Withdrew from the FFA Exploration of {date}.",
        "ffa_result": "🎲 FFA Exploration of {date}: rank {rank}, {reward} ⚙️.",
        "bird_chosen": "🪶 Bird chosen: {bird}.",
        "voliere_action": "🪶 Volière action: {action}.",
        "corbeau_theft": "🐦‍⬛ Corbeau theft on {target} ({points} ⚙️, {state}).",
        "corbeau_theft_stop": "🐦‍⬛ Corbeau theft on {target} — stopped, {points} ⚙️ kept.",
        "corbeau_theft_dead": "💀 Corbeau killed by the contest of {target} — everything lost.",
        "corbeau_contested": "🛡️ Contested the corbeau theft of {thief}.",
        "market_purchase": "🛒 Marché purchase: {item} ({cost} ⚙️).",
        "parish_upgrade": "⛪ Parish upgraded to tier {tier}.",
        "cathedral_consecrated": "⛪ Cathedral consecrated on {territory}.",
    },
    "fr": {
        "left": "quitte la saison.",
        "crowned": "👑 Couronné Roi de {kingdom}.",
        "enrolled_kingdom": "🎬 Rejoint {kingdom} comme seigneur.",
        "enrolled_queue": "⏳ Rejoint la file d'attente.",
        "marriage": "💍 Marié à la civilisation {civ} (palier Paroisse {tier}).",
        "arranged_marriage": "💍 Mariage arrangé avec {civ} (palier Paroisse {tier}).",
        "royal_marriage": "💍 Mariage royal (Reine) avec {civ} (palier Paroisse {tier}).",
        "attack_declared": "⚔️ Attaque déclarée sur {map} ({target}).",
        "embuscade": "⚔️ Embuscade déclarée sur {map} ({target}).",
        "defense": "🛡️ Pris la défense de {map}.",
        "pickban": "♟️ Pick & ban ({side}) sur {map}.",
        "attack_result": "⚔️ Attaque sur {map} : {outcome}.",
        "defense_result": "🛡️ Défense de {map} : {outcome}.",
        "restitution": "↩️ Restitution du budget d'attaque ({map}).",
        "ffa_joined": "🎲 Inscrit à la FFA Exploration du {date}.",
        "ffa_left": "🎲 Désistement de la FFA Exploration du {date}.",
        "ffa_result": "🎲 FFA Exploration du {date} : rang {rank}, {reward} ⚙️.",
        "bird_chosen": "🪶 Oiseau choisi : {bird}.",
        "voliere_action": "🪶 Action de volière : {action}.",
        "corbeau_theft": "🐦‍⬛ Vol de ⚙️ sur {target} ({points} ⚙️, {state}).",
        "corbeau_theft_stop": "🐦‍⬛ Vol sur {target} — STOP, {points} ⚙️ gardés.",
        "corbeau_theft_dead": "💀 Corbeau tué par la contestation de {target} — tout perdu.",
        "corbeau_contested": "🛡️ Contesté le vol du Corbeau de {thief}.",
        "market_purchase": "🛒 Achat au Marché : {item} ({cost} ⚙️).",
        "parish_upgrade": "⛪ Paroisse améliorée au palier {tier}.",
        "cathedral_consecrated": "⛪ Cathédrale consacrée sur {territory}.",
    },
}


def _chronicle_line(locale: str, entry: Any) -> str:
    """Render one chronicle entry, EXACTLY timestamped (14/10 21h37 style)."""
    templates = _CHRONICLE_TEMPLATES["fr"] if str(locale).lower().startswith("fr") else _CHRONICLE_TEMPLATES["en"]
    params = {
        str(key): str(value) for key, value in (getattr(entry, "params", {}) or {}).items()
    }
    kind = str(getattr(entry, "kind", ""))
    template: str | None
    if kind == chronicle.KIND_ENROLLED:
        template = templates["enrolled_queue" if params.get("status") == "queue" else "enrolled_kingdom"]
    else:
        template = templates.get(kind)
    if template is None:  # pragma: no cover — forward compatibility
        template = f"{kind} {params}".strip()
    known = {k: v for k, v in params.items() if k in template}
    return f"<t:{int(getattr(entry, 'at', 0))}:f> — {template.format(**known)}"


def _fiche_identity(strings: dict[str, str], lord: Any, kingdom: Any) -> str:
    """Render the ALWAYS-visible identity block (Drasah's gating floor)."""
    is_king = str(getattr(lord, "role", "lord")) == "king"
    role = strings["seigneurie_king"] if is_king else strings["seigneurie_lords"]
    insight = str(getattr(lord, "insight_name", None) or strings["unknown"])
    kingdom_name = str(getattr(kingdom, "name", None) or strings["unknown"])
    return strings["fiche_identity"].format(
        name=str(getattr(lord, "display_name", "?")),
        insight=insight,
        kingdom=kingdom_name,
        role=role,
    )


def _fiche_status(
    strings: dict[str, str], lord: Any, kingdom: Any, territory_count: int
) -> list[str]:
    """Render the status block — married/secured civs, territories, Parish tier."""
    lines = [strings["fiche_status_title"]]
    married = getattr(lord, "married_civilization", None)
    if married:
        lines.append(strings["fiche_status_married"].format(civ=str(married)))
    secured = [str(civ) for civ in (getattr(kingdom, "secured_civilizations", []) or [])]
    if secured:
        lines.append(strings["fiche_status_secured"].format(civs=", ".join(secured)))
    lines.append(strings["fiche_status_territories"].format(count=territory_count))
    lines.append(strings["fiche_status_parish"].format(tier=int(getattr(kingdom, "parish_tier", 1) or 1)))
    return lines


def _fiche_chronicle(strings: dict[str, str], locale: str, lord: Any, *, limit: int = 3) -> list[str]:
    """Render the ``limit`` most recent deeds, newest first."""
    entries = chronicle.recent(lord, limit)
    if not entries:
        return [strings["fiche_chronicle_title"], strings["unknown"]]
    lines = [strings["fiche_chronicle_title"]]
    lines.extend(_chronicle_line(locale, entry) for entry in entries)
    total = len(getattr(lord, "chronicle", []) or [])
    if limit == 3 and total > len(entries):
        lines.append(strings["fiche_chronicle_more"].format(count=total - len(entries)))
    return lines


def _fiche_history(strings: dict[str, str], locale: str, lord: Any) -> str:
    """Render the FULL dépliable history, newest first, under Discord's 2000."""
    lines = [strings["fiche_history_title"].format(name=str(getattr(lord, "display_name", "?")))]
    for entry in chronicle.recent(lord, chronicle.CHRONICLE_CAP):
        line = _chronicle_line(locale, entry)
        if sum(len(item) + 1 for item in lines) + len(line) + 200 > 2000:
            break
        lines.append(line)
    return "\n".join(lines)


async def _fiche_content(strings: dict[str, str], locale: str, lord_id: str, consultant_lord: Any) -> str:
    """Build one lord's fiche, GATED by the consultant's corbeau_track.

    Drasah's rule (15/10): the identity shows to EVERYONE; the status
    and the chronique show ONLY while the consultant's kingdom runs an
    active ``corbeau_track`` on the lord (24h window).
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    service = wiring.kingdoms_service
    if service is None:
        raise RuntimeError("the Kingdoms service is not wired")
    lords = await service.lords()
    lord = next((item for item in lords if item.id == lord_id), None)
    if lord is None:
        raise RuntimeError("no such lord")
    kingdoms = await service.kingdoms()
    kingdom = next((k for k in kingdoms if k.id == getattr(lord, "kingdom_id", None)), None)
    lines = [strings["fiche_title"].format(name=str(lord.display_name))]
    lines.append(_fiche_identity(strings, lord, kingdom))
    consultant_kingdom = None
    if consultant_lord is not None:
        consultant_kingdom = next(
            (k for k in kingdoms if k.id == getattr(consultant_lord, "kingdom_id", None)),
            None,
        )
    tracked = consultant_kingdom is not None and chronicle.is_tracked_by(
        consultant_kingdom, str(lord.id), game_now()
    )
    if not tracked:
        lines.append(strings["fiche_gated"])
        return "\n".join(lines)
    territory_count = 0
    if wiring.territories_service is not None:
        try:
            territories = await wiring.territories_service.territories()
            territory_count = len(
                [t for t in territories if str(getattr(t, "owner_kingdom_id", "")) == str(lord.kingdom_id)]
            )
        except Exception:
            territory_count = 0
    lines.append(strings["fiche_tracked"])
    lines.extend(_fiche_status(strings, lord, kingdom, territory_count))
    lines.extend(_fiche_chronicle(strings, locale, lord))
    return "\n".join(lines)


def _seigneurie_fiche_select(strings: dict[str, str], lords: list[Any], kingdoms: list[Any]) -> discord.ui.Select[Any]:
    """Build the fiche selector (max 25 — Discord's option ceiling)."""
    names = {str(k.id): str(k.name) for k in kingdoms if not getattr(k, "is_gaia", False)}
    options: list[discord.SelectOption] = []
    for lord in lords:
        if len(options) >= 25:
            break
        if getattr(lord, "left", False) or getattr(lord, "in_queue", False):
            continue
        kingdom_name = names.get(str(getattr(lord, "kingdom_id", "")), "?")
        options.append(
            discord.SelectOption(
                label=str(lord.display_name)[:100],
                value=str(lord.id)[:100],
                description=kingdom_name[:100],
            )
        )
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:scriptorium:fiche:select",
        placeholder=strings["fiche_choose"][:100],
        options=options,
    )
    return select


async def _rubric_content(topic: str, strings: dict[str, str], kingdoms_service: Any) -> str:
    """Build one rubric's content from the wired services (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    service = kingdoms_service or wiring.kingdoms_service
    if service is None:
        raise RuntimeError("the Kingdoms service is not wired")
    config = getattr(service, "config", None)
    if topic == "cadastre":
        return _cadastre_content(strings, list(getattr(config, "maps", ()) or ()))
    if topic == "diplomatie":
        return _diplomatie_content(strings, list(getattr(config, "civilizations", ()) or ()))
    kingdoms = await service.kingdoms()
    lords = await service.lords()
    attacks_config = getattr(config, "attacks", None) if config is not None else None
    attack_budget = int(getattr(attacks_config, "attacks_per_week", 1) or 1)
    defense_budget = int(getattr(attacks_config, "defenses_per_week", 1) or 1)
    return _seigneurie_content(strings, kingdoms, lords, attack_budget, defense_budget)


# ----------------------------------------------------------------------
# The pinned sommaire
# ----------------------------------------------------------------------


def _scriptorium_view(strings: dict[str, str]) -> discord.ui.View:
    """Build the persistent three-button view of the pinned sommaire."""
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomScriptoriumButton("cadastre", strings["topic_cadastre"]))
    view.add_item(KingdomScriptoriumButton("diplomatie", strings["topic_diplomatie"]))
    view.add_item(KingdomScriptoriumButton("seigneurie", strings["topic_seigneurie"]))
    return view


async def deploy_scriptorium_panel(
    channel: discord.TextChannel,
    locale: str,
    kingdoms_service: Any = None,
) -> bool:
    """Pin (or refresh) the Scriptorium sommaire in its salon."""
    strings = _strings(locale)
    content = f"{strings['summary_title']}\n{strings['summary_line']}\n{SCRIPTORIUM_MARKER}"
    marked: Any = None
    try:
        async for message in channel.history(limit=50):
            if SCRIPTORIUM_MARKER in (message.content or ""):
                marked = message
                break
    except Exception:
        logger.warning("SCRIPTORIUM: sommaire history scan failed", exc_info=True)
    view = _scriptorium_view(strings)
    try:
        if marked is not None:
            await marked.edit(content=content, view=view)
        else:
            await channel.send(content=content, view=view)
        return True
    except Exception:
        logger.warning("SCRIPTORIUM: sommaire send failed", exc_info=True)
        return False


def find_scriptorium_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    """Return the guild's 📜 Scriptorium salon (None when absent)."""
    if guild is None:
        return None
    wanted = _slug(SCRIPTORIUM_CHANNEL)
    return next(
        (c for c in getattr(guild, "text_channels", []) if _slug(getattr(c, "name", "")) == wanted),
        None,
    )


# ----------------------------------------------------------------------
# The persistent rubric button
# ----------------------------------------------------------------------


class KingdomScriptoriumButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:scriptorium:(?P<topic>cadastre|diplomatie|seigneurie)",
):
    """The restart-proof rubric button of the pinned Scriptorium sommaire."""

    def __init__(self, topic: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:scriptorium:{topic}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.topic = topic

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomScriptoriumButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        strings = _strings(_locale(interaction))
        return cls(str(match.group("topic")), strings[f"topic_{match.group('topic')}"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the topic's EPHEMERAL view (public rubric, market TTL)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _market_ttl

        strings = _strings(_locale(interaction))
        locale = _locale(interaction)
        try:
            content = await _rubric_content(self.topic, strings, None)
        except Exception:
            logger.warning("SCRIPTORIUM: rubric build failed", exc_info=True)
            await interaction.response.send_message(strings["unknown"], ephemeral=True)
            return
        view: discord.ui.View | None = None
        if self.topic == "seigneurie":
            # Seigneurie V2 (Drasah 15/10): the rubric carries the
            # clickable fiche selector (max 25 — Discord's ceiling).
            try:
                from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

                wiring = _wiring()
                lords = await wiring.kingdoms_service.lords()
                kingdoms = await wiring.kingdoms_service.kingdoms()
                select = _seigneurie_fiche_select(strings, lords, kingdoms)
                select.callback = _fiche_select_callback(locale)  # type: ignore[method-assign]
                view = discord.ui.View(timeout=600)
                view.add_item(select)
            except Exception:
                logger.warning("SCRIPTORIUM: fiche select build failed", exc_info=True)
                view = None
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            message = cast(
                Any,
                await interaction.followup.send(content, ephemeral=True, view=cast(Any, view)),
            )
            _market_ttl(interaction, message)
        except Exception:
            logger.warning("SCRIPTORIUM: ephemeral send failed", exc_info=True)


# ----------------------------------------------------------------------
# The fiche select + the history button (Seigneurie V2)
# ----------------------------------------------------------------------


def _fiche_select_callback(locale: str) -> Any:
    """Build the in-session fiche select callback (ephemeral bubble)."""

    async def on_select(interaction: discord.Interaction) -> None:
        strings = _strings(locale)
        from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of, _market_ttl, _wiring

        values = [str(v) for v in (getattr(interaction, "data", None) or {}).get("values", [])]
        if not values:
            return
        wiring = _wiring()
        try:
            consultant = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        except Exception:
            consultant = None
        try:
            lord = next(
                (item for item in await wiring.kingdoms_service.lords() if item.id == values[0]),
                None,
            )
            content = await _fiche_content(strings, locale, values[0], consultant)
        except Exception:
            logger.warning("SCRIPTORIUM: fiche build failed", exc_info=True)
            await interaction.response.edit_message(content=strings["unknown"], view=None)
            return
        view = discord.ui.View(timeout=600)
        if lord is not None:
            view.add_item(KingdomSeigneurieButton(str(lord.id), strings["fiche_history_button"]))
        try:
            await interaction.response.edit_message(content=content, view=view)
            _market_ttl(interaction, interaction.message)
        except Exception:
            logger.warning("SCRIPTORIUM: fiche send failed", exc_info=True)

    return on_select


class KingdomSeigneurieSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"kingdoms:scriptorium:fiche",
):
    """The restart-proof fiche selector of the Seigneurie rubric."""

    def __init__(self, strings: dict[str, str], lords: list[Any], kingdoms: list[Any]) -> None:
        super().__init__(_seigneurie_fiche_select(strings, lords, kingdoms))

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomSeigneurieSelect:
        """Rebuild the selector from the wire — THIS module's strings only."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        lords = await wiring.kingdoms_service.lords()
        kingdoms = await wiring.kingdoms_service.kingdoms()
        return cls(strings, lords, kingdoms)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the selected lord's fiche (gated, market TTL)."""
        await _fiche_select_callback(_locale(interaction))(interaction)


class KingdomSeigneurieButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:scriptorium:fiche:history:(?P<lord_id>[^:]+)",
):
    """The « full history » dépliable of one fiche (restart-proof)."""

    def __init__(self, lord_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:scriptorium:fiche:history:{lord_id}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.lord_id = lord_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomSeigneurieButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(str(match.group("lord_id")), _strings(_locale(interaction))["fiche_history_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Déplie the lord's FULL chronicle (gated like the fiche)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of, _wiring

        locale = _locale(interaction)
        strings = _strings(locale)
        wiring = _wiring()
        try:
            consultant = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
            kingdoms = await wiring.kingdoms_service.kingdoms()
            consultant_kingdom = None
            if consultant is not None:
                consultant_kingdom = next(
                    (
                        k
                        for k in kingdoms
                        if k.id == getattr(consultant, "kingdom_id", None)
                    ),
                    None,
                )
            lord = next(
                (
                    item
                    for item in await wiring.kingdoms_service.lords()
                    if item.id == self.lord_id
                ),
                None,
            )
            if (
                lord is None
                or consultant_kingdom is None
                or not chronicle.is_tracked_by(consultant_kingdom, str(lord.id), game_now())
            ):
                await interaction.response.edit_message(
                    content=strings["fiche_gated"], view=None
                )
                return
            await interaction.response.edit_message(
                content=_fiche_history(strings, locale, lord), view=None
            )
        except Exception:
            logger.warning("SCRIPTORIUM: history build failed", exc_info=True)
            await interaction.response.edit_message(content=strings["unknown"], view=None)
