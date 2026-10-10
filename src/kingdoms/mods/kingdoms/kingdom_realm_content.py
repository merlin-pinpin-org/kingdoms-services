"""Kingdoms mod — the per-realm salon state views (v1, reference D70).

Every salon of a kingdom category is a **silent state view** (all
announcements go to géopolitique), except the Salle du Conseil —
the kingdom's only private discussion salon, which stays empty.

Version 1 scope (first render, admin-reviewable):

- 🏰 Le-Royaume — the pinned kingdom sheet (effectif, trésorerie,
  territoires, civilisations, mariages, patrouille, garde royale,
  Paroisse placeholder);
- 🎖️ Seigneurs — the private roster (one line per member, marriages,
  attack/defense budgets, badges);
- 🛡️ Patrouille — the bought 2h slots, the purchases left, the
  modification window (D68);
- 🗺️ Territoire — the owned territories with display names;
- ⛪ Église — the Paroisse placeholder (D64 à venir), the marriage
  stock, the active marriages with their 💍 badges (D74);
- 🕊️ Pigeon-Voyageur — the D69 catalogue placeholder.

📜 Alliances keeps its « 🎲 Draft starter » message (the civs ARE the
alliances view, D51); 💬 Salle du Conseil stays empty by design.

Every view is marked and edited in place (the ``panel_messages``
contract): re-deploys, approvals and resets never duplicate.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kingdoms.mods.kingdoms.panel_messages import channel_messages, message_text

if TYPE_CHECKING:
    import discord

logger = logging.getLogger("kingdoms.kingdom_realm_content")

VIEW_MARKER_PREFIX = "kingdoms:realm:view"


def _strings(locale: str) -> dict[str, str]:
    """Return the FR/EN view strings (designer-authored pattern)."""
    if str(locale).lower().startswith("fr"):
        return {
            "overview_title": "🏰 {0}",
            "members": "Effectif",
            "members_line": "👑 {0} + **{1}** seigneur(s) — {2} membre(s)",
            "no_king": "Roi non enrôlé",
            "treasury": "Trésorerie",
            "territories": "Territoires",
            "civilizations": "Civilisations",
            "marriages": "Mariages actifs",
            "patrol": "Patrouille",
            "royal_guard": "Garde royale",
            "parish": "Paroisse",
            "parish_placeholder": "⛪ Chapelle (palier 1/3) — paliers D64 à venir",
            "yes": "oui",
            "no": "non",
            "none_f": "aucune",
            "none_m": "aucun",
            "unknown": "inconnu",
            "roster_title": "🎖️ Seigneurs — {0}",
            "roster_empty": "Aucun membre enrôlé pour le moment.",
            "left_badge": "❌ parti",
            "patrol_title": "🛡️ Patrouille — {0}",
            "patrol_bought": "Tranches achetées",
            "patrol_left": "Achats restants",
            "patrol_window": "Modification : dimanche 23h30 → lundi 10h00 (D68, câblage à venir)",
            "patrol_slot": "{0:02d}h00 → {1:02d}h00 (chaque jour)",
            "territory_title": "🗺️ Territoire — {0}",
            "territory_count": "{0} territoire(s)",
            "territory_empty": "Aucun territoire pour le moment.",
            "territory_note": "La Corruption transfère le territoire de salon (D58).",
            "church_title": "⛪ Église — {0}",
            "church_stock": "Stock mariages",
            "church_active": "Mariages actifs",
            "church_none": "Aucun mariage actif.",
            "church_badge": "💍 {0} — {1} (verrou en cours)",
            "church_actions": "Boutons 💍 Se marier · ⛪ Améliorer · ✨ Chantier Sacrée — bientôt disponibles (D64)",
            "carrier_title": "🕊️ Pigeon-Voyageur — {0}",
            "carrier_body": (
                "Ce salon recevra les rappels du Roi (D69) : achat de patrouille,"
                " mariages scellés, fenêtre de protection, changement d'époque…"
                " Catalogue de 14 messages en préparation."
            ),
        }
    return {
        "overview_title": "🏰 {0}",
        "members": "Members",
        "members_line": "👑 {0} + **{1}** lord(s) — {2} member(s)",
        "no_king": "King not enrolled",
        "treasury": "Treasury",
        "territories": "Territories",
        "civilizations": "Civilizations",
        "marriages": "Active marriages",
        "patrol": "Patrol",
        "royal_guard": "Royal guard",
        "parish": "Parish",
        "parish_placeholder": "⛪ Chapel (tier 1/3) — D64 tiers coming soon",
        "yes": "yes",
        "no": "no",
        "none_f": "none",
        "none_m": "none",
        "unknown": "unknown",
        "roster_title": "🎖️ Lords — {0}",
        "roster_empty": "No member enrolled yet.",
        "left_badge": "❌ left",
        "patrol_title": "🛡️ Patrol — {0}",
        "patrol_bought": "Bought slots",
        "patrol_left": "Purchases left",
        "patrol_window": "Change: Sunday 23:30 → Monday 10:00 (D68, wiring coming soon)",
        "patrol_slot": "{0:02d}:00 → {1:02d}:00 (daily)",
        "territory_title": "🗺️ Territory — {0}",
        "territory_count": "{0} territor(y|ies)",
        "territory_empty": "No territory yet.",
        "territory_note": "Corruption moves the territory across salons (D58).",
        "church_title": "⛪ Church — {0}",
        "church_stock": "Marriage stock",
        "church_active": "Active marriages",
        "church_none": "No active marriage.",
        "church_badge": "💍 {0} — {1} (lock running)",
        "church_actions": "Buttons 💍 Marry · ⛪ Upgrade · ✨ Holy Works — coming soon (D64)",
        "carrier_title": "🕊️ Carrier Pigeon — {0}",
        "carrier_body": (
            "This salon will carry the King's reminders (D69): patrol purchase,"
            " sealed marriages, protection window, epoch change… A catalogue of"
            " 14 messages is in preparation."
        ),
    }


async def deploy_realm_content(
    guild: discord.Guild,
    kingdom: Any,
    wiring: Any,
    locale: str = "fr",
) -> dict[str, bool]:
    """Deploy every realm state view of one kingdom (idempotent).

    Returns the per-salon outcome; each view fails alone (best-effort,
    like every provisioning path — a broken view never blocks the
    others).
    """
    from kingdoms.mods.kingdoms.kingdom_realms import _find_realm_category
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    category = _find_realm_category(guild, getattr(kingdom, "name", ""))
    if category is None:
        return {}
    channels: dict[str, Any] = {}
    for channel in getattr(category, "channels", []):
        channels[_slug(getattr(channel, "name", ""))] = channel

    state = await _gather_state(kingdom, wiring)
    strings = _strings(locale)
    builders: dict[str, Any] = {
        "le-royaume": _build_overview,
        "seigneurs": _build_roster,
        "patrouille": _build_patrol,
        "territoire": _build_territory,
        "eglise": _build_church,
        "pigeon-voyageur": _build_carrier,
    }
    results: dict[str, bool] = {}
    for key, builder in builders.items():
        channel = channels.get(key)
        if channel is None:
            results[key] = False
            continue
        try:
            content = builder(strings, kingdom, state)
            results[key] = await _upsert_view(channel, key, content)
        except Exception:
            logger.warning("KINGDOM REALM CONTENT: view %s failed", key, exc_info=True)
            results[key] = False
    return results


def _catalog_names(service: Any, attribute: str) -> dict[str, str]:
    """Return the key → display-name map of a config catalog."""
    catalog = getattr(getattr(service, "config", None), attribute, None) or ()
    return {
        str(getattr(entry, "key", entry)): str(getattr(entry, "display_name", entry))
        for entry in catalog
    }


async def _read_members(service: Any, kid: str) -> list[Any]:
    """Return the kingdom's active members (empty on failure)."""
    try:
        lords = await service.lords()
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: lords read failed", exc_info=True)
        return []
    return [
        lord
        for lord in lords
        if getattr(lord, "kingdom_id", None) == kid and not getattr(lord, "left", False)
    ]


async def _read_territories(service: Any, kid: str) -> list[Any]:
    """Return the kingdom's owned territories (empty on failure)."""
    try:
        territories = await service.territories()
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: territories read failed", exc_info=True)
        return []
    return [
        territory
        for territory in territories
        if str(getattr(territory, "owner_kingdom_id", "")) == kid
    ]


async def _read_wallet(service: Any, kid: str, fallback: int) -> int:
    """Return the kingdom wallet, falling back to the bank on failure."""
    try:
        return int(await service.wallet(kid))
    except Exception:
        return fallback


async def _read_purchases(service: Any, kid: str) -> dict[str, int]:
    """Return the kingdom's purchase counters (empty on failure)."""
    try:
        tech = await service.technology_state(kid)
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: technology read failed", exc_info=True)
        return {}
    return dict(getattr(tech, "purchases", {}) or {})


async def _gather_state(kingdom: Any, wiring: Any) -> dict[str, Any]:
    """Collect the kingdom's live data behind defensive lookups."""
    service = getattr(wiring, "kingdoms_service", None)
    kid = str(getattr(kingdom, "id", ""))
    bank = int(getattr(kingdom, "tech_points_bank", 0) or 0)
    state: dict[str, Any] = {
        "members": await _read_members(service, kid) if service is not None else [],
        "territories": await _read_territories(getattr(wiring, "territories_service", None), kid)
        if getattr(wiring, "territories_service", None) is not None
        else [],
        "wallet": await _read_wallet(getattr(wiring, "economy_service", None), kid, bank)
        if getattr(wiring, "economy_service", None) is not None
        else bank,
        "purchases": await _read_purchases(getattr(wiring, "attacks_service", None), kid)
        if getattr(wiring, "attacks_service", None) is not None
        else {},
        "civ_names": _catalog_names(service, "civilizations") if service is not None else {},
        "map_names": _catalog_names(service, "maps") if service is not None else {},
        "patrol_limit": _patrol_limit(service),
    }
    return state


def _patrol_limit(service: Any) -> int:
    """Return the configured per-season patrol purchase limit (default 2)."""
    limits = getattr(getattr(service, "config", None) if service is not None else None, "technologies", None)
    if limits is None:
        return 2
    try:
        return int(limits.limits.get("patrouille", 2))
    except Exception:
        return 2


def _build_overview(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> str:
    """Build the 🏰 Le-Royaume pinned sheet."""
    members = state["members"]
    king = next((m for m in members if str(getattr(m, "role", "")) == "king"), None)
    king_name = getattr(king, "display_name", None) or strings["no_king"]
    subjects = [m for m in members if m is not king]
    slots = _patrol_slots(state)
    married = [m for m in members if getattr(m, "married_civilization", None)]
    lines = [
        f"# {strings['overview_title'].format(getattr(kingdom, 'name', ''))}",
        f"**{strings['members']}** : {strings['members_line'].format(king_name, len(subjects), len(members))}",
        f"**{strings['treasury']}** : {state['wallet']} 🔬",
        f"**{strings['territories']}** : {len(state['territories'])}",
        f"**{strings['civilizations']}** : {len(getattr(kingdom, 'civilizations', []) or [])}",
        f"**{strings['marriages']}** : {len(married)}",
        f"**{strings['patrol']}** : "
        f"{', '.join(strings['patrol_slot'].format(s, s + 2) for s in slots) if slots else strings['none_f']}",
        f"**{strings['royal_guard']}** : "
        f"{strings['yes'] if state['purchases'].get('garde_royale') else strings['no']}",
        f"**{strings['parish']}** : {strings['parish_placeholder']}",
        f"-# {VIEW_MARKER_PREFIX}:le-royaume",
    ]
    return "\n".join(lines)


def _build_roster(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> str:
    """Build the 🎖️ Seigneurs private roster."""
    lines = [f"# {strings['roster_title'].format(getattr(kingdom, 'name', ''))}"]
    members = state["members"]
    if not members:
        lines.append(strings["roster_empty"])
    for member in members:
        role = str(getattr(member, "role", ""))
        icon = "👑" if role == "king" else "🎖️"
        line = f"• {icon} **{getattr(member, 'display_name', strings['unknown'])}**"
        civ = getattr(member, "married_civilization", None)
        if civ:
            line += f" — 💍 {state['civ_names'].get(str(civ), str(civ))}"
        line += (
            f" — ⚔️ {int(getattr(member, 'attack_used', 0) or 0)}"
            f" · 🛡️ {int(getattr(member, 'defense_used', 0) or 0)}"
        )
        if getattr(member, "left", False):
            line += f" {strings['left_badge']}"
        lines.append(line)
    lines.append(f"-# {VIEW_MARKER_PREFIX}:seigneurs")
    return "\n".join(lines)


def _patrol_slots(state: dict[str, Any]) -> list[int]:
    """Return the bought patrol slots (sorted slot starts)."""
    from kingdoms.mods.kingdoms.attacks import PATROUILLE_SLOT_KEYS

    slots: list[int] = []
    for key in PATROUILLE_SLOT_KEYS:
        value = state["purchases"].get(key)
        if isinstance(value, int):
            slots.append(value)
    return sorted(slots)


def _build_patrol(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> str:
    """Build the 🛡️ Patrouille state view (D68)."""
    slots = _patrol_slots(state)
    bought = ", ".join(
        strings["patrol_slot"].format(slot, slot + 2) for slot in slots
    ) or strings["none_f"]
    remaining = max(0, int(state["patrol_limit"]) - len(slots))
    lines = [
        f"# {strings['patrol_title'].format(getattr(kingdom, 'name', ''))}",
        f"**{strings['patrol_bought']}** : {bought}",
        f"**{strings['patrol_left']}** : {remaining}/{state['patrol_limit']}",
        strings["patrol_window"],
        f"-# {VIEW_MARKER_PREFIX}:patrouille",
    ]
    return "\n".join(lines)


def _build_territory(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> str:
    """Build the 🗺️ Territoire state view."""
    lines = [
        f"# {strings['territory_title'].format(getattr(kingdom, 'name', ''))}",
        strings["territory_count"].format(len(state["territories"])),
    ]
    if not state["territories"]:
        lines.append(strings["territory_empty"])
    for territory in state["territories"]:
        key = str(getattr(territory, "map_key", ""))
        lines.append(f"• **{state['map_names'].get(key, key)}**")
    lines += [strings["territory_note"], f"-# {VIEW_MARKER_PREFIX}:territoire"]
    return "\n".join(lines)


def _build_church(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> str:
    """Build the ⛪ Église state view (D64 placeholder + marriages)."""
    lines = [
        f"# {strings['church_title'].format(getattr(kingdom, 'name', ''))}",
        f"**{strings['parish']}** : {strings['parish_placeholder']}",
        f"**{strings['church_stock']}** : {int(getattr(kingdom, 'marriage_capacity', 0) or 0)}",
        f"**{strings['church_active']}** :",
    ]
    married = [m for m in state["members"] if getattr(m, "married_civilization", None)]
    if not married:
        lines.append(strings["church_none"])
    for member in married:
        civ = str(getattr(member, "married_civilization", ""))
        lines.append(
            "• "
            + strings["church_badge"].format(
                getattr(member, "display_name", strings["unknown"]),
                state["civ_names"].get(civ, civ),
            )
        )
    lines += [strings["church_actions"], f"-# {VIEW_MARKER_PREFIX}:eglise"]
    return "\n".join(lines)


def _build_carrier(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> str:
    """Build the 🕊️ Pigeon-Voyageur placeholder (D69 catalogue à venir)."""
    return "\n".join(
        [
            f"# {strings['carrier_title'].format(getattr(kingdom, 'name', ''))}",
            strings["carrier_body"],
            f"-# {VIEW_MARKER_PREFIX}:pigeon-voyageur",
        ]
    )


async def _upsert_view(channel: Any, key: str, content: str) -> bool:
    """Edit the marked view in place, or send it once (never duplicate)."""
    marker = f"{VIEW_MARKER_PREFIX}:{key}"
    for message in await channel_messages(channel):
        if marker in message_text(message):
            try:
                await message.edit(content=content)
            except Exception:
                logger.warning("KINGDOM REALM CONTENT: view %s refresh failed", key, exc_info=True)
            return True
    await channel.send(content)
    return True
