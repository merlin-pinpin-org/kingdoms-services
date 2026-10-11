"""Kingdoms mod — the per-realm salon state views (v1, reference D70).

Every salon of a kingdom category is a **silent state view** (all
announcements go to géopolitique), except the Salle du Conseil —
the kingdom's only private discussion salon, which stays empty.

Each view is a Components V2 panel (Drasah's choice, 2026-10-10: same
visual language as the admin panels — container, accent colour,
separators), marked and re-deployed in place (the ``panel_messages``
contract): re-deploys, approvals and resets never duplicate.

Phase rule (Drasah's game design, 2026-10-10): while the season is in
``setup``, NOTHING random is revealed — the 🗺️ Territoire salon shows
a "distribution at season start" placeholder and the 📜 Alliances one
carries its own (in kingdom_realms). Once the admin starts the season
(``started``), the real content replaces the placeholders.
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
            "territory_pending_body": (
                "La carte reste secrète pour l'instant.\n"
                "Au démarrage de la saison, chaque royaume recevra ses "
                "territoires par tirage aléatoire."
            ),
            "church_title": "⛪ Paroisse — {0}",
            "church_stock": "Points de mariage",
            "church_arranged": "Mariages arrangés en stock",
            "church_active": "Mariages actifs",
            "church_none": "Aucun mariage actif.",
            "church_badge": "💍 {0} — {1} (verrou {2} restant)",
            "church_lock_hours": "{0}h",
            "church_singles": "Célibataires",
            "church_singles_none": "Aucun seigneur célibataire.",
            "church_tier_line": "Palier : {0} ({1}/{2}) — Offrande : {3} 🔬",
            "church_lock_line": "Verrou mariage classique : {0}h",
            "church_marry_button": "💍 Se marier",
            "church_arrange_button": "💒 Mariage arrangé ({0})",
            "church_upgrade_button": "⛪ Améliorer → {0} ({1} 🔬)",
            "church_upgrade_max": "⛪ Cathédrale — palier maximal",
            "church_household_palier": "✨ Notre {0}",
            "church_household": "💒 La Maisonnée",
            "church_bachelor": "🎖️ {0} — célibataire · prêt à se marier 💍",
            "church_help": (
                "-# 💍 Un point de mariage = un mariage classique disponible ·"
                " 🎟️ un jeton = un mariage arrangé disponible (acheté au Marché)"
            ),
            "church_ladder_title": "🔭 Aperçu des paliers",
            "church_ladder_current": "●",
            "church_ladder_other": "▸",
            "church_ladder_line": "{0} {1} — {2} 🔬 · verrou {3}h · offrande +{4} 🔬",
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
        "territory_pending_body": (
            "The map stays secret for now.\n"
            "At the season start, each kingdom will receive its "
            "territories through a random draw."
        ),
        "church_title": "⛪ Parish — {0}",
        "church_stock": "Marriage points",
        "church_arranged": "Arranged marriages in stock",
        "church_active": "Active marriages",
        "church_none": "No active marriage.",
        "church_badge": "💍 {0} — {1} (lock {2} left)",
        "church_lock_hours": "{0}h",
        "church_singles": "Bachelors",
        "church_singles_none": "No bachelor lord.",
        "church_tier_line": "Tier: {0} ({1}/{2}) — Offering: {3} 🔬",
        "church_lock_line": "Standard marriage lock: {0}h",
        "church_marry_button": "💍 Marry",
        "church_arrange_button": "💒 Arranged marriage ({0})",
        "church_upgrade_button": "⛪ Upgrade → {0} ({1} 🔬)",
        "church_upgrade_max": "⛪ Cathedral — top tier",
        "church_household_palier": "✨ Our {0}",
        "church_household": "💒 The Household",
        "church_bachelor": "🎖️ {0} — bachelor · ready to marry 💍",
        "church_help": (
            "-# 💍 One marriage point = one standard marriage available ·"
            " 🎟️ one token = one arranged marriage available (bought at the Market)"
        ),
        "church_ladder_title": "🔭 Tier ladder",
        "church_ladder_current": "●",
        "church_ladder_other": "▸",
        "church_ladder_line": "{0} {1} — {2} 🔬 · lock {3}h · offering +{4} 🔬",
        "carrier_title": "🕊️ Carrier Pigeon — {0}",
        "carrier_body": (
            "This salon will carry the King's reminders (D69): patrol purchase,"
            " sealed marriages, protection window, epoch change… A catalogue of"
            " 14 messages is in preparation."
        ),
    }


async def _season_phase(kingdoms_service: Any) -> str:
    """Return the season phase ('started' when unknown/legacy/no service)."""
    if kingdoms_service is None:
        return "started"
    try:
        season = await kingdoms_service.current_season()
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: season read failed", exc_info=True)
        return "started"
    if season is None:
        return "started"
    return str(getattr(season, "phase", "started") or "started")


async def deploy_realm_content(
    guild: discord.Guild,
    kingdom: Any,
    wiring: Any,
    locale: str = "fr",
) -> dict[str, bool]:
    """Deploy every realm state view of one kingdom (idempotent).

    Returns the per-salon outcome; each view fails alone (best-effort,
    like every provisioning path — a broken view never blocks the
    others). While the season is in ``setup``, the Territoire salon
    shows the distribution placeholder instead of the territories.
    """
    from kingdoms.mods.kingdoms.kingdom_realms import REALM_SALONS, _find_realm_category
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    category = _find_realm_category(guild, getattr(kingdom, "name", ""))
    if category is None:
        return {}
    # The salon data keys (e.g. ``eglise``) map to display names
    # (``Paroisse``); match channels by the slug of the display name.
    salon_slugs = {key: _slug(display) for key, display in REALM_SALONS}
    channels: dict[str, Any] = {}
    for channel in getattr(category, "channels", []):
        channels[_slug(getattr(channel, "name", ""))] = channel

    phase = await _season_phase(getattr(wiring, "kingdoms_service", None))
    state = await _gather_state(kingdom, wiring)
    strings = _strings(locale)
    builders: dict[str, Any] = {
        "le-royaume": _build_overview,
        "seigneurs": _build_roster,
        "patrouille": _build_patrol,
        "territoire": _build_territory if phase != "setup" else _build_territory_pending,
        "eglise": _build_church,
        "pigeon-voyageur": _build_carrier,
    }
    results: dict[str, bool] = {}
    for key, builder in builders.items():
        channel = channels.get(salon_slugs.get(key, key))
        if channel is None:
            results[key] = False
            continue
        try:
            view = builder(strings, kingdom, state)
            results[key] = await _upsert_view(channel, key, view)
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
    if service is None:
        return []
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
    territories_service = getattr(wiring, "territories_service", None)
    economy_service = getattr(wiring, "economy_service", None)
    attacks_service = getattr(wiring, "attacks_service", None)
    state: dict[str, Any] = {
        "members": await _read_members(service, kid),
        "territories": (
            await _read_territories(territories_service, kid)
            if territories_service is not None
            else []
        ),
        "wallet": (
            await _read_wallet(economy_service, kid, bank)
            if economy_service is not None
            else bank
        ),
        "purchases": (
            await _read_purchases(attacks_service, kid) if attacks_service is not None else {}
        ),
        "civ_names": _catalog_names(service, "civilizations") if service is not None else {},
        "map_names": _catalog_names(service, "maps") if service is not None else {},
        "patrol_limit": _patrol_limit(service),
        "parish": _parish_state(kingdom, service),
    }
    return state


def _parish_state(kingdom: Any, service: Any) -> dict[str, Any]:
    """Collect the Paroisse slice (palier, points, stock, offrande, lock)."""
    config = getattr(service, "config", None) if service is not None else None
    settings = getattr(config, "parish", None)
    tier = max(1, int(getattr(kingdom, "parish_tier", 1) or 1))
    if settings is None:
        # defensive default: the config seam is unavailable (tests)
        fallback = (
            ("Chapelle", 0, 24, 0),
            ("Église", 2, 12, 1),
            ("Cathédrale", 4, 6, 2),
        )
        ladder = [
            {
                "name": name,
                "cost": cost,
                "lock_hours": lock,
                "offering": offering,
            }
            for name, cost, lock, offering in fallback
        ]
        entry = ladder[tier - 1]
        return {
            "tier": tier,
            "name": entry["name"],
            "cost": ladder[tier]["cost"] if tier < len(ladder) else 0,
            "next_name": ladder[tier]["name"] if tier < len(ladder) else "",
            "offering": entry["offering"],
            "lock_hours": entry["lock_hours"],
            "max_tier": len(ladder),
            "ladder": ladder,
        }
    tiers = settings.tiers
    entry = settings.tier(tier)
    next_entry = tiers[tier] if tier < len(tiers) else None
    ladder = [
        {
            "name": str(item.display_name),
            "cost": int(item.cost),
            "lock_hours": int(item.marriage_lock_hours),
            "offering": int(item.offering),
        }
        for item in tiers
    ]
    return {
        "tier": tier,
        "name": str(entry.display_name),
        "cost": int(next_entry.cost) if next_entry is not None else 0,
        "next_name": str(next_entry.display_name) if next_entry is not None else "",
        "offering": int(entry.offering),
        "lock_hours": int(entry.marriage_lock_hours),
        "max_tier": len(tiers),
        "ladder": ladder,
    }


def _patrol_limit(service: Any) -> int:
    """Return the configured per-season patrol purchase limit (default 2)."""
    limits = getattr(getattr(service, "config", None) if service is not None else None, "technologies", None)
    if limits is None:
        return 2
    try:
        return int(limits.limits.get("patrouille", 2))
    except Exception:
        return 2


def _panel_view(
    title: str,
    body: str,
    marker: str,
    accent: discord.Colour,
) -> discord.ui.LayoutView:
    """Build one Components V2 state panel (title / body / marker)."""
    import discord

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {title}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(body),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {marker}"),
            accent_colour=accent,
        )
    )
    return view


def _build_overview(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the 🏰 Le-Royaume pinned sheet."""
    import discord

    members = state["members"]
    king = next((m for m in members if str(getattr(m, "role", "")) == "king"), None)
    king_name = getattr(king, "display_name", None) or strings["no_king"]
    subjects = [m for m in members if m is not king]
    slots = _patrol_slots(state)
    married = [m for m in members if getattr(m, "married_civilization", None)]
    patrol = (
        ", ".join(strings["patrol_slot"].format(s, s + 2) for s in slots)
        if slots
        else strings["none_f"]
    )
    body = "\n".join(
        [
            f"**{strings['members']}** : "
            f"{strings['members_line'].format(king_name, len(subjects), len(members))}",
            f"**{strings['treasury']}** : {state['wallet']} 🔬",
            f"**{strings['territories']}** : {len(state['territories'])}",
            f"**{strings['civilizations']}** : {len(getattr(kingdom, 'civilizations', []) or [])}",
            f"**{strings['marriages']}** : {len(married)}",
            f"**{strings['patrol']}** : {patrol}",
            f"**{strings['royal_guard']}** : "
            f"{strings['yes'] if state['purchases'].get('garde_royale') else strings['no']}",
            f"**{strings['parish']}** : {strings['parish_placeholder']}",
        ]
    )
    return _panel_view(
        strings["overview_title"].format(getattr(kingdom, "name", "")),
        body,
        f"{VIEW_MARKER_PREFIX}:le-royaume",
        discord.Colour.gold(),
    )


def _build_roster(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the 🎖️ Seigneurs private roster."""
    import discord

    members = state["members"]
    if not members:
        lines = [strings["roster_empty"]]
    else:
        lines = []
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
    return _panel_view(
        strings["roster_title"].format(getattr(kingdom, "name", "")),
        "\n".join(lines),
        f"{VIEW_MARKER_PREFIX}:seigneurs",
        discord.Colour.blue(),
    )


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
) -> Any:
    """Build the 🛡️ Patrouille state view (D68)."""
    import discord

    slots = _patrol_slots(state)
    bought = ", ".join(
        strings["patrol_slot"].format(slot, slot + 2) for slot in slots
    ) or strings["none_f"]
    remaining = max(0, int(state["patrol_limit"]) - len(slots))
    body = "\n".join(
        [
            f"**{strings['patrol_bought']}** : {bought}",
            f"**{strings['patrol_left']}** : {remaining}/{state['patrol_limit']}",
            strings["patrol_window"],
        ]
    )
    return _panel_view(
        strings["patrol_title"].format(getattr(kingdom, "name", "")),
        body,
        f"{VIEW_MARKER_PREFIX}:patrouille",
        discord.Colour.teal(),
    )


def _build_territory(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the 🗺️ Territoire state view."""
    import discord

    lines = [strings["territory_count"].format(len(state["territories"]))]
    if not state["territories"]:
        lines.append(strings["territory_empty"])
    for territory in state["territories"]:
        key = str(getattr(territory, "map_key", ""))
        lines.append(f"• **{state['map_names'].get(key, key)}**")
    lines.append(strings["territory_note"])
    return _panel_view(
        strings["territory_title"].format(getattr(kingdom, "name", "")),
        "\n".join(lines),
        f"{VIEW_MARKER_PREFIX}:territoire",
        discord.Colour.green(),
    )


def _build_territory_pending(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the 🗺️ setup-phase placeholder (nothing revealed yet)."""
    import discord

    return _panel_view(
        strings["territory_title"].format(getattr(kingdom, "name", "")),
        strings["territory_pending_body"],
        f"{VIEW_MARKER_PREFIX}:territoire",
        discord.Colour.dark_grey(),
    )


def _build_church(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the ⛪ Paroisse state view (D64 paliers + marriages).

    Live data only: palier, points de mariage, jetons de mariage
    arrangé, mariages actifs avec verrou restant, célibataires — the
    anti-stale rule (Drasah 2026-10-11) re-renders this view after
    every state change. The buttons (marry / arranged / upgrade) are
    restart-proof DynamicItems registered on the bot.
    """
    import time as _time

    import discord

    parish = state["parish"]
    members = [m for m in state["members"] if not getattr(m, "left", False)]
    now = int(_time.time())
    married = [m for m in members if getattr(m, "married_civilization", None)]
    singles = [
        m for m in members if getattr(m, "married_civilization", None) is None
    ]

    # ✨ Palier block
    lines = [
        f"## {strings['church_household_palier'].format(parish['name'])}",
        strings["church_tier_line"].format(
            parish["name"], parish["tier"], parish["max_tier"], parish["offering"]
        ),
        strings["church_lock_line"].format(parish["lock_hours"]),
        "",
        # 💒 La Maisonnée — one line per member with their state
        f"## {strings['church_household']}",
    ]
    if not members:
        lines.append(strings["church_none"])
    for member in married:
        civ = str(getattr(member, "married_civilization", ""))
        until = int(getattr(member, "marriage_locked_until", 0) or 0)
        left = max(0, -(-(until - now) // 3600)) if until > now else 0
        lines.append(
            strings["church_badge"].format(
                getattr(member, "display_name", strings["unknown"]),
                state["civ_names"].get(civ, civ),
                strings["church_lock_hours"].format(left),
            )
        )
    for member in singles:
        lines.append(
            strings["church_bachelor"].format(
                getattr(member, "display_name", member.id)
            )
        )
    # 💰 Stocks — one marriage point = one standard marriage available
    lines += [
        "",
        f"**{strings['church_stock']}** : {int(getattr(kingdom, 'marriage_points', 0) or 0)}",
        f"**{strings['church_arranged']}** : {int(getattr(kingdom, 'arranged_marriage_stock', 0) or 0)}",
        strings["church_help"],
        "",
        # 🔭 Aperçu des paliers — every tier visible with cost and perks
        f"## {strings['church_ladder_title']}",
    ]
    for index, step in enumerate(parish.get("ladder") or [], start=1):
        marker = strings["church_ladder_current"] if index == parish["tier"] else strings["church_ladder_other"]
        lines.append(
            strings["church_ladder_line"].format(
                marker,
                step["name"],
                step["cost"],
                step["lock_hours"],
                step["offering"],
            )
        )
    body = "\n".join(lines)

    buttons = _church_buttons(strings, kingdom, state)
    return _panel_view_with_buttons(
        strings["church_title"].format(getattr(kingdom, "name", "")),
        body,
        f"{VIEW_MARKER_PREFIX}:eglise",
        discord.Colour.purple(),
        buttons,
    )


def _church_buttons(strings: dict[str, str], kingdom: Any, state: dict[str, Any]) -> list[Any]:
    """Build the restart-proof Paroisse buttons of the current palier."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomChurchArrangeButton,
        KingdomChurchMarryButton,
        KingdomChurchUpgradeButton,
    )

    parish = state["parish"]
    arranged = int(getattr(kingdom, "arranged_marriage_stock", 0) or 0)
    buttons: list[Any] = [KingdomChurchMarryButton(strings)]
    if parish["tier"] >= 2:
        buttons.append(KingdomChurchArrangeButton(strings, arranged))
    if parish["tier"] < parish["max_tier"]:
        buttons.append(
            KingdomChurchUpgradeButton(strings, parish["next_name"], parish["cost"])
        )
    else:
        buttons.append(KingdomChurchUpgradeButton(strings, "", 0))
    return buttons


def _panel_view_with_buttons(
    title: str,
    body: str,
    marker: str,
    accent: discord.Colour,
    buttons: list[Any],
) -> discord.ui.LayoutView:
    """Build one Components V2 state panel with an action row of buttons."""
    import discord

    view = discord.ui.LayoutView(timeout=None)
    container: discord.ui.Container[discord.ui.LayoutView] = discord.ui.Container(
        discord.ui.TextDisplay(f"# {title}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(body),
        discord.ui.Separator(),
        accent_colour=accent,
    )
    if buttons:
        container.add_item(discord.ui.ActionRow(*buttons))
    container.add_item(discord.ui.TextDisplay(f"-# {marker}"))
    view.add_item(container)
    return view


def _build_carrier(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the 🕊️ Pigeon-Voyageur placeholder (D69 catalogue à venir)."""
    import discord

    return _panel_view(
        strings["carrier_title"].format(getattr(kingdom, "name", "")),
        strings["carrier_body"],
        f"{VIEW_MARKER_PREFIX}:pigeon-voyageur",
        discord.Colour.light_grey(),
    )


async def _upsert_view(channel: Any, key: str, view: Any) -> bool:
    """Replace the marked panel in place, or send it once (never duplicate)."""
    marker = f"{VIEW_MARKER_PREFIX}:{key}"
    for message in await channel_messages(channel):
        if marker in message_text(message):
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM REALM CONTENT: view %s removal failed", key, exc_info=True)
    await channel.send(view=view)
    return True
