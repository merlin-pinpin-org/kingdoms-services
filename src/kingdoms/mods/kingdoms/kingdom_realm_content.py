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
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.panel_messages import (
    NO_MENTIONS,
    channel_messages,
    message_text,
    message_text_squashed,
    view_text,
)

if TYPE_CHECKING:
    import discord

logger = logging.getLogger("kingdoms.kingdom_realm_content")

VIEW_MARKER_PREFIX = "kingdoms:realm:view"

_PARIS_TZ = "Europe/Paris"


def _lock_until_label(until: int, strings: dict[str, str]) -> str:
    """Format one lock deadline as a readable Paris time (Drasah 2026-10-11).

    An end TIME never goes stale — unlike a remaining duration, the
    label stays true for ever, and it names the exact moment the lord
    can fight again.
    """
    stamp = datetime.fromtimestamp(until, tz=ZoneInfo(_PARIS_TZ))
    days = str(strings.get("lock_days", "")).split("|")
    day = days[stamp.weekday()] if stamp.weekday() < len(days) else ""
    return str(strings.get("lock_time_fmt", "{0} {1}h{2}")).format(
        day, f"{stamp.hour:02d}", f"{stamp.minute:02d}"
    )


def _strings(locale: str) -> dict[str, str]:
    """Return the FR/EN view strings (designer-authored pattern)."""
    if str(locale).lower().startswith("fr"):
        return {
            "overview_title": "🏰 {0}",
            "members": "Effectif",
            "members_line": "👑 {0} + **{1}** seigneur(s) — {2} membre(s)",
            "no_king": "Roi non enrôlé",
            "alliances_panel_title": "📜 Alliances — {0}",
            "alliances_panel_legend": (
                "Légende : 🎲 Draft starter · 🗺️ Condition · 💍 Mariage ·"
                " 👑 Royal · 📜 Mercenaire · 🤝 saison antérieure"
            ),
            "alliances_panel_badges": (
                "-# Chaque blason porte son NOM et l'🎲 🗺️ 💍 👑 📜 de sa provenance · 🤝 saison antérieure"
            ),
            "alliances_panel_hand": "-# main : {0}",
            "alliances_panel_married_line": "💍 {0} — {1}",
            "alliances_panel_merc_line": "⚔️ **{0}** — {1} ({2} usage(s) de draft)",
            "alliances_panel_empty": (
                "Aucune civilisation en main — les alliances arrivent par le jeu"
                " (conditions, mariages, Chef Mercenaire)."
            ),
            "alliances_panel_help": (
                "-# ❓ Conditions d'alliance : 📜 Scriptorium → rubrique Diplomatie."
            ),
            "alliances_btn_next": "🔭 Prochain cycle",
            "alliances_btn_locked": "🚫 Verrouillées",
            "alliances_btn_history": "📖 Historique",
            "alliances_btn_durations": "⏳ Durées",
            "alliances_btn_names": "🔤 Noms",
            "alliances_kingdom_none": "❌ Le royaume de ce salon est introuvable.",
            "alliances_no_data": "❌ Aucune donnée d'alliance disponible pour le moment.",
            "alliances_next_title": "🔭 Prochain cycle — {0}",
            "alliances_next_kept": "✅ Gardées : {0}",
            "alliances_next_arriving": "📥 Arrivent : {0}",
            "alliances_next_dropping": "📤 Quitteront la main : {0}",
            "alliances_next_elsewhere": "💍 Déjà mariée ailleurs : {0}",
            "alliances_locked_title": "🚫 Verrouillées — {0}",
            "alliances_locked_line": "🚫 **{0}** — {1}",
            "alliances_locked_cond_maps": "cartes requises : {0}",
            "alliances_locked_cond_count": "{0} territoire(s) requis",
            "alliances_locked_cond_none": "condition non remplie",
            "alliances_locked_married": "mariée à un autre royaume (exclusivité)",
            "alliances_locked_none": "Aucune civilisation verrouillée — tout le catalogue est jouable.",
            "alliances_history_title": "📖 Historique — {0}",
            "alliances_history_line": "{0} **{1}** — {2}",
            "alliances_history_none": "Aucune acquisition enregistrée pour le moment.",
            "alliances_durations_title": "⏳ Durées — {0}",
            "alliances_durations_line": "{0} **{1}**",
            "alliances_durations_since": "détenue depuis {0}",
            "alliances_durations_none": "Aucune civilisation en main.",
            "alliances_names_title": "🔤 Noms — {0}",
            "alliances_names_line": "• **{0}** — techtree : {1}",
            "alliances_names_none": "Aucune civilisation en main.",
            "treasury": "Trésorerie",
            "territories": "Territoires",
            "civilizations": "Civilisations",
            "marriages": "Mariages actifs",
            "patrol": "Patrouille",
            "royal_guard": "Garde royale",
            "parish": "Paroisse",
            "parish_placeholder": "⛪ Chapelle (palier 1/3) — paliers D64 à venir",
            "ledger_button": "🏛️ Chambre des comptes",
            "settings_button": "⚙️ Paramètres utilisateur",
            "settings_footer": "-# ⚙️ Réglages hors-jeu — ceci ne fait pas partie du jeu.",
            "yes": "oui",
            "no": "non",
            "none_f": "aucune",
            "none_m": "aucun",
            "unknown": "inconnu",
            "roster_title": "🎖️ Seigneurs — {0}",
            "roster_empty": "Aucun membre enrôlé pour le moment.",
            "left_badge": "❌ parti",
            "patrol_title": "🧱 Remparts — {0}",
            "patrol_window": (
                "🔄 Redéfinition des créneaux : uniquement pendant le Jour du"
                " Seigneur (dimanche 23h30 → lundi 10h00)."
            ),
            "patrol_slot": "{0:02d}h00 → {1:02d}h00 (chaque jour)",
            "remparts_patrouille_slot": "🛡️ **Patrouille {0}**",
            "remparts_patrouille_unplaced": "débloquée — à poser",
            "remparts_patrouille_locked": "verrouillée (2 ⚙️ au Marché)",
            "remparts_guard": "Gardes Royales",
            "remparts_guard_none": "aucune",
            "remparts_guard_line": "🛡️ **{0}** — protégée jusqu'à {1}",
            "remparts_guard_stock": "En réserve : {0}",
            "remparts_guard_button": "🛡️ Placer un Garde Royal",
            "remparts_patrouille_button": "🛡️ Placer Patrouille {0}",
            "remparts_place_guard_choose": "🛡️ Choisis la map à protéger :",
            "remparts_guard_placed": (
                "🛡️ La Garde Royale protège **{0}** jusqu'au {1} (heure de Paris)."
            ),
            "remparts_patrouille_placed": (
                "✅ Patrouille {0} posée : {1:02d}h00 → {2:02d}h00 (chaque jour)."
            ),
            "traquenard_button": "🪤 Utiliser un Traquenard",
            "traquenard_choose": "🪤 Contre quel royaume activer le Traquenard ?",
            "traquenard_done": (
                "🪤 Traquenard activé contre **{kingdom}** — l'IA de défense devient"
                " Facile pendant 6h (jusqu'à {time}) et les mariages scellés prennent"
                " +2h de verrou."
            ),
            "traquenard_failed": "❌ {message}",
            "traquenard_no_target": "❌ Aucun royaume adverse à cibler.",
            "remparts_ai": "🤖 Bot de défense (IA) : **{0}**/5",
            "remparts_reinforced": "💪 Patrouilles Renforcées (créneaux de 3h)",
            "roster_exploring": "🗺️ exploration jusqu'à {0}",
            "roster_marriage_lock": "🔒 verrou mariage jusqu'à {0}",
            "roster_ffa_lock": "🚫 FFA verrouillé jusqu'à {0}",
            "remparts_open_button": "🧱 Remparts",
            "not_lord": "Tu ne fais partie d'aucun royaume.",
            "seigneurie_open_button": "🛡️ Seigneurie",
            "territory_title": "🗺️ Territoire — {0}",
            "territory_count": "{0} territoire(s)",
            "territory_empty": "Aucun territoire pour le moment.",
            # restructuration-serveur-v1 c (Drasah 19/10): le salon
            # Patrouille disparaît — le niveau de l'IA vit ici.
            "gaia_ai_level": "🤖 Niveau de l'IA Gaïa : {0}/5",
            "territory_guard": "🛡️ protégée jusqu'à {0}",
            "territory_note": "La Corruption transfère le territoire de salon (D58).",
            "territory_pending_body": (
                "La carte reste secrète pour l'instant.\n"
                "Au démarrage de la saison, chaque royaume recevra ses "
                "territoires par tirage aléatoire."
            ),
            "church_title": "⛪ Paroisse — {0} · ⛪ {1} · 💍 {2} · 💰 {3}",
            "church_active": "Mariages actifs",
            "church_none": "Aucun mariage actif.",
            "church_badge_locked": "💍 {0} — {1} · 🔒 verrou jusqu'à {2}",
            "church_badge_free": "💍 {0} — {1} · ✅ verrou levé",
            "church_badge_royal_locked": "💍 {0} — 👑 Reine {1} · 🔒 verrou jusqu'à {2}",
            "church_badge_royal_free": "💍 {0} — 👑 Reine {1} · ✅ verrou levé",
            "church_singles": "Célibataires",
            "church_singles_none": "Aucun seigneur célibataire.",
            "church_tier_line": "Palier : {0} ({1}/{2}) — Offrande : {3} ⚙️",
            "church_lock_line": "Verrou mariage classique : {0}h",
            "church_royal_lock_line": "Verrou mariage royal (Roi) : {0}h",
            "church_marry_button": "💍 Se marier",
            "church_arrange_button": "💒 Mariage arrangé ({0})",
            "church_upgrade_button": "⛪ Améliorer → {0} ({1} ⚙️)",
            "church_upgrade_max": "⛪ Cathédrale — palier maximal",
            "parish_line": "{0} — palier {1}/{2}",
            "territory_chantier": "⏳ Lieu Sacrée — chantier jusqu'au {0}",
            "territory_sacred": "⛪ SACRÉ — à vie",
            "church_chantier_line": "⏳ Lieu Sacrée en chantier sur **{0}** — verdict le {1} (Paris)",
            "church_sacred_line": "⛪ Lieu Sacrée SACRÉ sur **{0}** — immunisé à vie",
            "church_household_palier": "✨ Notre {0}",
            "church_household": "💒 Les Fidèles du Royaume",
            "church_bachelor": "🎖️ {0} — célibataire · prêt à se marier 💍",
            "church_ladder_title": "🔭 Aperçu des paliers",
            "church_ladder_current": "●",
            "church_ladder_other": "▸",
            "church_ladder_line": "{0} {1} — {2} ⚙️ · verrou {3}h · offrande +{4} ⚙️",
            "church_ladder_sacree": " · ⛪ Lieu Sacrée",
            "church_legend_lock": (
                "-# 🔒 Verrou : un seigneur fraîchement marié ne peut ni"
                " attaquer ni défendre pendant quelques heures (24h · 12h · 6h"
                " selon le palier, doublé pour un mariage royal)."
            ),
            "church_legend_offering": (
                "-# 🕯️ Offrande : à chaque mariage célébré, la Paroisse verse"
                " des points de technologie au trésor du royaume."
            ),
            "church_legend_token": (
                "-# 💰 Jeton : acheté au Marché (3 ⚙️), permet un mariage"
                " arrangé sans consommer de point de mariage."
            ),
            "church_legend_sacree": (
                "-# ⛪ Lieu Sacrée (Cathédrale) : le royaume consacre un"
                " territoire — immunisé contre toute action technologique et"
                " insensible à la conquête pendant 72h ; seule une attaque"
                " classique peut le disputer."
            ),
            "lock_days": "lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche",
            "lock_time_fmt": "{0} {1}h{2}",
        }
    return {
        "overview_title": "🏰 {0}",
        "members": "Members",
        "members_line": "👑 {0} + **{1}** lord(s) — {2} member(s)",
        "no_king": "King not enrolled",
        "alliances_panel_title": "📜 Alliances — {0}",
        "alliances_panel_legend": (
            "Legend: 🎲 Starter draft · 🗺️ Condition · 💍 Marriage ·"
            " 👑 Royal · 📜 Mercenary · 🤝 earlier season"
        ),
        "alliances_panel_badges": (
            "-# Every crest wears its NAME and its origin 🎲 🗺️ 💍 👑 📜 · 🤝 earlier season"
        ),
        "alliances_panel_married_line": "💍 {0} — {1}",
        "alliances_panel_hand": "-# hand: {0}",
        "alliances_panel_merc_line": "⚔️ **{0}** — {1} ({2} draft use(s))",
        "alliances_panel_empty": (
            "No civilization in hand — alliances arrive through play"
            " (conditions, marriages, Chief Mercenary)."
        ),
        "alliances_panel_help": "-# ❓ Alliance conditions: 📜 Scriptorium → Diplomatie rubric.",
        "alliances_btn_next": "🔭 Next cycle",
        "alliances_btn_locked": "🚫 Locked",
        "alliances_btn_history": "📖 History",
        "alliances_btn_durations": "⏳ Durations",
        "alliances_btn_names": "🔤 Names",
        "alliances_kingdom_none": "❌ This salon's kingdom cannot be found.",
        "alliances_no_data": "❌ No alliance data available right now.",
        "alliances_next_title": "🔭 Next cycle — {0}",
        "alliances_next_kept": "✅ Kept: {0}",
        "alliances_next_arriving": "📥 Arriving: {0}",
        "alliances_next_dropping": "📤 Leaving the hand: {0}",
        "alliances_next_elsewhere": "💍 Married elsewhere: {0}",
        "alliances_locked_title": "🚫 Locked — {0}",
        "alliances_locked_line": "🚫 **{0}** — {1}",
        "alliances_locked_cond_maps": "required maps: {0}",
        "alliances_locked_cond_count": "{0} territory(/ies) required",
        "alliances_locked_cond_none": "condition not met",
        "alliances_locked_married": "married to another kingdom (exclusivity)",
        "alliances_locked_none": "No locked civilization — the whole catalogue is playable.",
        "alliances_history_title": "📖 History — {0}",
        "alliances_history_line": "{0} **{1}** — {2}",
        "alliances_history_none": "No acquisition recorded yet.",
        "alliances_durations_title": "⏳ Durations — {0}",
        "alliances_durations_line": "{0} **{1}**",
        "alliances_durations_since": "held since {0}",
        "alliances_durations_none": "No civilization in hand.",
        "alliances_names_title": "🔤 Names — {0}",
        "alliances_names_line": "• **{0}** — techtree: {1}",
        "alliances_names_none": "No civilization in hand.",
        "treasury": "Treasury",
        "territories": "Territories",
        "civilizations": "Civilizations",
        "marriages": "Active marriages",
        "patrol": "Patrol",
        "royal_guard": "Royal guard",
        "parish": "Parish",
        "parish_placeholder": "⛪ Chapel (tier 1/3) — D64 tiers coming soon",
        "ledger_button": "🏛️ Chambre des comptes",
        "settings_button": "⚙️ User settings",
        "settings_footer": "-# ⚙️ Out-of-game settings — this is not part of the game.",
        "yes": "yes",
        "no": "no",
        "none_f": "none",
        "none_m": "none",
        "unknown": "unknown",
        "roster_title": "🎖️ Lords — {0}",
        "roster_empty": "No member enrolled yet.",
        "left_badge": "❌ left",
        "patrol_title": "🧱 Remparts — {0}",
        "patrol_window": (
            "🔄 Window redefinition: only during the Lord's Day"
            " (Sunday 23:30 → Monday 10:00)."
        ),
        "patrol_slot": "{0:02d}:00 → {1:02d}:00 (daily)",
        "remparts_patrouille_slot": "🛡️ **Patrol {0}**",
        "remparts_patrouille_unplaced": "unlocked — to be placed",
        "remparts_patrouille_locked": "locked (2 ⚙️ at the Market)",
        "remparts_guard": "Royal Guards",
        "remparts_guard_none": "none",
        "remparts_guard_line": "🛡️ **{0}** — shielded until {1}",
        "remparts_guard_stock": "In reserve: {0}",
        "remparts_guard_button": "🛡️ Place a Royal Guard",
        "remparts_patrouille_button": "🛡️ Place Patrol {0}",
        "remparts_place_guard_choose": "🛡️ Choose the map to shield:",
        "remparts_guard_placed": (
            "🛡️ The Royal Guard shields **{0}** until {1} (Paris time)."
        ),
        "remparts_patrouille_placed": (
            "✅ Patrol {0} placed: {1:02d}:00 → {2:02d}:00 (daily)."
        ),
            "traquenard_button": "🪤 Use a Traquenard",
            "traquenard_choose": "🪤 Against which kingdom should the Traquenard be activated?",
            "traquenard_done": (
                "🪤 Traquenard activated against **{kingdom}** — the defense AI turns"
                " Easy for 6h (until {time}) and sealed marriages gain +2h of lock."
            ),
            "traquenard_failed": "❌ {message}",
            "traquenard_no_target": "❌ No adversary kingdom to target.",
            "remparts_ai": "🤖 Defense bot (AI): **{0}**/5",
            "remparts_reinforced": "💪 Reinforced Patrols (3h slots)",
            "roster_exploring": "🗺️ exploring until {0}",
            "roster_marriage_lock": "🔒 marriage lock until {0}",
            "roster_ffa_lock": "🚫 FFA locked until {0}",
            "remparts_open_button": "🧱 Ramparts",
            "seigneurie_open_button": "🛡️ Lordship",
            "not_lord": "You are not part of any kingdom.",
        "territory_title": "🗺️ Territory — {0}",
        "territory_count": "{0} territor(y|ies)",
        "territory_empty": "No territory yet.",
        # restructuration-serveur-v1 c (Drasah 19/10): the Patrouille
        # salon disappears — the AI level lives here.
        "gaia_ai_level": "🤖 Gaïa AI level: {0}/5",
        "territory_guard": "🛡️ shielded until {0}",
        "territory_note": "Corruption moves the territory across salons (D58).",
        "territory_pending_body": (
            "The map stays secret for now.\n"
            "At the season start, each kingdom will receive its "
            "territories through a random draw."
        ),
        "church_title": "⛪ Parish — {0} · ⛪ {1} · 💍 {2} · 💰 {3}",
        "church_active": "Active marriages",
        "church_none": "No active marriage.",
        "church_badge_locked": "💍 {0} — {1} · 🔒 locked until {2}",
        "church_badge_free": "💍 {0} — {1} · ✅ lock lifted",
        "church_badge_royal_locked": "💍 {0} — 👑 Queen {1} · 🔒 locked until {2}",
        "church_badge_royal_free": "💍 {0} — 👑 Queen {1} · ✅ lock lifted",
        "church_singles": "Bachelors",
        "church_singles_none": "No bachelor lord.",
        "church_tier_line": "Tier: {0} ({1}/{2}) — Offering: {3} ⚙️",
        "church_lock_line": "Standard marriage lock: {0}h",
        "church_royal_lock_line": "Royal marriage lock (King): {0}h",
        "church_marry_button": "💍 Marry",
        "church_arrange_button": "💒 Arranged marriage ({0})",
        "church_upgrade_button": "⛪ Upgrade → {0} ({1} ⚙️)",
        "church_upgrade_max": "⛪ Cathedral — top tier",
        "parish_line": "{0} — tier {1}/{2}",
        "territory_chantier": "⏳ Lieu Sacrée — chantier until {0}",
        "territory_sacred": "⛪ SACRED — forever",
        "church_chantier_line": "⏳ Lieu Sacrée chantier on **{0}** — verdict on {1} (Paris)",
        "church_sacred_line": "⛪ SACRED Lieu Sacrée on **{0}** — immune forever",
        "church_household_palier": "✨ Our {0}",
        "church_household": "💒 The Kingdom's Faithful",
        "church_bachelor": "🎖️ {0} — bachelor · ready to marry 💍",
        "church_ladder_title": "🔭 Tier ladder",
        "church_ladder_current": "●",
        "church_ladder_other": "▸",
        "church_ladder_line": "{0} {1} — {2} ⚙️ · lock {3}h · offering +{4} ⚙️",
        "church_ladder_sacree": " · ⛪ Sacred Site",
        "church_legend_lock": (
            "-# 🔒 Lock: a freshly married lord can neither attack nor"
            " defend for a few hours (24h · 12h · 6h depending on the tier,"
            " doubled for a royal marriage)."
        ),
        "church_legend_offering": (
            "-# 🕯️ Offering: at every celebrated marriage, the Parish pays"
            " technology points into the kingdom's treasury."
        ),
        "church_legend_token": (
            "-# 💰 Token: bought at the Market (3 ⚙️), allows one arranged"
            " marriage without spending a marriage point."
        ),
        "church_legend_sacree": (
            "-# ⛪ Sacred Site (Cathedral): the kingdom consecrates a"
            " territory — immune to every technology action and safe from"
            " conquest for 72h; only a classic attack can dispute it."
        ),
        "lock_days": "Mon|Tue|Wed|Thu|Fri|Sat|Sun",
        "lock_time_fmt": "{0} {1}:{2}",
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


def _build_alliances_panel(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the 📜 Alliances panel (alliances-lisibilite-v1 T3, Drasah 25/10).

    ONE unified view: the blasons ride a single MOSAIC attachment
    (crest + origin badge per tile, painted in pure Python) instead of
    N full-size attachments, every crest tile wears its ORIGIN EMOJI
    badge (mosaic-emoji-badge: Twemoji glyph composited in pure
    Python, colour-square fallback offline), the written names stay the written names stay one 🔤 Noms click away, the married
    holders and the live mercenary contracts read as compact lines, and
    the help line points at the Scriptorium's Diplomatie rubric. The
    five ephemeral buttons ride the panel — the answers stay
    read-only, the cycle recalculation alone decides the truth.
    """
    import discord

    from kingdoms.mods.kingdoms.kingdom_alliances_panel import (
        KingdomAlliancesDurationsButton,
        KingdomAlliancesHistoryButton,
        KingdomAlliancesLockedButton,
        KingdomAlliancesNamesButton,
        KingdomAlliancesNextButton,
    )

    civ_names = state.get("civ_names", {})
    hand = [str(civ) for civ in getattr(kingdom, "civilizations", []) or []]
    married: dict[str, str] = {}
    mercenary: list[str] = []
    for member in state.get("members", []):
        civ = getattr(member, "married_civilization", None)
        if civ:
            married[str(civ)] = str(
                getattr(member, "display_name", getattr(member, "id", "?"))
            )
        merc_civ = getattr(member, "mercenary_civilization", None)
        merc_uses = int(getattr(member, "mercenary_uses_left", 0) or 0)
        if merc_civ and merc_uses > 0:
            mercenary.append(
                strings["alliances_panel_merc_line"].format(
                    getattr(member, "display_name", getattr(member, "id", "?")),
                    civ_names.get(str(merc_civ), str(merc_civ)),
                    merc_uses,
                )
            )
    lines: list[str] = [strings["alliances_panel_legend"], strings["alliances_panel_badges"]]
    # empreinte main (T3b): the body must change when the HAND changes,
    # or the identity guard would keep a stale mosaic after a hand edit.
    lines.append(strings["alliances_panel_hand"].format(", ".join(hand)))
    if not hand and not mercenary:
        lines.append(strings["alliances_panel_empty"])
    for civ_key, lord in married.items():
        if civ_key in hand:
            lines.append(
                strings["alliances_panel_married_line"].format(civ_names.get(civ_key, civ_key), lord)
            )
    lines.extend(mercenary)
    body = "\n".join([*lines, "", strings["alliances_panel_help"]])
    buttons: list[Any] = [
        KingdomAlliancesNextButton(strings["alliances_btn_next"]),
        KingdomAlliancesLockedButton(strings["alliances_btn_locked"]),
        KingdomAlliancesHistoryButton(strings["alliances_btn_history"]),
        KingdomAlliancesDurationsButton(strings["alliances_btn_durations"]),
        KingdomAlliancesNamesButton(strings["alliances_btn_names"]),
    ]
    return _panel_view_with_buttons(
        strings["alliances_panel_title"].format(getattr(kingdom, "name", "")),
        body,
        f"{VIEW_MARKER_PREFIX}:alliances-panel",
        discord.Colour.dark_embed(),
        buttons,
        media="attachment://alliances.png" if hand else None,
    )


async def _sweep_legacy_mercenary_rubric(channel: Any) -> None:
    """Sweep the standalone ⚔️ mercenary rubric (merged in T3).

    alliances-lisibilite-v1 T3 (Drasah 25/10): the mercenary contracts
    now ride the unified Alliances panel — the old marked message of
    the T2c rubric must never survive a refresh. The PANEL marker
    embeds the rubric marker, so the panel message itself is exempt.
    """
    rubric_marker = f"{VIEW_MARKER_PREFIX}:alliances"
    panel_marker = f"{VIEW_MARKER_PREFIX}:alliances-panel"
    for message in await channel_messages(channel):
        text = message_text(message)
        if rubric_marker in text and panel_marker not in text:
            try:
                await message.delete()
            except Exception:
                logger.warning(
                    "KINGDOM REALM CONTENT: legacy mercenary rubric sweep failed",
                    exc_info=True,
                )


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
    # Drasah 19/10 (restructuration-serveur-v1): seigneurs/patrouille/
    # pigeon-voyageur are no longer salons — Remparts and Seigneurie
    # open from the Le-Royaume buttons (ephemeral), the carrier had no
    # wiring at all.
    # alliances-lisibilite-v1 T3 (Drasah 25/10): ONE unified panel
    # rides the 🤝 salon — the standalone mercenary rubric is merged
    # into it and its old marked message is swept below.
    salon_keys: dict[str, str] = {"alliances-panel": "alliances"}
    builders: dict[str, Any] = {
        "le-royaume": _build_overview,
        "territoire": _build_territory if phase != "setup" else _build_territory_pending,
        "eglise": _build_church,
        # alliances-lisibilite-v1 T3 (Drasah 25/10): the unified hand
        # view — one crest MOSAIC (origin badge per tile) + the legend,
        # the married holders and the live mercenary contracts as
        # compact lines, plus the five ephemeral clarity buttons.
        "alliances-panel": _build_alliances_panel,
    }
    results: dict[str, bool] = {}
    for key, builder in builders.items():
        channel = channels.get(salon_slugs.get(salon_keys.get(key, key)))
        if channel is None:
            results[key] = False
            continue
        try:
            view = builder(strings, kingdom, state)
            # défaut blasons (T3): the panel wears ONE composite mosaic
            # of the hand's crests — every tile carries its origin
            # badge, the page fits again.
            files = None
            if key == "alliances-panel":
                from kingdoms.mods.kingdoms.civ_crests import civ_crest_mosaic_file

                await _sweep_legacy_mercenary_rubric(channel)
                ledger = dict(getattr(kingdom, "civilization_acquisitions", None) or {})
                hand = [str(civ) for civ in getattr(kingdom, "civilizations", []) or []]
                mosaic = await civ_crest_mosaic_file(
                    [
                        (
                            civ,
                            (ledger.get(civ) or {}).get("origin") or "",
                            state.get("civ_names", {}).get(civ, civ),
                        )
                        for civ in hand
                    ]
                )
                files = [mosaic] if mosaic is not None else None
            results[key] = await _upsert_view(channel, key, view, files)
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
        "attack_budget": _budget_limit(service, "attacks_per_week"),
        "defense_budget": _budget_limit(service, "defenses_per_week"),
        "parish": _parish_state(kingdom, service),
    }
    state["defense_ai"] = None
    if attacks_service is not None and hasattr(attacks_service, "defense_ai_level"):
        try:
            state["defense_ai"] = await attacks_service.defense_ai_level(kid)
        except Exception:
            state["defense_ai"] = None
    state["reinforced"] = state["defense_ai"] is not None and int(state["defense_ai"]) >= 5
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


def _budget_limit(service: Any, key: str) -> int:
    """Return the configured weekly attack/defense budget (default 1).

    Drasah 15/10: the roster shows the budgets each lord still holds,
    so the panel needs the season's per-lord ceilings.
    """
    attacks = getattr(getattr(service, "config", None) if service is not None else None, "attacks", None)
    if attacks is None:
        return 1
    try:
        return max(0, int(getattr(attacks, key, 1) or 0))
    except Exception:
        return 1


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


def _parish_overview_line(state: dict[str, Any], strings: dict[str, str]) -> str:
    """Render the Paroisse slice of the overview (lieu-sacree-v1: real tier).

    A state gathered without the parish slice (partial test states)
    falls back to the former placeholder line.
    """
    parish = state.get("parish") or {}
    name = parish.get("name")
    if name:
        return strings["parish_line"].format(
            name, parish.get("tier", 1), parish.get("max_tier", 3)
        )
    return strings["parish_placeholder"]


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
            f"**{strings['treasury']}** : {state['wallet']} ⚙️",
            f"**{strings['territories']}** : {len(state['territories'])}",
            f"**{strings['civilizations']}** : {len(getattr(kingdom, 'civilizations', []) or [])}",
            f"**{strings['marriages']}** : {len(married)}",
            f"**{strings['patrol']}** : {patrol}",
            f"**{strings['royal_guard']}** : "
            f"{strings['yes'] if state['purchases'].get('garde_royale') else strings['no']}",
            f"**{strings['parish']}** : "
            f"{_parish_overview_line(state, strings)}",
        ]
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomMarketLedgerButton,
        KingdomTraquenardButton,
    )
    from kingdoms.mods.kingdoms.kingdom_voliere_panel import KingdomVoliereButton
    from kingdoms.mods.kingdoms.kingdom_voliere_panel import (
        _strings as _voliere_strings,
    )

    buttons: list[Any] = [KingdomMarketLedgerButton(strings["ledger_button"])]
    # The Traquenard button only exists when the kingdom holds a charge
    # (same rule as the cover proposal of the declare flow).
    if int(state.get("purchases", {}).get("traquenard", 0) or 0) > 0:
        buttons.append(KingdomTraquenardButton(strings))
    # Volière (voliere-v1): always visible — the Féodal gate lives in the
    # service (VoliereLockedError), so the view stays simple.
    voliere_locale = "fr" if strings.get("yes") == "oui" else "en"
    buttons.append(
        KingdomVoliereButton(_voliere_strings(voliere_locale)["voliere_button"])
    )
    # Drasah 19/10 (restructuration-serveur-v1): Remparts and Seigneurie
    # leave the salon world — they open as ephemeral views from here.
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomRempartsOpenButton,
        KingdomSeigneurieOpenButton,
    )

    buttons.append(KingdomRempartsOpenButton(strings["remparts_open_button"]))
    buttons.append(KingdomSeigneurieOpenButton(strings["seigneurie_open_button"]))
    view = _panel_view_with_buttons(
        strings["overview_title"].format(getattr(kingdom, "name", "")),
        body,
        f"{VIEW_MARKER_PREFIX}:le-royaume",
        discord.Colour.gold(),
        buttons,
    )
    # immersion-v1 (Drasah 20/10): the user-settings button lives in its
    # OWN footer container, visually detached from the game buttons —
    # the player must read it as out-of-game, not as a game action.
    from kingdoms.mods.kingdoms.kingdom_immersion import KingdomUserSettingsButton

    # (buttons ride a list[Any] like the game row — the DynamicItem's
    # Button[Any] typevar is not LayoutView-typed, mypy-wise)
    footer_buttons: list[Any] = [KingdomUserSettingsButton(strings["settings_button"])]
    footer: discord.ui.Container[discord.ui.LayoutView] = discord.ui.Container(
        discord.ui.Separator(),
        discord.ui.TextDisplay(strings["settings_footer"]),
        discord.ui.ActionRow(*footer_buttons),
    )
    view.add_item(footer)
    return view


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
            attack_budget = int(state.get("attack_budget", 1) or 1)
            defense_budget = int(state.get("defense_budget", 1) or 1)
            attack_left = max(0, attack_budget - int(getattr(member, "attack_used", 0) or 0))
            defense_left = max(0, defense_budget - int(getattr(member, "defense_used", 0) or 0))
            line += (
                f" — ⚔️ {attack_left}/{attack_budget}"
                f" · 🛡️ {defense_left}/{defense_budget}"
            )
            exploring = int(getattr(member, "exploration_locked_until", 0) or 0)
            if exploring > int(game_now().timestamp()):
                line += f" {strings['roster_exploring'].format(_lock_until_label(exploring, strings))}"
            # Drasah 19/10 (restructuration-serveur-v1): the roster shows
            # the public locks too — marriage and FFA no-show. The Corbeau
            # « suivi » is NEVER displayed (espionnage secret).
            marriage_lock = int(getattr(member, "marriage_locked_until", 0) or 0)
            if marriage_lock > int(game_now().timestamp()):
                line += f" {strings['roster_marriage_lock'].format(_lock_until_label(marriage_lock, strings))}"
            ffa_lock = int(getattr(member, "ffa_locked_until", 0) or 0)
            if ffa_lock > int(game_now().timestamp()):
                line += f" {strings['roster_ffa_lock'].format(_lock_until_label(ffa_lock, strings))}"
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
    """Build the 🧱 Remparts view (remparts-v2, Drasah 14/10).

    The view carries the defence AI level, the Patrouille 1 / 2 slots
    (placed window, unlocked-but-unplaced, or locked behind a market
    purchase) and the Garde Royale lines (one per shielded map, the
    reserve count) — plus the three King-only PLACEMENT buttons (the
    market sells, Remparts places).
    """
    import discord

    purchases = dict(state.get("purchases") or {})
    span = 3 if state.get("reinforced") else 2
    now = game_now()
    now_ts = int(now.timestamp())
    lines: list[str] = []
    if state.get("defense_ai") is not None:
        lines.append(strings["remparts_ai"].format(int(state["defense_ai"])))
        if state.get("reinforced"):
            lines.append(strings["remparts_reinforced"])
    # Patrouille 1 / Patrouille 2 — placed, unlocked or locked (v2).
    for slot_no in (1, 2):
        unlock_key = f"patrouille_unlock_{slot_no}"
        slot_key = f"patrouille_slot_{slot_no}"
        if slot_key in purchases:
            window = strings["remparts_patrouille_placed"].format(
                slot_no, int(purchases[slot_key]), int(purchases[slot_key]) + span
            )
        elif unlock_key in purchases:
            window = strings["remparts_patrouille_unplaced"]
        else:
            window = strings["remparts_patrouille_locked"]
        lines.append(f"{strings['remparts_patrouille_slot'].format(slot_no)} : {window}")
    # Gardes Royales — one line per shielded map + the reserve (v2).
    guard_lines: list[str] = []
    for territory in state["territories"]:
        guard_key = f"garde_royale_until:{getattr(territory, 'id', '')}"
        until_ts = int(purchases.get(guard_key, 0) or 0)
        if until_ts <= now_ts:
            continue
        key = str(getattr(territory, "map_key", ""))
        guard_lines.append(
            strings["remparts_guard_line"].format(
                state["map_names"].get(key, key), _lock_until_label(until_ts, strings)
            )
        )
    guard_block = "\n".join(guard_lines) or strings["remparts_guard_none"]
    lines.append(f"**{strings['remparts_guard']}** :")
    lines.append(guard_block)
    stock = int(purchases.get("garde_royale_stock", 0) or 0)
    if stock > 0:
        lines.append(strings["remparts_guard_stock"].format(stock))
    lines.append(strings["patrol_window"])
    body = "\n".join(lines)
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackManagerButton,
        KingdomDefenseManagerButton,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomRempartsGuardButton,
        KingdomRempartsPatrouilleButton,
    )

    return _panel_view_with_buttons(
        strings["patrol_title"].format(getattr(kingdom, "name", "")),
        body,
        f"{VIEW_MARKER_PREFIX}:patrouille",
        discord.Colour.teal(),
        [
            KingdomRempartsGuardButton(strings),
            KingdomRempartsPatrouilleButton(1, strings),
            KingdomRempartsPatrouilleButton(2, strings),
            # Drasah 18/10: la vue privée du royaume défenseur — qui
            # défend quoi, l'effet de surprise restant public-board-free.
            KingdomDefenseManagerButton(strings.get("defense_manager_button", "🛡️ Gestion de la défense")),
            # planification-attaque-v1 (Drasah 23/10): ⚔️ Gestion
            # d'attaque — les déclarations en cours + les
            # plannifications du royaume (3 choix visibles).
            KingdomAttackManagerButton(
                strings.get("attack_manager_button", "⚔️ Gestion d'attaque")
            ),
        ],
    )


def _build_territory(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the 🗺️ Territoire state view (guarded maps wear 🛡️ + end)."""
    import discord

    now = game_now()
    lines = [strings["territory_count"].format(len(state["territories"]))]
    if not state["territories"]:
        lines.append(strings["territory_empty"])
    for territory in state["territories"]:
        key = str(getattr(territory, "map_key", ""))
        line = f"• **{state['map_names'].get(key, key)}**"
        until = getattr(territory, "protected_until", None)
        if until is not None and until > now:
            # marche-v2 article 7: the shielded map shows its 🛡️ and the
            # protection END TIME next to its name.
            line += f" {strings['territory_guard'].format(_lock_until_label(int(until.timestamp()), strings))}"
        # lieu-sacree-v1: the real chantier (⏳ end Paris) and SACRED (⛪)
        # states show next to the map name — never a stale placeholder.
        cathedral_until = getattr(territory, "cathedral_until", None)
        if bool(getattr(territory, "sacred", False)):
            line += f" {strings['territory_sacred']}"
        elif cathedral_until is not None and cathedral_until > now:
            line += (
                f" {strings['territory_chantier'].format(
                    _lock_until_label(int(cathedral_until.timestamp()), strings)
                )}"
            )
        lines.append(line)
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


def _build_gaia_territory(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
    ai_level: int,
) -> Any:
    """Build the Gaïa 🗺️ Territoire view (restructuration-serveur-v1 c).

    The Gaïa category keeps ONE salon: the retired « Patrouille » info
    (the AI level) is integrated here — Drasah 19/10, the level is read
    LIVE from the season epoch (D18 scale, fallback 1), never hardcoded.
    The territories carry the same guard/chantier/sacred badges as the
    player-kingdom views.
    """
    import discord

    now = game_now()
    lines = [strings["gaia_ai_level"].format(int(ai_level))]
    lines.append(strings["territory_count"].format(len(state["territories"])))
    if not state["territories"]:
        lines.append(strings["territory_empty"])
    for territory in state["territories"]:
        key = str(getattr(territory, "map_key", ""))
        line = f"• **{state['map_names'].get(key, key)}**"
        until = getattr(territory, "protected_until", None)
        if until is not None and until > now:
            line += f" {strings['territory_guard'].format(_lock_until_label(int(until.timestamp()), strings))}"
        if bool(getattr(territory, "sacred", False)):
            line += f" {strings['territory_sacred']}"
        else:
            cathedral_until = getattr(territory, "cathedral_until", None)
            if cathedral_until is not None and cathedral_until > now:
                line += (
                    f" {strings['territory_chantier'].format(
                        _lock_until_label(int(cathedral_until.timestamp()), strings)
                    )}"
                )
        lines.append(line)
    lines.append(strings["territory_note"])
    return _panel_view(
        strings["territory_title"].format(getattr(kingdom, "name", "") or "Gaïa"),
        "\n".join(lines),
        f"{VIEW_MARKER_PREFIX}:territoire-gaia",
        discord.Colour.green(),
    )


async def deploy_gaia_content(
    guild: discord.Guild,
    wiring: Any,
    locale: str = "fr",
) -> dict[str, bool]:
    """Deploy the Gaïa territory view (the Territoire salon, c).

    restructuration-serveur-v1 c (Drasah 19/10): the Gaïa category drops
    to ONE salon — the AI level lives in the Territoire view and the
    FFA lives in its ephemeral salon. restructuration-gaia-v1
    (Drasah 24/10): the retired Gaïa category's Territoire salon now
    lives inside the renamed Kingdoms-Gaia structure category — the
    lookup goes through it (the realm-category path is gone with the
    category). Best effort: a missing category, salon or kingdom simply
    reports False, never raises.
    """
    from kingdoms.mods.kingdoms.kingdom_setup import _find_category, _slug

    category = _find_category(guild, "Kingdoms-Gaia") or _find_category(guild, "Kingdoms")
    if category is None:
        return {}
    service = getattr(wiring, "kingdoms_service", None)
    if service is None:
        return {}
    try:
        kingdoms = await service.kingdoms()
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: gaia kingdom read failed", exc_info=True)
        return {}
    kingdom = next((k for k in kingdoms if getattr(k, "is_gaia", False)), None)
    if kingdom is None:
        return {}
    # The AI level is the LIVE season epoch's (D18 scale, fallback 1) —
    # the same source as the attackers' Itinéraire/defense checks.
    ai_level = 1
    attacks_service = getattr(wiring, "attacks_service", None)
    if attacks_service is not None:
        try:
            ai_level = int(await attacks_service.base_ai_level())
        except Exception:
            logger.warning("KINGDOM REALM CONTENT: gaia ai level read failed", exc_info=True)
    channel = next(
        (
            c
            for c in getattr(category, "channels", [])
            if _slug(getattr(c, "name", "")) == "territoire"
        ),
        None,
    )
    if channel is None:
        return {"territoire-gaia": False}
    state = await _gather_state(kingdom, wiring)
    strings = _strings(locale)
    try:
        view = _build_gaia_territory(strings, kingdom, state, ai_level)
        return {"territoire-gaia": await _upsert_view(channel, "territoire-gaia", view)}
    except Exception:
        logger.warning("KINGDOM REALM CONTENT: gaia territory view failed", exc_info=True)
        return {"territoire-gaia": False}



def _build_church(
    strings: dict[str, str],
    kingdom: Any,
    state: dict[str, Any],
) -> Any:
    """Build the ⛪ Paroisse state view (D64 paliers + marriages, v3).

    Live data only: palier, points de mariage and jetons in the title
    (Drasah's 2026-10-11 header), one line per Fidèle with their
    marriage and their lock END TIME (never a stale remaining
    duration), the palier ladder with the Cathédrale's Lieu Sacrée,
    and a small legend (verrou, offrande, jeton, sacrée). The buttons
    (marry / arranged / upgrade) are restart-proof DynamicItems.
    """
    import time as _time

    import discord

    from kingdoms.mods.kingdoms.diplomacy import _pick_promise

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
        strings["church_royal_lock_line"].format(parish["lock_hours"] * 2),
        "",
        # 💒 Les Fidèles du Royaume — one line per member with their state
        f"## {strings['church_household']}",
    ]
    if not members:
        lines.append(strings["church_none"])
    for member in married:
        civ = str(getattr(member, "married_civilization", ""))
        civ_display = state["civ_names"].get(civ, civ)
        # Drasah 2026-10-12: the badge shows the promise herself —
        # « 💍 Luc — Atotoztli (Aztèques) » — never a lone civ key.
        promise = _pick_promise(f"{member.id}:{civ_display}", civ_display)
        civ_label = f"{promise} ({civ_display})" if promise else civ_display
        until = int(getattr(member, "marriage_locked_until", 0) or 0)
        royal = bool(getattr(member, "royal_marriage", False))
        base = "church_badge_royal" if royal else "church_badge"
        key = f"{base}_locked" if until > now else f"{base}_free"
        lines.append(
            strings[key].format(
                getattr(member, "display_name", strings["unknown"]),
                civ_label,
                _lock_until_label(until, strings) if until > now else "",
            )
        )
    for member in singles:
        lines.append(
            strings["church_bachelor"].format(
                getattr(member, "display_name", member.id)
            )
        )
    # 🔭 Aperçu des paliers — every tier visible with cost and perks
    lines += [
        "",
        f"## {strings['church_ladder_title']}",
    ]
    for index, step in enumerate(parish.get("ladder") or [], start=1):
        marker = strings["church_ladder_current"] if index == parish["tier"] else strings["church_ladder_other"]
        line = strings["church_ladder_line"].format(
            marker,
            step["name"],
            step["cost"],
            step["lock_hours"],
            step["offering"],
        )
        if index == parish["max_tier"]:
            line += strings["church_ladder_sacree"]
        lines.append(line)
    # lieu-sacree-v1: the REAL Lieu Sacrée state — every chantier
    # (⏳ verdict end, Paris) and every SACRED territory of the kingdom.
    now_dt = game_now()
    chantiers = [
        item
        for item in state["territories"]
        if getattr(item, "cathedral_until", None) is not None
        and item.cathedral_until > now_dt
    ]
    sacreds = [
        item for item in state["territories"] if bool(getattr(item, "sacred", False))
    ]
    if chantiers or sacreds:
        lines += [""]
        for item in chantiers:
            lines.append(
                strings["church_chantier_line"].format(
                    str(getattr(item, "map_key", "")),
                    _lock_until_label(
                        int(item.cathedral_until.timestamp()), strings
                    ),
                )
            )
        for item in sacreds:
            lines.append(
                strings["church_sacred_line"].format(str(getattr(item, "map_key", "")))
            )
    # 📖 la légende — verrou, offrande, jeton, lieu sacrée
    lines += [
        "",
        strings["church_legend_lock"],
        strings["church_legend_offering"],
        strings["church_legend_token"],
        strings["church_legend_sacree"],
    ]
    body = "\n".join(lines)

    buttons = _church_buttons(strings, kingdom, state)
    return _panel_view_with_buttons(
        strings["church_title"].format(
            getattr(kingdom, "name", ""),
            parish["name"],
            int(getattr(kingdom, "marriage_points", 0) or 0),
            int(getattr(kingdom, "arranged_marriage_stock", 0) or 0),
        ),
        body,
        f"{VIEW_MARKER_PREFIX}:eglise",
        discord.Colour.purple(),
        buttons,
    )


def _church_buttons(strings: dict[str, str], kingdom: Any, state: dict[str, Any]) -> list[Any]:
    """Build the restart-proof Paroisse buttons of the current palier."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomChurchArrangeButton,
        KingdomChurchConsecrateButton,
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
        # lieu-sacree-v1: at the Cathédrale palier the King may
        # consecrate a Lieu Sacrée (3 ⚙️, 72h chantier).
        buttons.append(KingdomChurchConsecrateButton(strings))
    return buttons


def _panel_view_with_buttons(
    title: str,
    body: str,
    marker: str,
    accent: discord.Colour,
    buttons: list[Any],
    media: str | None = None,
) -> discord.ui.LayoutView:
    """Build one Components V2 state panel with an action row of buttons.

    mosaic-render-fix (T3b, Drasah 25/10): Components V2 messages do
    NOT render unreferenced file attachments — an optional top-level
    ``MediaGallery`` referencing the mosaic attachment makes the
    blasons visible under the panel container.
    """
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
    if media:
        view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(media=media)))
    return view



async def _upsert_view(
    channel: Any, key: str, view: Any, files: list[Any] | None = None
) -> bool:
    """Edit the marked panel in place, or send it once (never duplicate).

    vues-silencieuses-v1 (Drasah 19/10): the marked message is now edited in
    place instead of delete+send, with an identity guard so an unchanged view
    costs zero API calls. Duplicates are purged, and a failed edit falls back
    to a fresh send.
    """
    marker = f"{VIEW_MARKER_PREFIX}:{key}"
    marked: list[Any] = [
        message
        for message in await channel_messages(channel)
        if marker in message_text(message)
    ]
    if marked:
        for duplicate in marked[1:]:
            try:
                await duplicate.delete()
            except Exception:
                logger.warning(
                    "KINGDOM REALM CONTENT: view %s duplicate purge failed", key, exc_info=True
                )
        existing = marked[0]
        if message_text_squashed(existing) == view_text(view) and (
            files is None or getattr(existing, "attachments", None)
        ):
            return True
        try:
            await existing.edit(view=view, **({"attachments": files} if files else {}))
            return True
        except Exception:
            logger.warning(
                "KINGDOM REALM CONTENT: view %s edit failed, resending", key, exc_info=True
            )
    await channel.send(view=view, **({"files": files} if files else {}), allowed_mentions=NO_MENTIONS)
    return True
