"""Kingdoms mod attack phases Discord surface (D77).

The pinned panel of the Déclarer une attaque salon (marker
``<!-- kingdoms-attack -->``) carries the restart-proof declare and
history buttons. Every DECLARED attack opens its OWN salon
(restructuration-serveur-v1 b) right under the declare salon — the
base message carries the 🛡️ Défendre button, and the centralized
meeting message (🔗 ID → ✅ Prêt → 🏆 Résultat) lives there too. The
scheduler tick (``run_attack_phases_safe``) drives the phases: the
T-15 reveal announcement, the pick & ban DMs and the meeting-time
forfeits — every step replays safely after a restart through the
service-side flags.

Lesson Check 30: the persistent buttons read ONLY this module's
strings (FR/EN mirrors below).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kingdoms.mods.kingdoms.civ_crests import civ_crest_mosaic_file, meeting_vs_mosaic_file
from kingdoms.mods.kingdoms.game_clock import game_now

logger = logging.getLogger(__name__)

# battle-room-id-v1: strong refs to the background unlock tasks (the
# grace sweep that re-opens the ID entry after a cancelled modal).
_ROOM_UNLOCK_TASKS: set[asyncio.Task[None]] = set()

# battle-room-id-v2: auto-destruction timers of the ephemeral flows —
# keyed by (attack_id, player_id) for the winner flows (re-armed at
# each interaction) and by unique refusal keys for the one-shots.
_ROOM_EPHEMERAL_TIMERS: dict[tuple[str, str], asyncio.Task[None]] = {}


def _arm_ephemeral_timer(key: tuple[str, str], interaction: Any, seconds: float) -> None:
    """(Re)arm the auto-destruction of one ephemeral response (v2).

    Drasah 22/10 (battle-room-id-v2): every ephemeral bubble of the
    room self-destructs — a component interaction RE-ARMS the timer
    so the view survives exactly as long as the player is working
    with it. ``interaction`` is the interaction whose ORIGINAL
    response carries the ephemeral message (its webhook token stays
    valid 15 minutes).
    """
    existing = _ROOM_EPHEMERAL_TIMERS.pop(key, None)
    if existing is not None:
        existing.cancel()

    async def _delete_later() -> None:
        try:
            await asyncio.sleep(seconds)
            await interaction.delete_original_response()
        except Exception:
            logger.debug("kingdoms: ephemeral auto-delete skipped", exc_info=True)
        finally:
            _ROOM_EPHEMERAL_TIMERS.pop(key, None)

    task = asyncio.create_task(_delete_later())
    _ROOM_EPHEMERAL_TIMERS[key] = task


# battle-ai-rdv-v1: auto-destruction timers of the DECLARED-note
# ephemerals — the note dies at the MEETING time (it stops being news
# once the room opens). Keyed by attack id (one declaration, one note).
_DECLARE_NOTE_TASKS: dict[str, asyncio.Task[None]] = {}


def _arm_declared_note_timer(attack_id: str, interaction: Any, message_id: int, expires_at: Any) -> None:
    """Arm the declared-note deletion at the meeting time (best effort).

    Drasah 22/10 (battle-ai-rdv-v1): « ⚔️ Attaque déclarée … RDV … »
    disappears the moment the meeting happens — the token webhook
    lives 15 minutes, so a later meeting keeps the note (the room's
    own message takes over the visibility).
    """
    try:
        when = expires_at if isinstance(expires_at, datetime) else datetime.fromtimestamp(float(expires_at), tz=UTC)
        delay = (when - game_now()).total_seconds()
    except Exception:
        return
    if delay <= 0 or delay > 840:
        return
    existing = _DECLARE_NOTE_TASKS.pop(attack_id, None)
    if existing is not None:
        existing.cancel()

    async def _delete_later() -> None:
        try:
            await asyncio.sleep(delay)
            await interaction.followup.delete_message(message_id)
        except Exception:
            logger.debug("kingdoms: declared-note auto-delete skipped", exc_info=True)
        finally:
            _DECLARE_NOTE_TASKS.pop(attack_id, None)

    _DECLARE_NOTE_TASKS[attack_id] = asyncio.create_task(_delete_later())


# sabotage-ux-v1 (Drasah 22/10): the sabotage phase lives on ONE
# ephemeral PANEL per (attack, player) — every action edits it in
# place (no more stacking bubbles). The dict holds the interaction
# that carries the panel; its webhook token dies after 15 minutes,
# so the timer re-arms at each edit and deletes at 900 s max.
_SABOTAGE_PANELS: dict[tuple[str, str], dict[str, Any]] = {}
_SABOTAGE_PANEL_SECONDS = 900.0
_SABOTAGE_PANEL_SWEEP_SECONDS = 30.0


def _arm_sabotage_panel_timer(key: tuple[str, str], seconds: float | None = None) -> None:
    """(Re)arm the auto-destruction of one sabotage panel (sabotage-ux-v1).

    The panel deletes itself at the 15-minute webhook token limit —
    each edit RE-ARMS the timer so the panel lives exactly as long as
    the player works with it. A short ``seconds`` (no action left)
    sweeps the closed panel quickly.
    """
    panel = _SABOTAGE_PANELS.get(key)
    if panel is None:
        return
    existing = panel.get("task")
    if existing is not None:
        existing.cancel()
    interaction = panel["interaction"]

    async def _delete_later() -> None:
        try:
            await asyncio.sleep(seconds if seconds is not None else _SABOTAGE_PANEL_SECONDS)
            await interaction.delete_original_response()
        except Exception:
            logger.debug("kingdoms: sabotage panel auto-delete skipped", exc_info=True)
        finally:
            current = asyncio.current_task()
            if panel.get("task") is current:
                panel.pop("task", None)
            if _SABOTAGE_PANELS.get(key) is panel and "task" not in panel:
                _SABOTAGE_PANELS.pop(key, None)

    panel["task"] = asyncio.create_task(_delete_later())


async def _delete_sabotage_panel(key: tuple[str, str]) -> None:
    """Delete one player's sabotage panel NOW (best effort) and unarm it."""
    panel = _SABOTAGE_PANELS.pop(key, None)
    if panel is None:
        return
    task = panel.get("task")
    if task is not None:
        task.cancel()
    try:
        await panel["interaction"].delete_original_response()
    except Exception:
        logger.debug("kingdoms: sabotage panel delete skipped", exc_info=True)


async def _unique_notice(interaction: discord.Interaction, content: str) -> None:
    """Answer through the mod's fenêtre unique registry (PATCH 1.0 item 1).

    Drasah 29/10 live report: the draft's doublon warning stayed
    stacked under the DM after a corrected validation, and the
    sabotage refusal bubbles pile up next to the panel. Every
    REFUSAL/notice of the draft and sabotage flows now routes through
    the shared registry — a new refusal replaces the previous one, and
    the next successful step (validate, panel open) closes the stale
    window instead of letting bubbles stack."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _open_unique_ephemeral

    await _open_unique_ephemeral(interaction, content)


async def _close_stale_notice(user_id: str) -> None:
    """Close the player's stale refusal window (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _close_previous_ephemeral

    await _close_previous_ephemeral(user_id)


def _room_channel_name(guild: Any, prefix: str, *parts: str) -> str:
    """Build the duel channel name, unique per guild (battle-ai-rdv-v1).

    Drasah 22/10 (battle-room-id-v2): the readable-but-ugly generated
    names (« {joueur} attaque {map} de {kingdom} », the 🆚 voice
    title) become a duel name — ``⚔️-drasah-vs-bourgogne``. Discord
    lowercases everything: the slug strips accents, a numbered suffix
    (``-2``, ``-3``…) separates SIMULTANEOUS attacks on the same
    names; the existing salons are never renamed (no retro sweep).

    battle-ai-rdv-v1 (Drasah 22/10): the PREFIX is a parameter — 🗡️
    dague for the ATTACK salon (the attack itself), ⚔️ épées
    croisées for the VOCAL (a duel, there IS a defender — AI or
    player).
    """
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    base = f"{prefix}-" + "-vs-".join(_slug(str(part)) for part in parts) or f"{prefix}-bataille"
    base = base[:90]
    names = {str(getattr(channel, "name", "")) for channel in getattr(guild, "channels", [])}
    if base not in names:
        return base
    suffix = 2
    while f"{base}-{suffix}" in names:
        suffix += 1
    return f"{base}-{suffix}"


ATTACK_PANEL_MARKER = "<!-- kingdoms-attack -->"
_PARIS = ZoneInfo("Europe/Paris")

_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "panel_title": "⚔️ **Champ de bataille — Attaquer**",
        "panel_help": (
            "Déclarez une attaque sur un territoire adverse — le RDV est fixé à l'heure affichée.\n"
            "Déroulé : **Siège** (la défense s'ouvre dès la déclaration, dans le salon de l'attaque) → **Draft** "
            "(pick & ban en MP) → **RDV** (confirmez votre présence) → **Score** (la game, "
            "déclarez le résultat)."
        ),
        "panel_pointer": (
            "↳ Chaque attaque déclarée ouvre son PROPRE salon de bataille juste sous ce"
            " panel — défense, reveal et résultats y vivent."
        ),
        "history_button": "📜 Historique des confrontations",
        "history_title": "📜 **Historique des confrontations**",
        "history_empty": "🕊️ Aucune confrontation terminée pour l'instant.",
        "history_line": "• **{date}** — **{map}** · {attacker} ⚔️ {defender} — {outcome}",
        "history_outcome_win": "victoire de **{kingdom}**",
        "history_outcome_cancelled": "🏳️ attaque annulée",
        "history_outcome_double": "🏳️ double forfait",
        "history_outcome_expired": "⏳ expirée sans issue",
        "history_page": "Page {page}/{pages}",
        "itinerary_line": "⚔️ Itinéraire : exécution dans **{minutes} min** après la déclaration.",
        "itinerary_note": (
            "⚔️ Attaque déclarée sur **{map}** — itinéraire : exécution dans **{delay} min** "
            "— RDV **{time}** (heure de Paris)."
        ),
        "cancel_attack_button": "❌ Annuler l'attaque",
        "cancel_attack_done": (
            "🏳️ **Attaque annulée** — budget d'attaque remboursé"
            " (défense remboursée aussi si elle était enregistrée). Le salon de l'attaque disparaît."
        ),
        "cancel_defense_button": "↩️ Retirer ma défense",
        "cancel_defense_done": "🛡️ Défense retirée — budget de défense remboursé.",
        "defend_wrong_kingdom": "❌ Vous ne pouvez défendre ce territoire.",
        "defend_already": "❌ Le royaume est déjà défendu.",
        "room_not_player": "❌ Seuls les deux joueurs du match peuvent agir ici.",
        "board_title": "🗡️ **Attaques en cours**",
        "board_empty": "🕊️ Aucune attaque en cours.",
        "panel_empty": "🕊️ Aucune attaque en cours.",
        "attack_line": "⚔️ **{map}** — {attacker} ({kingdom}) 🆚 {defender} — RDV **{time}** (Paris) · {phase}",
        "gaia_line": "🦅 **{map}** — bataille Gaïa · {count} combattant(s) — RDV **{time}** (Paris)",
        "gaia_join_button": "🦅 Rejoindre la bataille",
        "gaia_join_note": (
            "🦅 Tu as rejoint la bataille sur **{map}** ({count} combattants) — RDV **{time}** (heure de Paris)."
        ),
        "gaia_join_announce": "🦅 **{lord}** rejoint la bataille sur **{map}** ({count} combattants) !",
        "gaia_gone": "❌ Cette bataille n'accepte plus de participants.",
        "announce_v1_gaia": (
            "\n🦅 D'autres seigneurs peuvent rejoindre la bataille via le bouton"
            " **Rejoindre** du panel Attaquer (jusqu'à {max} combattants) !"
        ),
        "phase_siege": "Siège",
        "phase_draft": "Draft",
        "phase_ready": "RDV",
        "phase_score": "Score",
        "reveal_battle": "⚔️ Bataille en vue (défenseur : {defender})",
        "reveal_battle_anon": "⚔️ Bataille en vue",
        "defense_manager_button": "🛡️ Gestion de la défense",
        "defense_manager_title": "🛡️ Gestion de la défense — attaques contre **{kingdom}**",
        "defense_manager_empty": "🐺 Aucune attaque en cours contre ton royaume.",
        "defense_manager_row": "• **{map}** — RDV **{time}** (Paris) · {state}",
        "defense_manager_defended": "défendue par **{lord}** ✓",
        "defense_manager_waiting": "en attente de défenseur ⏳",
        "defense_manager_not_defender": "❌ Tu n'es pas un seigneur actif d'un royaume.",
        "defense_manager_no_service": "❌ Le service des attaques est indisponible.",
        "plan_button": "📅 Planifier une attaque",
        "plan_manager_button": "⚔️ Gestion d'attaque",
        "plan_manager_title": "⚔️ Gestion d'attaque — **{kingdom}**",
        "plan_manager_declared_section": "**⚔️ Attaques déclarées**",
        "plan_manager_planned_section": "**📅 Attaques planifiées**",
        "plan_manager_empty": "Aucune attaque en cours ni planifiée.",
        "plan_manager_declared_row": "• **{map}** — RDV **{time}** (Paris) · {state}",
        "plan_manager_planned_row": "• **{map}** — RDV **{time}** (Paris) · {lord} · choix {index}/{total}",
        "plan_disclaimer": "⚠️ Une plannification ne réserve pas le territoire : si un autre royaume déclare une attaque dessus avant l'heure choisie, ta plannification basculera sur ton choix suivant — ou sera rompue s'il n'en reste aucun.",
        "plan_wizard_title": "📅 Planifier une attaque",
        "plan_time_day_label": "Jour du RDV",
        "plan_time_day_today": "Aujourd'hui — {date}",
        "plan_time_day_tomorrow": "Demain — {date}",
        "plan_time_hour_label": "Heure",
        "plan_time_minute_label": "Minute",
        "plan_time_hint": "Choisis le jour, l'heure et la minute du RDV (heure de Paris), puis valide. Règle d'or : 24h maximum à l'avance.",
        "plan_time_validate_button": "✅ Valider l'heure",
        "plan_time_invalid": "❌ Ce RDV est impossible : il doit être dans le futur et dans les 24h. Choisis un autre créneau.",
        "plan_confirm_title": "📅 Planification — choix 1 : **{map}** · RDV **{time}** (Paris)",
        "plan_choice2_placeholder": "Choix 2 (facultatif)",
        "plan_choice3_placeholder": "Choix 3 (facultatif)",
        "plan_confirm_button": "✅ Confirmer la planification",
        "plan_dm_header": "📅 Plannification d'attaque — {map}",
        "plan_choices_line": "Choix {n} : **{map}**{active}",
        "plan_active_tag": " ✅",
        "plan_dm_time": "RDV : **{time}** (Paris) — déclaration automatique à **{declare_time}** (Paris).",
        "plan_dm_budget": "Le budget d'attaque (⚔️) sera consommé à la DÉCLARATION — pas maintenant.",
        "plan_created_note": "📅 Plannification enregistrée — le récap est dans tes messages privés.",
        "plan_no_dm": "⚠️ Plannification enregistrée, mais l'envoi du MP a échoué (MPs fermés ?).",
        "plan_failed": "❌ La plannification a échoué : {message}",
        "plan_cancel_button": "❌ Annuler ma plannification",
        "plan_cancelled_ephemeral": "❌ Plannification annulée — aucun budget n'a été consommé.",
        "plan_event_executed": "✅ Ton attaque planifiée a été DÉCLARÉE sur **{map}** — RDV **{time}** (Paris). Le budget d'attaque a été consommé à la déclaration.",
        "plan_event_switched": "⚠️ Ton choix sur **{old}** ne peut aboutir (la bataille a déjà été déclarée dessus) — bascule sur le choix suivant : **{map}**.",
        "plan_event_broken": "⛔ Ton attaque planifiée ne peut aboutir : la bataille a déjà été déclarée sur **{map}** et aucun autre choix n'est disponible. Aucun budget n'a été consommé.",
        "plan_event_cancelled": "❌ Ta plannification a été annulée — aucun budget n'a été consommé.",
        "plan_no_service": "❌ Le service des attaques est indisponible.",
        "lord_engaged": "⛔ Tu es engagé dans une bataille en cours — attends son issue avant d'attaquer, planifier, embusquer ou défendre.",
        "lord_tavern_locked": "⛔ Tu es sous l'effet d'une boisson de la Taverne — attends la fin du verrou avant d'attaquer, planifier, embusquer ou défendre.",
        "reveal_no_defense": "🏳️ Assaut irrésistible — aucune défense",
        "defender_pending": "en attente de défense",
        "defender_anon": "défense en place 🔒",
        "defender_ai": "IA 🤖",
        "covered_tag": " 🕶️",
        "declare_button": "⚔️ Déclarer une attaque",
        "attack_info_button": "⚔️ Information attaque / défense",
        "defend_button": "🛡️ Défendre",
        "modal_title": "⚔️ Déclarer une attaque",
        "modal_map": "Territoire adverse (clé de carte)",
        "modal_lobby": "URL du lobby de jeu",
        "modal_cover": "Seigneur de couverture (mention, optionnel)",
        "modal_traquenard": "Traquenard ? (oui/non)",
        "declared_note": "⚔️ Attaque déclarée sur **{map}** — RDV à **{time}** (heure de Paris).",
        "declared_announce": (
            "⚔️ **{attacker}** ({kingdom}) attaque **{map}** 🆚 {defender_kingdom} — RDV **{time}** (heure de Paris) !"
        ),
        "defense_done": "🛡️ **{map}** — tu portes la défense de ton royaume ! RDV **{time}** (Paris).",
        "failed": "❌ {message}",
        "not_a_lord": "❌ Tu n'es pas un seigneur actif d'un royaume.",
        "no_service": "❌ Le service des attaques est indisponible.",
        "no_target": "❌ Aucun royaume adverse attaquable pour le moment.",
        "choose_kingdom": "🎯 **Choisis le royaume à attaquer :**",
        "protected_line": "🛡️ Sous patrouille (impossible d'attaquer) : {kingdoms}",
        "patrol_kingdom": "**{name}** jusqu'à {end}h00",
        "ai_level_desc": "IA de défense : {level}/5 · {count} territoire(s)",
        "choose_territory": "🗺️ **Choisis le territoire de {kingdom} à attaquer :**",
        "mode_choose": "⚔️ **Déclarer une attaque — choisis ton itinéraire :**",
        "mode_classic_button": "⚔️ Attaque classique — {delay} itinéraire",
        "mode_embuscade_button": "🥷 Utiliser une embuscade — {delay} itinéraire",
        "embuscade_armed_line": (
            "🥷 **Embuscade armée** ({count} restant) — attaque exceptionnelle, hors budget hebdo."
        ),
        "embuscade_choose": "🎯 **Embuscade — choisis le territoire cible :** (attaque exceptionnelle, hors budget hebdo)",
        "embuscade_note": (
            "🎯 **Embuscade déclarée sur {map}** — itinéraire **{delay} min**, rendez-vous à"
            " **{time}** (heure de Paris). La défense ne consomme PAS le point hebdo."
        ),
        "traquenard_hint": (
            "🪤 Info : un royaume peut activer un Traquenard sur son adversaire — l'IA de"
            " défense devient Facile pendant 6h et les mariages scellés prennent +2h de verrou."
        ),
        "cover_question": (
            "🕶️ Utiliser un contre-espionnage ? (1 charge — l'attaque sera annoncée sous"
            " le pseudo d'un autre seigneur — de ton royaume ou d'un autre —"
            " démasqué à la fin du Siège)"
        ),
        "cover_yes_button": "🕶️ OUI — bluffer",
        "cover_no_button": "❌ NON — à visage découvert",
        "choose_cover": (
            "🕶️ Sous l'identité de quel seigneur déclarer l'attaque ?"
            " (tous royaumes, sauf le royaume attaqué)"
        ),
        "announce_v1": (
            "⚔️ {attacker} du royaume {kingdom} a lancé une attaque sur le royaume"
            " {defender} !\n📅 {date}\nBonne chance aux combattants ! 🍀"
        ),
        "announce_v1_traquenard": (
            "\n🪤 Un Traquenard est actif — l'IA du royaume défenseur est Facile (fenêtre 6h) !"
        ),
        "date_full": "{weekday} {day}/{month}/{year} à {hour}h{minute}",
        "weekdays": "Lundi,Mardi,Mercredi,Jeudi,Vendredi,Samedi,Dimanche",
        "thread_body": (
            "📜 **Déroulé de l'attaque & droits du défenseur**\n"
            "• **Siège** — la défense reste ouverte ; à la fin du Siège (reveal), le bluff"
            " (s'il y en a un) tombe et sans défenseur l'IA du royaume prend la défense.\n"
            "• **Draft** — le pick & ban part en MP : 3 civilisations classées de votre"
            " main, validation des deux côtés (les civs partent au salon de la bataille).\n"
            "• **RDV** — un salon vocal de bataille s'ouvre à l'heure du RDV : une absence"
            " au vocal est un forfait.\n"
            "• **Score** — la game : déclarez le résultat avec l'ID de la game.\n"
            "🛡️ N'importe quel seigneur du royaume défenseur peut défendre via le"
            " bouton du panel **Déclarer une attaque**.\n"
            "🪤 Un royaume peut activer un Traquenard sur son adversaire : l'IA de"
            " défense devient Facile pendant 6h et les mariages scellés prennent +2h"
            " de verrou."
        ),
        "reveal_cover": (
            "🕶️ Reveal — l'attaque sur **{map}** était menée par **{attacker}** du"
            " royaume **{attacker_kingdom}**, sous la couverture de **{cover}** du"
            " royaume **{cover_kingdom}**. La couverture tombe."
        ),
        "reveal_ai": (
            "🤖 Personne n'a défendu **{map}** — l'IA du royaume **{kingdom}** prend la défense"
            " (niveau **{level}**/5) !"
        ),
        "reveal_defense": "🛡️ La défense de **{map}** est verrouillée — RDV **{time}** (Paris).",
        "pickban_dm": (
            "⚔️ **Pick & ban — {map}**\nClasse tes **{count}** pick de civilisation par ordre de préférence "
            "(ton classement ordonne les replis).\n"
            "🕒 **Fermeture de la fenêtre : {deadline}** (heure du RDV).\n"
            "Si tu possèdes un Sabotage ou un Faucon, tu pourras en faire usage dans le salon de la bataille.\n"
            "\n"
            "⚠️ **Règle anti-blocage** : si tu ne valides aucun choix avant le RDV, "
            "3 civilisations seront tirées **au hasard** de ta main — et aucun "
            "sabotage ni faucon ne s'appliquera. Le RDV a lieu quoi qu'il arrive.\n"
            "Ta main : {hand}\nMain adverse : {theirs}"
        ),
        "pickban_pick": "Choix {n}",
        "pickban_validate": "✅ Valider",
        "pickban_missing": "❌ Classe d'abord tes {count} civilisations.",
        "pickban_duplicate": (
            "❌ Vous ne pouvez pas avoir de doublons dans vos choix de civilisations."
        ),
        "pickban_waiting": "⏳ Picks enregistrés et validés — en attente de l'adversaire.",
        "pickban_reveal_dm": (
            "🎭 **Reveal — {map}**\nLes 3 civilisations potentielles de ton"
            " adversaire (ordre aléatoire) :\n**{civs}**\n\n🕒 **RDV : {deadline}** —"
            " la fenêtre de sabotage se ferme à cette heure.\n🗡️ Veux-tu saboter"
            " ou utiliser un Faucon ? — jusqu'à 2 civilisations adverses"
            " retirées par camp.\n⚔️ Sinon, valide avec Passer — un joueur"
            " absent du salon vocal à l'heure du RDV est déclaré forfait !"
        ),
        "sab_open_button": "🗡️ Saboter / Utiliser un Faucon",
        "pickban_default_reveal_dm": (
            "🎭 **Reveal — {map}**\n⏱️ Le draft a été clôturé à l'heure : les"
            " civilisations jamais classées ont été tirées au sort (aucun"
            " sabotage ni Faucon).\n🕒 Le RDV a lieu maintenant — rejoins le"
            " salon vocal.\nLes 3 civilisations potentielles de ton"
            " adversaire (ordre aléatoire) :\n**{civs}**"
        ),
        "sab_pass_button": "⚔️ Passer — je suis prêt",
        "sab_title": (
            "🪶 **Phase de sabotage** — Faucon ou Sabotage (jusqu'à 2 par camp),"
            " puis reviens valider avec ⚔️ Passer dans le message du draft."
        ),
        "sab_no_resources": (
            "🕊️ **Vous ne disposez pas de sabotage ni de Faucon** — en attente"
            " de ton adversaire. Tu peux valider avec ⚔️ Passer."
        ),
        "sab_faucon": "🦅 Faucon",
        "sab_techno": "🧪 Sabotage",
        "sab_choose": "🎯 **Choisis la civilisation adverse à retirer :**",
        "sab_done": "🪶 **{civ}** retirée du camp adverse.",
        "sab_settled": "✅ Ton camp a validé — en attente de l'autre camp…",
        "sab_phase_done": (
            "🎭 **Phase de sabotage terminée — {map}**\nRejoins le salon vocal à"
            " l'heure du RDV : les civilisations finales y seront affichées."
        ),
        "sab_over": "🕊️ La phase de sabotage est terminée.",
        "sab_empty": "🕊️ Aucune civilisation adverse disponible.",
        "sab_not_player": "❌ Seuls les deux joueurs du match peuvent agir ici.",
        "sab_stock_line": (
            "🗡️ Sabotage : {sabotage} · 🦅 Faucon : {faucon}\n"
            "Un camp ne peut subir que 2 civilisations sabotées maximum."
        ),
        "sab_faucon_ready": "dispo",
        "sab_faucon_flying": "en vol jusqu'à {time}",
        "sab_faucon_absent": "absent",
        "sab_panel_title": "🪶 **Sabotage** — {used}/2 civilisations retirées sur cette attaque",
        "sab_panel_used": "Retirées : {list}",
        "sab_panel_none": "Aucune civilisation retirée pour l'instant.",
        "sab_panel_done": "🪶 **{civ}** retirée du camp adverse — {used}/2.",
        "sab_again_ask": "🗡️ Veux-tu saboter une autre civilisation ?",
        "sab_again_yes": "Oui — un autre sabotage",
        "sab_again_no": "Non — j'ai fini",
        "room_ping_winner": "🏆 Vainqueur : **{winner}**",
        "ready_button": "✅ Prêt — confirme ta présence",
        "ready_done": "✅ Présence confirmée — rejoins le salon de la bataille à l'heure du RDV.",
        "room_role": "Bataille {map}",
        "room_scoreboard_map": "⚔️ {map}",
        "room_scoreboard_id": "🆔 ID : {id}",
        "room_side_attacker": "Attaquant",
        "room_side_defender": "Défenseur",
        "room_side_ready": "✅ Prêt",
        "room_side_wait": "⏳ en attente du Prêt",
        "room_side_none": "🔗 en attente de l'ID",
        "room_side_ai": "🤖 IA — niv. {level}/5",
        "room_ai_disclaimer": (
            "⚠️ **IA de niveau {level}/5** — la partie se joue en multijoueur"
            " avec un ID (jamais en local) pour rester traçable et vérifiable"
            " post-game. Le joueur est cru sur parole ; toute fraude présumée"
            " peut être contestée ⚖️ puis arbitrée par les admins."
        ),
        "room_id_button": "🔗 Renseigner l'ID",
        "room_id_modify_button": "🔴 Modifier l'ID",
        "room_id_pending_button": "⏳ Saisie de l'ID en cours…",
        "room_ready_attacker_button": "⚔️ Prêt — {player}",
        "room_ready_defender_button": "Prêt 🛡️ — {player}",
        "room_not_your_side": ("⛔ Ce bouton Prêt est réservé à votre adversaire."),
        "room_scoreboard_footer": ("🎮 Les deux joueurs sont prêts — bonne game ! Déclarez le vainqueur avec 🏆."),
        "room_ping_message": ("⚔️ {attacker} {defender} — la salle de bataille est ouverte, à vos armes !"),
        "room_ping_attacker_only": ("⚔️ {attacker} — la salle de bataille est ouverte, à vos armes !"),
        "room_dm": (
            "🏟️ **Tiens-toi prêt — {map}**\n"
            "Rejoins ton adversaire en vocal : {room}\n"
            "Rejoins le salon textuel pour continuer : {salon}"
        ),
        "room_dm_deadline": (
            "\n\n⏳ Présence obligatoire (vocal ou ✅ Prêt) avant {deadline} —"
            " passé ce délai, un absent est déclaré forfait."
        ),
        "room_dm_luck": "\n\nBonne chance ! 🍀",
        "result_button": "🏆 Déclarer le vainqueur",
        "draft_button": "📜 Voir la draft",
        "draft_sheet": "📜 Draft — {map}",
        "draft_sheet_help": (
            "Ordre des choix des joueurs durant la draft (ordre aléatoire —"
            " le classement reste secret), les sabotées barrées :"
        ),
        "result_select_title": "🏆 **Qui remporte la partie ?**",
        "result_select_placeholder": "Le vainqueur de la game",
        "result_confirm_title": "🏆 **Vainqueur : {winner}** — confirmer ce résultat ?",
        "result_confirm_button": "✅ Confirmer",
        "result_edit_button": "✏️ Modifier",
        "result_review_title": "⏳ **{player} a déclaré {winner} vainqueur.**",
        "result_review_confirm_button": "✅ Confirmer le résultat",
        "result_review_contest_button": "⚖️ Contester",
        "score_url_posted": "🔗 **URL de la partie** : {url}",
        "score_pending": (
            "⏳ Résultat enregistré (game {game}) — il sera appliqué sauf"
            " contestation de l'autre joueur dans les {minutes} prochaines minutes."
        ),
        "forfeit_both": (
            "🏳️ Forfait général sur **{map}** — personne n'a validé : l'attaquant, présent, l'emporte (**{kingdom}**)."
        ),
        "forfeit_side": "🏳️ Forfait sur **{map}** — **{side}** a manqué la validation : victoire de **{kingdom}**.",
        "side_attacker": "l'attaquant",
        "side_defender": "le défenseur",
        "score_url_button": "🔗 ID de la partie",
        "score_url_modal_title": "ID de la partie",
        "score_url_modal_label": "ID de la partie",
        "score_url_modal_placeholder": "ex. 123456",
        "score_reminder_dm": (
            "⏰ **RDV — {map}** : aucun résultat déclaré depuis 3h — déclare le"
            " résultat de la game avec le bouton 🏆 du salon de la bataille !"
        ),
        "room_ready_button": "✅ Prêt",
        "room_start": (
            "🎮 **La partie commence — {map}** !\n{mentions} — retrouvez-vous dans le salon vocal et bonne game ! 🍀"
        ),
        "room_event_name": "⚔️ {map} — {attacker} 🆚 {defender}",
        "room_event_description": (
            "⚔️ Bataille Kingdoms en direct — {attacker} 🆚 {defender}.\n"
            "Civilisations et boutons de jeu : dans le salon vocal de la bataille."
        ),
        "room_ready_needs_url": (
            "🔗 Renseigne d'abord l'ID de la partie (bouton 🔗) — le bouton ✅ Prêt n'apparaît qu'après."
        ),
        "result_not_ready": ("⏳ Le bouton Déclarer le vainqueur apparaît une fois que les deux joueurs sont Prêts."),
        "conquest_title": "⚔️ Le Royaume **{winner}** a conquis **{map}** !",
        "conquest_defense_title": "🛡️ Le Royaume **{winner}** a défendu **{map}** !",
        "conquest_thread": "Détail de la conquête",
        "conquest_thread_defense": "Détail de la défense",
        "conquest_attacker_line": "⚔️ Attaquant : **{player}** ({kingdom})",
        "conquest_defense_line": "🛡️ Défense : **{player}** ({kingdom})",
        "conquest_ai_defense": "🛡️ Défense : IA du royaume **{kingdom}** (niveau {level})",
        "conquest_civ_line": "🎯 {player} jouait **{civ}**",
        "conquest_embuscade_line": "🥷 Embuscade utilisée",
        "conquest_sabotages_line": "🧨 Sabotages ({side}, Faucon/Techno) : {civs}",
        "conquest_id_line": "🔗 ID de la partie : **{game}**",
        "conquest_none": "aucune",
        "conquest_side_attacker": "attaquant",
        "conquest_side_defender": "défenseur",
        "conquest_married_lost": ("💔 **{player}** perd son lien sacré avec la civilisation **{civ}**."),
        "score_resolved": ("🏆 Résultat enregistré — **{winner}** remporte **{map}** (game {game})."),
        "score_already": "✅ Ce résultat était déjà enregistré — rien à faire.",
        "score_disputed": (
            "⚖️ Déclaration contradictoire — les admins arbitrent (game {game})."
            "\nVeuillez prendre contact avec un admin."
        ),
        "noshow_side": ("🏳️ **{map}** — **{side}** a manqué le RDV : victoire de **{kingdom}**."),
        "noshow_both": ("🏳️ **{map}** — double forfait : aucun joueur au RDV, le territoire reste à son propriétaire."),
        "dispute_admin_dm": ("⚖️ **Litige — {map}**\n{declarations}\nTranche le résultat (game {game})."),
        "auto_dispute": (
            "⚖️ **Litige auto-ouvert sur {map}** — aucun résultat déclaré 15 h après"
            " l'ouverture de la partie : un admin tranche via la console (`resultat`)."
        ),
        "dispute_win_attacker": "⚖️ Tranche : attaquant",
        "dispute_win_defender": "⚖️ Tranche : défenseur",
        "dispute_double": "🏳️ Double forfait",
        "dispute_applied": "⚖️ Arbitrage appliqué — **{kingdom}** remporte **{map}**.",
        "dispute_forfeit": "⚖️ Arbitrage appliqué — double forfait sur **{map}**.",
        "dispute_not_admin": "❌ Seuls les bot-admins arbitrent un litige.",
    },
    "en": {
        "panel_title": "⚔️ **Battlefield — Attack**",
        "panel_help": (
            "Declare an attack on an adversary territory — the meeting is set at the displayed time.\n"
            "Flow: **Siege** (the defense opens at the declaration, in the attack's own salon) → **Draft** "
            "(DM pick & ban) → **Meeting** (confirm your presence) → **Score** (the game, "
            "declare the result)."
        ),
        "panel_pointer": (
            "↳ Every declared attack opens its OWN battle salon right under this"
            " panel — defense, reveal and results live there."
        ),
        "history_button": "📜 History of confrontations",
        "history_title": "📜 **History of confrontations**",
        "history_empty": "🕊️ No finished confrontation yet.",
        "history_line": "• **{date}** — **{map}** · {attacker} ⚔️ {defender} — {outcome}",
        "history_outcome_win": "victory of **{kingdom}**",
        "history_outcome_cancelled": "🏳️ attack cancelled",
        "history_outcome_double": "🏳️ double forfeit",
        "history_outcome_expired": "⏳ expired with no outcome",
        "history_page": "Page {page}/{pages}",
        "itinerary_line": "⚔️ Itinerary: execution **{minutes} min** after the declaration.",
        "itinerary_note": (
            "⚔️ Attack declared on **{map}** — itinerary: execution in **{delay} min** "
            "— meeting **{time}** (Paris time)."
        ),
        "cancel_attack_button": "❌ Cancel the attack",
        "cancel_attack_done": (
            "🏳️ **Attack cancelled** — attack budget refunded"
            " (the defense refunded too when one was recorded). The attack salon disappears."
        ),
        "cancel_defense_button": "↩️ Withdraw my defense",
        "cancel_defense_done": "🛡️ Defense withdrawn — defense budget refunded.",
        "defend_wrong_kingdom": "❌ You cannot defend this territory.",
        "defend_already": "❌ The kingdom is already defended.",
        "room_not_player": "❌ Only the two players of the match may act here.",
        "board_title": "🗡️ **Ongoing attacks**",
        "board_empty": "🕊️ No ongoing attack.",
        "panel_empty": "🕊️ No ongoing attack.",
        "attack_line": "⚔️ **{map}** — {attacker} ({kingdom}) 🆚 {defender} — meets **{time}** (Paris) · {phase}",
        "gaia_line": "🦅 **{map}** — Gaïa battle · {count} fighter(s) — meets **{time}** (Paris)",
        "gaia_join_button": "🦅 Join the battle",
        "gaia_join_note": (
            "🦅 You joined the battle on **{map}** ({count} fighters) — meeting **{time}** (Paris time)."
        ),
        "gaia_join_announce": "🦅 **{lord}** joins the battle on **{map}** ({count} fighters)!",
        "gaia_gone": "❌ This battle no longer accepts participants.",
        "announce_v1_gaia": (
            "\n🦅 Other lords may join the battle through the **Join** button of"
            " the Attack panel (up to {max} fighters)!"
        ),
        "phase_siege": "Siege",
        "phase_draft": "Draft",
        "phase_ready": "Meeting",
        "phase_score": "Score",
        "reveal_battle": "⚔️ Battle ahead (defender: {defender})",
        "reveal_battle_anon": "⚔️ Battle ahead",
        "defense_manager_button": "🛡️ Defense management",
        "defense_manager_title": "🛡️ Defense management — attacks against **{kingdom}**",
        "defense_manager_empty": "🐺 No ongoing attack against your kingdom.",
        "defense_manager_row": "• **{map}** — meeting **{time}** (Paris) · {state}",
        "defense_manager_defended": "defended by **{lord}** ✓",
        "defense_manager_waiting": "waiting for a defender ⏳",
        "defense_manager_not_defender": "❌ You are no active lord of a kingdom.",
        "defense_manager_no_service": "❌ The attack service is unavailable.",
        "plan_button": "📅 Plan an attack",
        "plan_manager_button": "⚔️ Attack management",
        "plan_manager_title": "⚔️ Attack management — **{kingdom}**",
        "plan_manager_declared_section": "**⚔️ Declared attacks**",
        "plan_manager_planned_section": "**📅 Planned attacks**",
        "plan_manager_empty": "No ongoing or planned attack.",
        "plan_manager_declared_row": "• **{map}** — meeting **{time}** (Paris) · {state}",
        "plan_manager_planned_row": "• **{map}** — meeting **{time}** (Paris) · {lord} · choice {index}/{total}",
        "plan_disclaimer": "⚠️ A planning reserves NOTHING: if another kingdom declares an attack on the territory before your chosen hour, your planning switches to your next choice — or breaks when none remains.",
        "plan_wizard_title": "📅 Plan an attack",
        "plan_time_day_label": "Meeting day",
        "plan_time_day_today": "Today — {date}",
        "plan_time_day_tomorrow": "Tomorrow — {date}",
        "plan_time_hour_label": "Hour",
        "plan_time_minute_label": "Minute",
        "plan_time_hint": "Pick the meeting day, hour and minute (Paris time), then validate. Golden rule: 24h maximum ahead.",
        "plan_time_validate_button": "✅ Validate the time",
        "plan_time_invalid": "❌ That meeting is impossible: it must be in the future and within 24h. Pick another slot.",
        "plan_confirm_title": "📅 Planning — choice 1: **{map}** · meeting **{time}** (Paris)",
        "plan_choice2_placeholder": "Choice 2 (optional)",
        "plan_choice3_placeholder": "Choice 3 (optional)",
        "plan_confirm_button": "✅ Confirm the planning",
        "plan_dm_header": "📅 Attack planning — {map}",
        "plan_choices_line": "Choice {n}: **{map}**{active}",
        "plan_active_tag": " ✅",
        "plan_dm_time": "Meeting: **{time}** (Paris) — automatic declaration at **{declare_time}** (Paris).",
        "plan_dm_budget": "The attack budget (⚔️) is consumed at the DECLARATION — not now.",
        "plan_created_note": "📅 Planning saved — the recap is in your DMs.",
        "plan_no_dm": "⚠️ Planning saved, but the DM failed (DMs closed?).",
        "plan_failed": "❌ The planning failed: {message}",
        "plan_cancel_button": "❌ Cancel my planning",
        "plan_cancelled_ephemeral": "❌ Planning cancelled — no budget was consumed.",
        "plan_event_executed": "✅ Your planned attack was DECLARED on **{map}** — meeting **{time}** (Paris). The attack budget was consumed at the declaration.",
        "plan_event_switched": "⚠️ Your choice on **{old}** cannot proceed (the battle was already declared there) — switched to the next choice: **{map}**.",
        "plan_event_broken": "⛔ Your planned attack cannot proceed: the battle was already declared on **{map}** and no other choice is available. No budget was consumed.",
        "plan_event_cancelled": "❌ Your planning was cancelled — no budget was consumed.",
        "plan_no_service": "❌ The attack service is unavailable.",
        "lord_engaged": "⛔ You are engaged in an ongoing battle — wait for its outcome before attacking, planning, ambushing or defending.",
        "lord_tavern_locked": "⛔ You are under a tavern drink's effect — wait for the lock to end before attacking, planning, ambushing or defending.",
        "reveal_no_defense": "🏳️ Irresistible assault — no defense",
        "defender_pending": "waiting for a defender",
        "defender_anon": "defense in place 🔒",
        "defender_ai": "AI 🤖",
        "covered_tag": " 🕶️",
        "declare_button": "⚔️ Declare an attack",
        "attack_info_button": "⚔️ Attack / defense information",
        "defend_button": "🛡️ Defend",
        "modal_title": "⚔️ Declare an attack",
        "modal_map": "Adversary territory (map key)",
        "modal_lobby": "Game lobby URL",
        "modal_cover": "Cover lord (mention, optional)",
        "modal_traquenard": "Ambush trap? (yes/no)",
        "declared_note": "⚔️ Attack declared on **{map}** — meeting at **{time}** (Paris time).",
        "declared_announce": (
            "⚔️ **{attacker}** ({kingdom}) attacks **{map}** 🆚 {defender_kingdom} — meeting **{time}** (Paris time)!"
        ),
        "defense_done": "🛡️ **{map}** — you carry your kingdom's defense! Meeting **{time}** (Paris).",
        "failed": "❌ {message}",
        "not_a_lord": "❌ You are no active lord of a kingdom.",
        "no_service": "❌ The attack service is unavailable.",
        "no_target": "❌ No adversary kingdom is attackable right now.",
        "choose_kingdom": "🎯 **Choose the kingdom to attack:**",
        "protected_line": "🛡️ Under patrol (cannot be attacked): {kingdoms}",
        "patrol_kingdom": "**{name}** until {end}:00",
        "ai_level_desc": "Defense AI: {level}/5 · {count} territor(y|ies)",
        "choose_territory": "🗺️ **Choose the territory of {kingdom} to attack:**",
        "mode_choose": "⚔️ **Declare an attack — choose your itinerary:**",
        "mode_classic_button": "⚔️ Classic attack — {delay} itinerary",
        "mode_embuscade_button": "🥷 Use an ambush — {delay} itinerary",
        "embuscade_armed_line": (
            "🥷 **Ambush armed** ({count} left) — exceptional attack, outside the weekly budget."
        ),
        "embuscade_choose": "🎯 **Ambush — choose the target territory:** (exceptional attack, outside the weekly budget)",
        "embuscade_note": (
            "🎯 **Ambush declared on {map}** — itinerary **{delay} min**, meeting at"
            " **{time}** (Paris time). The defense does NOT consume the weekly point."
        ),
        "traquenard_hint": (
            "🪤 Info: a kingdom may activate a Traquenard on its adversary — the defense"
            " AI turns Easy for 6h and sealed marriages gain +2h of lock."
        ),
        "cover_question": (
            "🕶️ Use a counter-espionage? (1 charge — the attack is announced under"
            " another lord's name — yours or a foreign kingdom's — unmasked at"
            " the end of the Siege)"
        ),
        "cover_yes_button": "🕶️ YES — bluff",
        "cover_no_button": "❌ NO — open-faced",
        "choose_cover": (
            "🕶️ Under which lord's identity should the attack be declared?"
            " (any kingdom, never the attacked one)"
        ),
        "announce_v1": (
            "⚔️ {attacker} of kingdom {kingdom} has launched an attack on kingdom"
            " {defender}!\n📅 {date}\nGood luck to the fighters! 🍀"
        ),
        "announce_v1_traquenard": ("\n🪤 A Traquenard is active — the defender kingdom's AI is Easy (6h window)!"),
        "date_full": "{weekday} {day}/{month}/{year} at {hour}:{minute}",
        "weekdays": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "thread_body": (
            "📜 **Attack rundown & defender rights**\n"
            "• **Siege** — the defense stays open; at the end of the Siege (reveal), the"
            " bluff (if any) drops and with no defender the kingdom's AI takes the defense.\n"
            "• **Draft** — the pick & ban lands in the DMs: 3 ranked civilizations,"
            " both sides validate (the civs land in the battle room).\n"
            "• **Meeting** — a battle voice room opens at the meeting time: a miss"
            " from the voice room is a forfeit.\n"
            "• **Score** — the game: declare the result with the game ID.\n"
            "🛡️ Any lord of the defending kingdom may defend via the button of the"
            " **Declare an attack** panel.\n"
            "🪤 A kingdom may activate a Traquenard on its adversary: the defense AI"
            " turns Easy for 6h and sealed marriages gain +2h of lock."
        ),
        "reveal_cover": (
            "🕶️ Reveal — the attack on **{map}** was led by **{attacker}** of kingdom"
            " **{attacker_kingdom}**, under the cover of **{cover}** of kingdom"
            " **{cover_kingdom}**. The cover drops."
        ),
        "reveal_ai": (
            "🤖 Nobody defended **{map}** — the AI of kingdom **{kingdom}** takes the defense (level **{level}**/5)!"
        ),
        "reveal_defense": "🛡️ The defense of **{map}** is locked — meeting **{time}** (Paris).",
        "pickban_dm": (
            "⚔️ **Pick & ban — {map}**\nRank your **{count}** civilization picks by"
            " order of preference (your ranking orders the fallbacks).\n"
            "🕒 **Window closes: {deadline}** (meeting time).\n"
            "If you own a Sabotage or a Faucon, you will use it in the battle room.\n"
            "\n"
            "⚠️ **Anti-blocking rule**: no choice validated before the meeting → "
            "3 **random** civilizations from your hand — and no sabotage or faucon "
            "applies. The meeting happens anyway.\n"
            "Your hand: {hand}\nAdversary hand: {theirs}"
        ),
        "pickban_pick": "Choice {n}",
        "pickban_validate": "✅ Validate",
        "pickban_missing": "❌ Rank your {count} civilizations first.",
        "pickban_duplicate": "❌ You cannot have duplicates in your civilization choices.",
        "pickban_waiting": "⏳ Picks recorded and validated — waiting for the adversary.",
        "pickban_reveal_dm": (
            "🎭 **Reveal — {map}**\nYour adversary's 3 potential civilizations"
            " (random order):\n**{civs}**\n\n🕒 **Meeting: {deadline}** — the sabotage"
            " window closes at that time.\n🗡️ Do you want to sabotage or use"
            " a Faucon? — up to 2 enemy civilizations removed per side.\n⚔️"
            " Otherwise confirm with Pass — a player missing from the voice"
            " room at the meeting time is a forfeit!"
        ),
        "sab_open_button": "🗡️ Sabotage / Use a Faucon",
        "pickban_default_reveal_dm": (
            "🎭 **Reveal — {map}**\n⏱️ The draft closed on time: the"
            " never-ranked civilizations were drawn at random (no sabotage,"
            " no Faucon).\n🕒 The meeting is NOW — join the voice salon.\n"
            "Your adversary's 3 potential civilizations"
            " (random order):\n**{civs}**"
        ),
        "sab_pass_button": "⚔️ Pass — I am ready",
        "sab_title": (
            "🪶 **Sabotage phase** — Faucon or Sabotage (up to 2 per side),"
            " then come back and confirm with ⚔️ Pass on the draft message."
        ),
        "sab_no_resources": (
            "🕊️ **You have no sabotage nor a Faucon** — waiting for your adversary. You can confirm with ⚔️ Pass."
        ),
        "sab_faucon": "🦅 Faucon",
        "sab_techno": "🧪 Sabotage",
        "sab_choose": "🎯 **Pick the enemy civilization to remove:**",
        "sab_done": "🪶 **{civ}** removed from the enemy side.",
        "sab_settled": "✅ Your side confirmed — waiting for the other side…",
        "sab_phase_done": (
            "🎭 **Sabotage phase over — {map}**\nJoin the voice room at the"
            " meeting time: the final civilizations are shown there."
        ),
        "sab_over": "🕊️ The sabotage phase is over.",
        "sab_empty": "🕊️ No enemy civilization available.",
        "sab_not_player": "❌ Only the two players of the match may act here.",
        "sab_stock_line": (
            "🗡️ Sabotage: {sabotage} · 🦅 Faucon: {faucon}\nOne side can suffer at most 2 sabotaged civilizations."
        ),
        "sab_faucon_ready": "ready",
        "sab_faucon_flying": "flying until {time}",
        "sab_faucon_absent": "absent",
        "sab_panel_title": "🪶 **Sabotage** — {used}/2 civilizations removed in this attack",
        "sab_panel_used": "Removed: {list}",
        "sab_panel_none": "No civilization removed yet.",
        "sab_panel_done": "🪶 **{civ}** removed from the enemy side — {used}/2.",
        "sab_again_ask": "🗡️ Do you want to sabotage another civilization?",
        "sab_again_yes": "Yes — another sabotage",
        "sab_again_no": "No — I'm done",
        "room_ping_winner": "🏆 Winner: **{winner}**",
        "ready_button": "✅ Ready — confirm your presence",
        "ready_done": "✅ Presence confirmed — join the battle room at the meeting time.",
        "room_role": "Battle {map}",
        "room_scoreboard_map": "⚔️ {map}",
        "room_scoreboard_id": "🆔 Game ID: {id}",
        "room_side_attacker": "Attacker",
        "room_side_defender": "Defender",
        "room_side_ready": "✅ Ready",
        "room_side_wait": "⏳ waiting for Ready",
        "room_side_none": "🔗 waiting for the game ID",
        "room_side_ai": "🤖 AI — lvl {level}/5",
        "room_ai_disclaimer": (
            "⚠️ **Level {level}/5 AI** — the game is played in multiplayer"
            " with an ID (never local) to stay traceable and verifiable"
            " post-game. The player is taken at their word; any suspected"
            " fraud can be contested ⚖️ then arbitrated by the admins."
        ),
        "room_id_button": "🔗 Enter the game ID",
        "room_id_modify_button": "🔴 Change the game ID",
        "room_id_pending_button": "⏳ Game ID being entered…",
        "room_ready_attacker_button": "⚔️ Ready — {player}",
        "room_ready_defender_button": "Ready 🛡️ — {player}",
        "room_not_your_side": ("⛔ This Ready button belongs to your adversary."),
        "room_scoreboard_footer": ("🎮 Both players are ready — good luck! Declare the winner with 🏆."),
        "room_ping_message": ("⚔️ {attacker} {defender} — the battle room is open, to arms!"),
        "room_ping_attacker_only": ("⚔️ {attacker} — the battle room is open, to arms!"),
        "room_dm": (
            "🏟️ **Stand ready — {map}**\n"
            "Join your adversary in the voice channel: {room}\n"
            "Join the text salon to carry on: {salon}"
        ),
        "room_dm_deadline": (
            "\n\n⏳ Presence required (voice or ✅ Ready) before {deadline} —"
            " past that line, a no-show forfeits."
        ),
        "room_dm_luck": "\n\nGood luck! 🍀",
        "result_button": "🏆 Declare the winner",
        "draft_button": "📜 View the draft",
        "draft_sheet": "📜 Draft — {map}",
        "draft_sheet_help": (
            "The players' pick order during the draft (random order — the"
            " ranking stays secret), sabotaged picks struck through:"
        ),
        "result_select_title": "🏆 **Who wins the game?**",
        "result_select_placeholder": "The game's winner",
        "result_confirm_title": "🏆 **Winner: {winner}** — confirm this result?",
        "result_confirm_button": "✅ Confirm",
        "result_edit_button": "✏️ Change",
        "result_review_title": "⏳ **{player} declared {winner} the winner.**",
        "result_review_confirm_button": "✅ Confirm the result",
        "result_review_contest_button": "⚖️ Contest",
        "score_url_posted": "🔗 **Game URL**: {url}",
        "score_pending": (
            "⏳ Result recorded (game {game}) — it applies unless the other"
            " player contests it within the next {minutes} minutes."
        ),
        "forfeit_both": (
            "🏳️ General forfeit on **{map}** — nobody validated: the attacker, present, wins (**{kingdom}**)."
        ),
        "forfeit_side": "🏳️ Forfeit on **{map}** — **{side}** missed the validation: victory of **{kingdom}**.",
        "side_attacker": "the attacker",
        "side_defender": "the defender",
        "score_url_button": "🔗 Game ID",
        "score_url_modal_title": "Game URL",
        "score_url_modal_label": "Game lobby URL",
        "score_url_modal_placeholder": "ex. 123456",
        "score_reminder_dm": (
            "⏰ **Meeting — {map}**: no result declared for 3 hours — declare the"
            " game result with the 🏆 button of the battle room!"
        ),
        "room_ready_button": "✅ Ready",
        "room_start": ("🎮 **The game starts — {map}**!\n{mentions} — meet in the voice channel and good luck! 🍀"),
        "room_event_name": "⚔️ {map} — {attacker} 🆚 {defender}",
        "room_event_description": (
            "⚔️ Live Kingdoms battle — {attacker} 🆚 {defender}.\n"
            "Civilizations and game buttons: in the battle's voice channel."
        ),
        "room_ready_needs_url": ("🔗 Enter the game ID first (🔗 button) — the ✅ Ready button appears afterwards."),
        "result_not_ready": ("⏳ The Declare-the-winner button appears once both players are Ready."),
        "conquest_title": "⚔️ Kingdom **{winner}** conquered **{map}**!",
        "conquest_defense_title": "🛡️ Kingdom **{winner}** defended **{map}**!",
        "conquest_thread": "Conquest details",
        "conquest_thread_defense": "Defense details",
        "conquest_attacker_line": "⚔️ Attacker: **{player}** ({kingdom})",
        "conquest_defense_line": "🛡️ Defense: **{player}** ({kingdom})",
        "conquest_ai_defense": "🛡️ Defense: the kingdom **{kingdom}**'s AI (level {level})",
        "conquest_civ_line": "🎯 {player} played **{civ}**",
        "conquest_embuscade_line": "🥷 Ambush used",
        "conquest_sabotages_line": "🧨 Sabotages ({side}, Falcon/Tech): {civs}",
        "conquest_id_line": "🔗 Game ID: **{game}**",
        "conquest_none": "none",
        "conquest_side_attacker": "attacker",
        "conquest_side_defender": "defender",
        "conquest_married_lost": ("💔 **{player}** loses their sacred bond with civilization **{civ}**."),
        "score_resolved": ("🏆 Result recorded — **{winner}** wins **{map}** (game {game})."),
        "score_already": "✅ This result was already recorded — nothing to do.",
        "score_disputed": (
            "⚖️ Contradictory declarations — the admins arbitrate (game {game}).\nPlease get in touch with an admin."
        ),
        "noshow_side": ("🏳️ **{map}** — **{side}** missed the meeting: victory of **{kingdom}**."),
        "noshow_both": ("🏳️ **{map}** — double forfeit: no player at the meeting, the territory stays with its owner."),
        "dispute_admin_dm": ("⚖️ **Dispute — {map}**\n{declarations}\nCut the result (game {game})."),
        "auto_dispute": (
            "⚖️ **Dispute auto-opened on {map}** — no result declared 15h after the"
            " game opened: an admin arbitrates through the console (`resultat`)."
        ),
        "dispute_win_attacker": "⚖️ Rule: attacker",
        "dispute_win_defender": "⚖️ Rule: defender",
        "dispute_double": "🏳️ Double forfeit",
        "dispute_applied": "⚖️ Arbitration applied — **{kingdom}** wins **{map}**.",
        "dispute_forfeit": "⚖️ Arbitration applied — double forfeit on **{map}**.",
        "dispute_not_admin": "❌ Only bot-admins arbitrate a dispute.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _STRINGS["fr"] if str(locale).lower().startswith("fr") else _STRINGS["en"]


def _phase_display(token: str, strings: dict[str, str]) -> str:
    """Render one engine phase token as its player-facing label."""
    return strings.get(f"phase_{token}", token.capitalize())


def _locale(interaction: discord.Interaction) -> str:
    return str(interaction.locale) if interaction.locale else "fr"


def _fmt_paris(moment: Any) -> str:
    """Format a moment in Paris time (the players' clock, D77)."""
    when = moment if isinstance(moment, datetime) else datetime.fromtimestamp(moment, tz=_PARIS)
    return when.astimezone(_PARIS).strftime("%d/%m %H:%M")


DECLARE_CHANNEL_NAME = "Déclarer une attaque"
GEOPOLITICS_CHANNEL_NAME = "Géopolitique"


def _declare_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    """Return the guild's « Déclarer une attaque » salon (None when absent).

    Patch restructuration-royaume: the legacy « Attaquer » salon is the
    declared fallback — the provisioning adopts it by key and
    ``deploy_attack_panel`` renames it in place.
    """
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if guild is None:
        return None
    wanted = _slug(DECLARE_CHANNEL_NAME)
    channels = [c for c in getattr(guild, "text_channels", []) if _slug(getattr(c, "name", "")) in (wanted, "attaquer")]
    return next((c for c in channels if _slug(c.name) == wanted), channels[0] if channels else None)


def _battlefield_category(guild: Any) -> Any:
    """Return the provisioned « Champs de Bataille » category (never create).

    battle-room-categorie-v1 (Drasah 19/10): the battle room used to look
    its category up by an EXACT match on the hardcoded « Champs de
    bataille » — never equal to the provisioned « Champs de Bataille »
    (capital B) — so it CREATED a duplicate category at the first battle.
    The category now resolves through the declare salon's own category
    (both live in the battlefield group), falling back to a slug match.
    Nothing is ever created here: a missing category means a warning and
    a room at the guild root.
    """
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if guild is None:
        return None
    declare = _declare_channel(guild)
    category = getattr(declare, "category", None) if declare is not None else None
    if category is not None:
        return category
    wanted = {"champs-de-bataille", "battlefields"}
    return next(
        (item for item in getattr(guild, "categories", []) if _slug(str(getattr(item, "name", ""))) in wanted),
        None,
    )


def _attack_salon(guild: Any, attack: Any) -> Any:
    """Return the attack's OWN salon (restructuration-serveur-v1 b).

    Every declared attack opens a dedicated salon right under the
    declare salon — the ref rides ``score["attack_channel_id"]`` so
    the reveal, the forfeits and the meeting message all route there
    without any name lookup.
    """
    channel_id = int((dict(getattr(attack, "score", None) or {})).get("attack_channel_id") or 0)
    if guild is None or not channel_id:
        return None
    return getattr(guild, "get_channel", lambda _id: None)(channel_id)


async def _attack_salon_send_safe(guild: Any, attack: Any, content: str) -> Any:
    """Best-effort announcement in the attack's own salon.

    salon-nettoyage-v1 (Drasah 29/10): returns the SENT message (None
    on any hiccup) so the caller can record its ref — the reveal notes
    are swept when the battle room's scoreboard replaces them.
    """
    channel = _attack_salon(guild, attack)
    if channel is None:
        return None
    try:
        return await channel.send(content)
    except Exception:
        logger.warning("KINGDOM ATTACK: attack salon announcement failed", exc_info=True)
        return None


def _geopolitics_channel(guild: Any) -> Any:
    """Return the guild's « Géopolitique » recap salon (None when absent).

    Drasah 18/10 v5: the conquest recaps land there.
    permissions-serveur-v1: the salon's name now carries the current
    age emoji (« 🗺️ Géopolitique 🌑 ») — the match is on the stable
    « geopolitique » part of the slug, never the full name.
    """
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if guild is None:
        return None
    return next(
        (c for c in getattr(guild, "text_channels", []) if "geopolitique" in _slug(getattr(c, "name", ""))),
        None,
    )


async def _lord_name(kingdoms_service: Any, lord_id: str | None) -> str:
    """Resolve a lord display name (the raw id as fallback)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    if not lord_id:
        return "—"
    lord = await _lord_of(kingdoms_service, lord_id)
    if lord is None:
        return lord_id
    return str(getattr(lord, "display_name", None) or getattr(lord, "name", None) or lord_id)


async def _kingdom_name(kingdoms_service: Any, kingdom_id: str | None) -> str:
    """Resolve a kingdom display name (the raw id as fallback)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _kingdom_by_id_safe

    kingdom = await _kingdom_by_id_safe(kingdoms_service, kingdom_id or "")
    if kingdom is None:
        return str(kingdom_id or "—")
    return str(getattr(kingdom, "name", None) or kingdom_id)


async def _lord_kingdom_name(kingdoms_service: Any, lord_id: str | None) -> str:
    """Resolve a lord's kingdom display name (empty on any hiccup).

    bluff-externe-v1 (Drasah 29/10): the cover paints the COVER lord's
    kingdom — on any resolution failure the surface degrades to the
    neutral fallback, NEVER to the real attacker's kingdom.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    if not lord_id or kingdoms_service is None:
        return ""
    try:
        lord = await _lord_of(kingdoms_service, lord_id)
        if lord is None or not getattr(lord, "kingdom_id", None):
            return ""
        return await _kingdom_name(kingdoms_service, lord.kingdom_id)
    except Exception:
        return ""


def _declare_view(strings: dict[str, str]) -> discord.ui.View:
    """Return the CLEAN declare panel view: declare + history buttons.

    Drasah 17/10: « Déclarer une attaque » stays clean — the ongoing
    attacks (and their defend/join buttons) live in their OWN salons
    (restructuration-serveur-v1 b). The 📜 Historique button shows the
    resolved confrontations (ephemeral, paginated).
    """
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomAttackDeclareButton(strings["declare_button"]))
    view.add_item(KingdomAttackHistoryButton(strings["history_button"]))
    view.add_item(KingdomPlanButton(strings["plan_button"]))
    view.add_item(KingdomAttackInfoButton(strings["attack_info_button"]))
    return view


def _attack_salon_view(strings: dict[str, str], attack: Any) -> discord.ui.View:
    """Return the base-message view of an attack's own salon (b).

    Player attack: 🛡️ Défendre (NEVER disappears — the surprise effect
    rides the ANSWER, not the button) + ❌ Annuler (declarant only).
    Gaïa free-for-all: 🦅 Rejoindre + ❌ Annuler (the service refuses
    a Gaïa cancellation — axis ouvert, default followed).
    """
    from kingdoms.mods.kingdoms.models import AttackKind

    view = discord.ui.View(timeout=None)
    if getattr(attack, "kind", None) is AttackKind.GAIA:
        view.add_item(KingdomAttackJoinButton(str(attack.id), strings["gaia_join_button"]))
    else:
        view.add_item(KingdomAttackDefendButton(str(attack.id), strings["defend_button"]))
    view.add_item(KingdomAttackCancelButton(str(attack.id), strings["cancel_attack_button"]))
    return view


def declare_panel_text(strings: dict[str, str]) -> str:
    """Render the clean declare panel message (title, help, pointer, marker)."""
    return "\n".join(
        (
            strings["panel_title"],
            "",
            strings["panel_help"],
            "",
            strings["panel_pointer"],
            ATTACK_PANEL_MARKER,
        )
    )


async def deploy_attack_panel(
    channel: discord.TextChannel | None,
    locale: str,
    kingdoms_service: Any = None,
    attacks_service: Any = None,
) -> None:
    """Pin (or refresh) the CLEAN declare panel in the Déclarer une attaque salon.

    Drasah 17/10: this salon keeps ONLY the declare flow. Since
    restructuration-serveur-v1 b every declared attack owns its salon
    (created right under this one). The service params stay for API
    compatibility with the existing call sites.
    """
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if channel is None:
        return
    try:
        if _slug(getattr(channel, "name", "")) != _slug(DECLARE_CHANNEL_NAME):
            await channel.edit(
                name=DECLARE_CHANNEL_NAME,
                reason="kingdoms: rename legacy Attaquer salon (restructuration-royaume)",
            )
    except Exception:
        logger.warning("KINGDOM ATTACK: legacy salon rename failed", exc_info=True)
    strings = _strings(locale)
    content = declare_panel_text(strings)
    view = _declare_view(strings)
    # attaques-panel-spam (Drasah 15/10): the panel is EDITED in place —
    # delete+send on every call multiplied messages whenever the old
    # message delete failed (rate limit, out of the history scan depth).
    # The scan reads deep (200) so a rate-limited delete of the previous
    # cycle cannot let two panels coexist.
    panel_message = await _find_panel_message(channel)
    if panel_message is not None:
        if (panel_message.content or "") == content:
            await _purge_declare_channel_safe(channel, panel_message)
            return  # unchanged — nothing to touch (no API call at all)
        try:
            await panel_message.edit(content=content, view=view)
            await _purge_declare_channel_safe(channel, panel_message)
            return
        except Exception:
            logger.warning("KINGDOM ATTACK: panel edit failed", exc_info=True)
    try:
        await channel.send(content=content, view=view)
    except Exception:
        logger.warning("KINGDOM ATTACK: panel send failed", exc_info=True)
        return
    panel_message = await _find_panel_message(channel)
    await _purge_declare_channel_safe(channel, panel_message)


async def _purge_declare_channel_safe(channel: Any, keep: Any) -> None:
    """Delete every other bot message of the declare salon (Drasah 18/10).

    The salon must stay clean: only the pinned panel lives there. The
    ephemeral flow answers cannot be deleted (they expire), but every
    stale NON-ephemeral bot message (old panels, old flows, error
    leftovers) is swept on each deploy.
    """
    try:
        async for message in channel.history(limit=100):
            if message.id == getattr(keep, "id", None):
                continue
            author = getattr(message, "author", None)
            if author is None or not getattr(author, "bot", False):
                continue
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM ATTACK: declare salon purge failed", exc_info=True)
    except Exception:
        logger.warning("KINGDOM ATTACK: declare salon sweep failed", exc_info=True)


async def _find_panel_message(channel: discord.TextChannel, marker: str = ATTACK_PANEL_MARKER) -> Any | None:
    """Return the live panel message of the salon, pruning the duplicates.

    attaques-panel-spam: the scan reads deep (200) so a rate-limited
    delete of a previous cycle cannot let two panels coexist — the
    first marker wins, the others are removed.
    """
    panel_message: Any = None
    try:
        async for message in channel.history(limit=200):
            if marker not in (message.content or ""):
                continue
            if panel_message is None:
                panel_message = message
                continue
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM ATTACK: duplicate panel removal failed", exc_info=True)
    except Exception:
        logger.warning("KINGDOM ATTACK: panel history scan failed", exc_info=True)
    return panel_message


async def refresh_attack_panel_safe(guild: discord.Guild | None, locale: str) -> None:
    """Re-pin the guild's declare panel after a mutation (b).

    restructuration-serveur-v1 b: no more Attaques board — each attack
    owns its salon, refreshed through its stored refs instead.
    """
    await deploy_attack_panel(_declare_channel(guild), locale)


# ----------------------------------------------------------------------
# The persistent declare button
# ----------------------------------------------------------------------
class KingdomAttackDeclareButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:declare",
):
    """The restart-proof declare button of the pinned attack panel."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:attack:declare",
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAttackDeclareButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["declare_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard the click, then open the GUIDED declaration flow.

        Drasah's recâblage (2026-10-14): the bare modal is replaced by
        a guided ephemeral flow — (mode?) kingdom → territory →
        (cover?) → lobby — so the attacker SEES the defense AI level,
        the patrol shields and the cadastre of every option before
        choosing.

        embuscade-v1 (Drasah 27/10): with bought Embuscade cards the
        flow opens on the MODE view first (classic itinerary vs the
        exceptional fixed 1h Embuscade); zero cards and the mode view
        is not offered at all.
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _lord_of, _open_unique_ephemeral, _wiring
        )

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await _open_unique_ephemeral(interaction, strings["no_service"])
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None:
            await _open_unique_ephemeral(interaction, strings["not_a_lord"])
            return
        own_kingdom_id = str(getattr(lord, "kingdom_id", "") or "")
        # embuscade-v1 (Drasah 27/10): with bought Embuscade cards in
        # stock, the flow opens on a MODE view first — the classic
        # itinerary vs the exceptional fixed 1h Embuscade. Zero stock
        # and the mode view is NOT offered (« cette vue n'est pas
        # proposée si 0 embuscade »): the kingdom step opens as before.
        mode_content, mode_view = await _attack_mode_select_view(
            wiring, own_kingdom_id, strings, interaction
        )
        if mode_view is not None:
            await _open_unique_ephemeral(interaction, mode_content, view=mode_view)
            return
        content, view = await _kingdom_select_view(wiring, own_kingdom_id, strings, interaction)
        if view is None:
            await _open_unique_ephemeral(interaction, strings["no_target"])
            return
        await _open_unique_ephemeral(interaction, content, view=view)


def _fmt_paris_full(moment: Any, strings: dict[str, str]) -> str:
    """Format a moment as « Vendredi 29/08/2025 à 19h15 » (the players' clock)."""
    when = moment if isinstance(moment, datetime) else datetime.fromtimestamp(moment, tz=_PARIS)
    local = when.astimezone(_PARIS)
    weekdays = str(strings.get("weekdays", "")).split(",")
    weekday = weekdays[local.weekday()] if len(weekdays) == 7 else local.strftime("%A")
    return str(strings["date_full"]).format(
        weekday=weekday,
        day=f"{local.day:02d}",
        month=f"{local.month:02d}",
        year=local.year,
        hour=f"{local.hour:02d}",
        minute=f"{local.minute:02d}",
    )


def _kingdom_mention(guild: Any, name: str) -> str:
    """Mention the kingdom's Discord role when it exists (bold fallback)."""
    roles = list(getattr(guild, "roles", []) or [])
    role = next((r for r in roles if getattr(r, "name", "") == name), None)
    if role is not None:
        return str(getattr(role, "mention", f"**{name}**"))
    return f"**{name}**"


_MAP_TAG_KEYS: tuple[tuple[str, str], ...] = (
    # The 12 Drasah cadastre tags (cadastre-diplomatie-v1, 15/10).
    ("eau", "💧"),
    ("hybride", "🌊"),
    ("objectif", "🎯"),
    ("nomade", "🌾"),
    ("foret", "🌲"),
    ("montagne", "⛰️"),
    ("desert", "🏜️"),
    ("marais", "🪷"),
    ("ouverte", "🌞"),
    ("chaotique", "🌀"),
    ("mur_depart", "🧱"),
    ("ferme", "🔒"),
)


def _map_description(entry: Any, strings: dict[str, str]) -> str:
    """One select-option description: the map's tags + cadastre bonus."""
    types = getattr(entry, "types", None)
    tags = [icon for key, icon in _MAP_TAG_KEYS if bool(getattr(types, key, False))]
    bonus = str((getattr(entry, "cadastre", None) or {}).get("bonus", "") or "")
    parts = [" ".join(tags)] if tags else []
    if bonus:
        parts.append(bonus)
    return " · ".join(parts)[:100]


def _kingdom_option(kingdom: Any, level: int, count: int, strings: dict[str, str]) -> discord.SelectOption:
    """One step-1 option: the kingdom, its defence AI level and map count."""
    import discord

    return discord.SelectOption(
        label=str(kingdom.name)[:100],
        value=str(kingdom.id)[:100],
        description=strings["ai_level_desc"].format(level=level, count=count)[:100],
    )


async def _kingdom_step_options(
    wiring: Any,
    own_kingdom_id: str,
    strings: dict[str, str],
    now: Any,
    base: int,
) -> tuple[list[Any], list[str]]:
    """Classify every kingdom as attackable or patrol-protected (step 1)."""
    from kingdoms.mods.kingdoms.attacks import (
        patrouille_active_at,
        patrouille_reinforced,
        patrouille_slots,
    )

    kingdoms_service = wiring.kingdoms_service
    attacks_service = wiring.attacks_service
    territories_service = wiring.territories_service
    territories = await territories_service.territories() if territories_service is not None else []
    options: list[Any] = []
    protected: list[str] = []
    for kingdom in await kingdoms_service.kingdoms():
        if str(kingdom.id) == own_kingdom_id:
            continue
        owned = [t for t in territories if t.owner_kingdom_id == kingdom.id]
        if not owned:
            continue
        if getattr(kingdom, "is_gaia", False):
            # Gaïa territories are attackable through the free-for-all
            # (Drasah 14/10): the epoch's AI defends, any other lord
            # may then join the battle via the panel's Join button.
            options.append(_kingdom_option(kingdom, base, len(owned), strings))
            continue
        state = await attacks_service.technology_state(str(kingdom.id))
        if patrouille_active_at(state, now, base_level=base):
            slots = patrouille_slots(state)
            span = 3 if patrouille_reinforced(state, base) else 2
            end = (slots[-1] + span) % 24 if slots else 0
            protected.append(strings["patrol_kingdom"].format(name=str(kingdom.name), end=f"{end:02d}"))
            continue
        level = await attacks_service.defense_ai_level(str(kingdom.id))
        options.append(_kingdom_option(kingdom, level, len(owned), strings))
    return options, protected


async def _kingdom_select_view(
    wiring: Any,
    own_kingdom_id: str,
    strings: dict[str, str],
    origin: Any = None,
    mode: str = "classic",
) -> tuple[str, discord.ui.View | None]:
    """Step 1 — the attackable kingdoms (patrol-shielded ones stay visible).

    embuscade-v1 (Drasah 27/10): in ``embuscade`` mode this same kingdom
    step rides the ambush flow — the armed line under the select says
    so — and the territory step that follows is the ambush's own.
    """
    import discord

    kingdoms_service = wiring.kingdoms_service
    attacks_service = wiring.attacks_service
    if kingdoms_service is None or attacks_service is None:
        return "", None
    now = game_now()
    base = await attacks_service.base_ai_level()
    options, protected = await _kingdom_step_options(wiring, own_kingdom_id, strings, now, base)
    if not options:
        return "", None
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:attack:choose:kingdom",
        placeholder=str(strings["choose_kingdom"])[:100],
        options=options[:25],
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        content, view = await _territory_select_view(
            wiring, own_kingdom_id, chosen[0], strings, origin, mode=mode
        )
        if view is None:
            await target.response.edit_message(content=strings["no_target"], view=None)
            return
        await target.response.edit_message(content=content, view=view)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    lines = [str(strings["choose_kingdom"])]
    if mode == "embuscade":
        lines.append(
            str(strings["embuscade_armed_line"]).format(
                count=await _embuscade_stock(wiring, own_kingdom_id),
            )
        )
    if protected:
        lines.append(str(strings["protected_line"]).format(kingdoms=" · ".join(protected)))
    lines.append(str(strings["traquenard_hint"]))
    return "\n".join(lines), view


async def _territory_select_view(
    wiring: Any,
    own_kingdom_id: str,
    kingdom_id: str,
    strings: dict[str, str],
    origin: Any = None,
    mode: str = "classic",
) -> tuple[str, discord.ui.View | None]:
    """Step 2 — the territories of the chosen kingdom (tags + cadastre).

    embuscade-v1 (Drasah 27/10): in ``embuscade`` mode the select is
    the ambush's OWN (it declares directly — no cover question, a
    bought card is consumed, fixed itinerary).
    """
    import discord

    kingdoms_service = wiring.kingdoms_service
    territories_service = wiring.territories_service
    if kingdoms_service is None or territories_service is None:
        return "", None
    kingdom = next((k for k in await kingdoms_service.kingdoms() if str(k.id) == kingdom_id), None)
    if kingdom is None:
        return "", None
    catalog = {
        str(getattr(entry, "key", "")): entry
        for entry in getattr(getattr(kingdoms_service, "config", None), "maps", None) or ()
    }
    owned = [t for t in await territories_service.territories() if t.owner_kingdom_id == kingdom_id]
    if not owned:
        return "", None
    options = [
        discord.SelectOption(
            label=str(getattr(catalog.get(str(t.map_key), None), "display_name", None) or t.map_key)[:100],
            value=str(t.map_key)[:100],
            description=_map_description(catalog.get(str(t.map_key), None), strings),
        )
        for t in owned
    ][:25]
    embuscade_mode = mode == "embuscade"
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=(
            "kingdoms:attack:embuscade:territory"
            if embuscade_mode
            else "kingdoms:attack:choose:territory"
        ),
        placeholder=str(strings["embuscade_choose" if embuscade_mode else "choose_territory"])[:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        # embuscade-v1 (Drasah 27/10): the ambush declares DIRECTLY
        # here — no cover question (it strikes in the open), a bought
        # card is consumed by the service and the fixed itinerary
        # note follows. The selector ephemeral dies instantly, like
        # the classic flow.
        if embuscade_mode:
            if origin is not None:
                try:
                    await origin.delete_original_response()
                except Exception:
                    logger.debug("kingdoms: selector ephemeral delete failed", exc_info=True)
            await _declare_embuscade_attack(wiring, target, strings, chosen[0])
            return
        # battle-ai-rdv-v1 (Drasah 22/10): the map is chosen — the
        # selector ephemeral dies INSTANTLY (no stale wizard left
        # behind after the declaration; the cover question and the
        # itinerary note speak for themselves).
        if origin is not None:
            try:
                await origin.delete_original_response()
            except Exception:
                logger.debug("kingdoms: selector ephemeral delete failed", exc_info=True)
        await _open_cover_step(
            wiring,
            target,
            strings,
            chosen[0],
            own_kingdom_id,
            kingdom_id,
            is_gaia=bool(getattr(kingdom, "is_gaia", False)),
        )

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    # embuscade-v1 (Drasah 27/10): the step-2 Embuscade button is gone
    # — the ambush is now chosen on the flow's MODE view (step 0) and
    # rides this same kingdom → territory flow in embuscade mode.
    # restructuration-serveur-v1 b: the « ⚔️ Itinéraire » line reads the
    # delay LIVE from the config (player vs Gaïa target — never hardcoded,
    # the beta reconfigures and the Corbeau 24h follow-up overrides it
    # per-lord at the declaration itself).
    delay_minutes = _declare_delay_minutes(wiring, kingdom)
    if embuscade_mode and wiring.attacks_service is not None:
        delay_minutes = int(wiring.attacks_service.embuscade_delay_minutes)
    lines = [
        str(strings["embuscade_choose"])
        if embuscade_mode
        else str(strings["choose_territory"]).format(kingdom=f"**{kingdom.name}**")
    ]
    if delay_minutes:
        lines.append(str(strings["itinerary_line"]).format(minutes=delay_minutes))
    lines.append(str(strings["traquenard_hint"]))
    return "\n".join(lines), view


async def _embuscade_stock(wiring: Any, own_kingdom_id: str) -> int:
    """Return the kingdom's bought Embuscade cards (0 on any wiring hiccup)."""
    if wiring.attacks_service is None:
        return 0
    try:
        return int(
            (await wiring.attacks_service.technology_state(own_kingdom_id)).purchases.get(
                "embuscade", 0
            )
        )
    except Exception:
        return 0


def _fmt_delay(minutes: int) -> str:
    """Format an itinerary as « 1h », « 1h30 » or « 45 min » (embuscade-v1)."""
    if minutes <= 0:
        return "0 min"
    hours, rest = divmod(int(minutes), 60)
    if hours == 0:
        return f"{rest} min"
    if rest == 0:
        return f"{hours}h"
    return f"{hours}h{rest:02d}"


async def _attack_mode_select_view(
    wiring: Any,
    own_kingdom_id: str,
    strings: dict[str, str],
    origin: Any = None,
) -> tuple[str, discord.ui.View | None]:
    """Step 0 — the itinerary choice (embuscade-v1, Drasah 27/10).

    Drasah's ruling: when the kingdom holds bought Embuscade cards,
    « Déclarer une attaque » opens on a MODE view first — the classic
    itinerary against the exceptional fixed 1h Embuscade — and the
    ambush then rides its own kingdom → territory flow (no cover
    question: it strikes in the open). With ZERO cards the mode view
    is NOT offered (« cette vue n'est pas proposée si 0 embuscade »):
    the flow starts at the kingdom step exactly as before.
    """
    if wiring.attacks_service is None:
        return "", None
    if await _embuscade_stock(wiring, own_kingdom_id) <= 0:
        return "", None
    classic: discord.ui.Button[Any] = discord.ui.Button(
        label=str(strings["mode_classic_button"]).format(
            delay=_fmt_delay(int(wiring.attacks_service.player_attack_delay_minutes))
        )[:80],
        style=discord.ButtonStyle.primary,
        custom_id="kingdoms:attack:mode:classic",
    )

    async def on_classic(target: discord.Interaction[Any]) -> None:
        content, view = await _kingdom_select_view(
            wiring, own_kingdom_id, strings, origin, mode="classic"
        )
        if view is None:
            await target.response.edit_message(content=strings["no_target"], view=None)
            return
        await target.response.edit_message(content=content, view=view)

    classic.callback = on_classic  # type: ignore[method-assign, assignment]
    embuscade: discord.ui.Button[Any] = discord.ui.Button(
        label=str(strings["mode_embuscade_button"]).format(
            delay=_fmt_delay(int(wiring.attacks_service.embuscade_delay_minutes))
        )[:80],
        style=discord.ButtonStyle.danger,
        custom_id="kingdoms:attack:mode:embuscade",
    )

    async def on_embuscade(target: discord.Interaction[Any]) -> None:
        content, view = await _kingdom_select_view(
            wiring, own_kingdom_id, strings, origin, mode="embuscade"
        )
        if view is None:
            await target.response.edit_message(content=strings["no_target"], view=None)
            return
        await target.response.edit_message(content=content, view=view)

    embuscade.callback = on_embuscade  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(classic)
    view.add_item(embuscade)
    lines = [str(strings["mode_choose"]), str(strings["traquenard_hint"])]
    return "\n".join(lines), view


async def _declare_embuscade_attack(
    wiring: Any,
    target: discord.Interaction[Any],
    strings: dict[str, str],
    map_key: str,
) -> None:
    """Declare the exceptional Embuscade at the mode flow's step 2 (embuscade-v1).

    The former step-2 button's declare leg, extracted: the service
    consumes the bought card, the fixed itinerary note follows, the
    battle salon opens and the panel refreshes. The caller deletes
    the original wizard (the selector ephemeral dies instantly).
    """
    attacks = wiring.attacks_service
    if attacks is None:
        await target.response.send_message(strings["no_service"], ephemeral=True)
        return
    await target.response.defer(ephemeral=True)
    try:
        attack = await attacks.declare_embuscade(str(target.user.id), map_key, "")
    except Exception as exc:
        await target.followup.send(_combat_failure_note(exc, strings), ephemeral=True)
        return
    try:
        delay_minutes = int((attack.expires_at - attack.declared_at).total_seconds() // 60)
    except Exception:
        delay_minutes = 0
    note: Any = await _followup_send_ephemeral(
        target,
        strings["embuscade_note"].format(
            map=attack.map_key,
            delay=delay_minutes,
            time=_fmt_paris(attack.expires_at),
        ),
    )
    _arm_declared_note_timer(
        str(attack.id), target, int(getattr(note, "id", 0) or 0), attack.expires_at
    )
    await _open_attack_salon_safe(target.guild, wiring, strings, attack)
    await refresh_attack_panel_safe(target.guild, _locale(target))


def _declare_delay_minutes(wiring: Any, kingdom: Any) -> int:
    """Return the LIVE declaration delay of this target (battle-ai-rdv-v1)."""
    delay_minutes = 0
    try:
        if bool(getattr(kingdom, "is_gaia", False)) and wiring.attacks_service is not None:
            delay_minutes = int(wiring.attacks_service.gaia_attack_delay_minutes)
        elif wiring.attacks_service is not None:
            delay_minutes = int(wiring.attacks_service.player_attack_delay_minutes)
    except Exception:
        delay_minutes = 0
    return delay_minutes


async def _followup_send_ephemeral(interaction: Any, content: str) -> Any:
    """Send an ephemeral followup and return its message (battle-ai-rdv-v1).

    discord.py returns the created message; some type builds type the
    send as None — the callers only read ``id`` best-effort.
    """
    return await interaction.followup.send(content, ephemeral=True)


async def _declare_player_attack(
    wiring: Any,
    target: discord.Interaction[Any],
    strings: dict[str, str],
    map_key: str,
    cover_lord_id: str | None,
) -> None:
    """Declare a player attack at step 3 — no modal anymore.

    Live beta 18/10: the lobby URL field was removed from the
    declaration (it moved to the RDV DM's 🔗 button) and the modal
    was left with ZERO component — Discord refuses an empty modal
    (400 Bad Request), the callback died and the attacker saw
    « Kingdoms n'a pas répondu à temps ». A player attack needs no
    extra input anymore: declare directly, then confirm.
    """
    attacks = wiring.attacks_service
    if attacks is None:
        await target.response.send_message(strings["no_service"], ephemeral=True)
        return
    await target.response.defer(ephemeral=True)
    try:
        attack = await attacks.declare_attack(str(target.user.id), map_key, "", cover_lord_id=cover_lord_id)
    except Exception as exc:
        await target.followup.send(_combat_failure_note(exc, strings), ephemeral=True)
        return
    delay_minutes = 0
    try:
        delay_minutes = int((attack.expires_at - attack.declared_at).total_seconds() // 60)
    except Exception:
        delay_minutes = 0
    note: Any = await _followup_send_ephemeral(
        target,
        strings["itinerary_note"].format(
            map=attack.map_key,
            delay=delay_minutes,
            time=_fmt_paris(attack.expires_at),
        ),
    )
    # battle-ai-rdv-v1: the note dies at the MEETING time — it stops
    # being news once the battle room opens (best effort, the
    # followup token lives 15 minutes).
    _arm_declared_note_timer(
        str(attack.id),
        target,
        int(getattr(note, "id", 0) or 0),
        attack.expires_at,
    )
    await _open_attack_salon_safe(target.guild, wiring, strings, attack)
    await refresh_attack_panel_safe(target.guild, _locale(target))


async def _cover_candidates(wiring: Any, defender_kingdom_id: str, except_user_id: str) -> list[Any]:
    """Return the bluff candidates (bluff-externe-v1, Drasah 29/10).

    EVERY active lord of the season is eligible — the attacker's own
    kingdom (D77) AND the foreign kingdoms (the false flag adds
    tension) — EXCEPT the lords of the ATTACKED kingdom and the
    attacker himself. The service re-checks the rule at the
    declaration (_engage_cover).
    """
    if wiring.kingdoms_service is None:
        return []
    try:
        lords = await wiring.kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOM ATTACK: cover lord scan failed", exc_info=True)
        return []
    return [
        lord
        for lord in lords
        if getattr(lord, "kingdom_id", None)
        and not getattr(lord, "left", False)
        and str(lord.kingdom_id) != str(defender_kingdom_id)
        and str(lord.id) != str(except_user_id)
    ]


async def _open_cover_step(
    wiring: Any,
    interaction: discord.Interaction[Any],
    strings: dict[str, str],
    map_key: str,
    own_kingdom_id: str,
    defender_kingdom_id: str,
    *,
    is_gaia: bool = False,
) -> None:
    """Step 3 — the counter-espionage question (skipped without a charge).

    A Gaïa target skips the cover entirely: the free-for-all has no
    defender to bluff (Drasah 14/10 — Gaïa is attackable like any
    kingdom, the battle just accepts extra lords).

    bluff-externe-v1 (Drasah 29/10): the candidates are ALL active
    lords of the season — ANY kingdom, including foreign ones (the
    false flag adds tension) — EXCEPT the lords of the ATTACKED
    kingdom and the attacker himself. With no eligible candidate the
    attack declares OPENLY and SILENTLY: no note, no charge consumed
    (Drasah 5a — no information pollution on every attack).
    """
    import discord

    if is_gaia:
        await interaction.response.send_modal(KingdomAttackLobbyModal(strings, map_key, None, "", is_gaia=True))
        return
    attacks_service = wiring.attacks_service
    if attacks_service is None:
        await interaction.response.send_message(strings["no_service"], ephemeral=True)
        return
    state = await attacks_service.technology_state(own_kingdom_id)
    has_stock = state.purchases.get("contre_espionnage", 0) > 0
    candidates = await _cover_candidates(wiring, defender_kingdom_id, str(interaction.user.id))
    if not has_stock or not candidates:
        await _declare_player_attack(wiring, interaction, strings, map_key, None)
        return
    view = discord.ui.View(timeout=600)

    async def open_lobby(cover_lord_id: str | None, target: discord.Interaction[Any]) -> None:
        await _declare_player_attack(wiring, target, strings, map_key, cover_lord_id)

    async def _cover_options(target: discord.Interaction[Any]) -> list[discord.SelectOption[Any]]:
        """The eligible lords sorted by (kingdom, name) with the kingdom
        as the option description — the attacker sees WHO belongs to
        WHICH kingdom before painting the false flag."""
        lords = await _cover_candidates(wiring, defender_kingdom_id, str(target.user.id))
        kingdom_names: dict[str, str] = {}
        try:
            kingdom_names = {
                str(k.id): str(k.name) for k in await wiring.kingdoms_service.kingdoms()
            }
        except Exception:
            kingdom_names = {}
        entries = [
            (
                kingdom_names.get(str(getattr(lord, "kingdom_id", "")), ""),
                str(getattr(lord, "display_name", lord.id)),
                lord,
            )
            for lord in lords
        ]
        entries.sort(key=lambda entry: (entry[0].lower(), entry[1].lower()))
        return [
            discord.SelectOption(
                label=entry[1][:100],
                value=str(entry[2].id)[:100],
                description=entry[0][:100],
            )
            for entry in entries
        ][:25]

    async def on_yes(target: discord.Interaction[Any]) -> None:
        options = await _cover_options(target)
        if not options:
            # Nobody left to bluff (a departure since the question was
            # shown) — declare openly, SILENTLY (bluff-externe-v1 5a:
            # no note, no charge consumed).
            await open_lobby(None, target)
            return
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:attack:choose:cover",
            placeholder=str(strings["choose_cover"])[:100],
            options=options,
        )

        async def on_cover(chosen: discord.Interaction[Any]) -> None:
            values = getattr(chosen, "data", None) or {}
            picked = [str(v) for v in values.get("values", [])]
            await open_lobby(picked[0] if picked else None, chosen)

        select.callback = on_cover  # type: ignore[method-assign, assignment]
        cover_view = discord.ui.View(timeout=600)
        cover_view.add_item(select)
        await target.response.edit_message(content=str(strings["choose_cover"]), view=cover_view)

    async def on_no(target: discord.Interaction[Any]) -> None:
        await open_lobby(None, target)

    yes: discord.ui.Button[Any] = discord.ui.Button(
        label=str(strings["cover_yes_button"])[:80],
        style=discord.ButtonStyle.primary,
        custom_id="kingdoms:attack:cover:yes",
    )
    no: discord.ui.Button[Any] = discord.ui.Button(
        label=str(strings["cover_no_button"])[:80],
        style=discord.ButtonStyle.secondary,
        custom_id="kingdoms:attack:cover:no",
    )
    yes.callback = on_yes  # type: ignore[method-assign, assignment]
    no.callback = on_no  # type: ignore[method-assign, assignment]
    view.add_item(yes)
    view.add_item(no)
    # battle-ai-rdv-v1: the selector ephemeral is already gone (it
    # died at the map choice) — the cover question opens its OWN
    # ephemeral instead of editing the wizard message.
    await interaction.response.send_message(content=str(strings["cover_question"]), view=view, ephemeral=True)


def _attack_salon_overwrites(guild: Any) -> dict[Any, discord.PermissionOverwrite]:
    """Build the attack salon's overwrite set (permissions-serveur-v1).

    The attack salons are masked from @everyone like the rest of the
    mod — the player roles (granted at acceptance) carry the view.
    ⚠️ lesson Check 75: the set MUST carry an explicit guild.me entry
    (the bot is no admin) or it locks itself out of its own salon.
    """
    overwrites: dict[Any, discord.PermissionOverwrite] = {}
    everyone = getattr(guild, "default_role", None)
    if everyone is not None:
        overwrites[everyone] = discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            read_message_history=False,
        )
    for role_name in ("Kingdoms Lord", "Kingdoms King", "Kingdoms Admin"):
        role = discord.utils.get(getattr(guild, "roles", []), name=role_name)
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            )
    me = getattr(guild, "me", None)
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )
    return overwrites


async def _open_attack_salon_safe(guild: Any, wiring: Any, strings: dict[str, str], attack: Any) -> None:
    """Open the attack's OWN salon and pin its base message (b).

    restructuration-serveur-v1 b (Drasah 19/10): every declared attack
    gets a dedicated salon positioned right under « Déclarer une
    attaque ». Name: ``{joueur} attaque {map} de {royaume}`` — NEVER a
    « vs défenseur » (unknown at the declaration; the shown identity is
    the COVER lord when a cover is active, the real attacker otherwise).
    Visible to everyone, @everyone denied send — ⚠️ lesson Check 75:
    the overwrite set MUST carry an explicit guild.me entry (the bot is
    no admin). The base message carries 🛡️ Défendre (never disappears)
    and ❌ Annuler, plus the rights thread.
    """
    kingdoms_service = wiring.kingdoms_service
    declare = _declare_channel(guild)
    if declare is None:
        return
    shown_id = attack.cover_lord_id or attack.attacker_lord_id
    shown_name = await _lord_name(kingdoms_service, shown_id)
    defender_name = await _kingdom_name(kingdoms_service, attack.defender_kingdom_id)
    # bluff-externe-v1 (Drasah 29/10): the illusion wears the COVER
    # lord's KINGDOM too — a foreign cover paints the whole false
    # flag; the real attacker's kingdom stays hidden until the reveal
    # (an unresolvable cover degrades to the neutral fallback, never
    # to the real kingdom).
    if attack.cover_lord_id:
        attacker_kingdom_name = await _lord_kingdom_name(kingdoms_service, attack.cover_lord_id) or "—"
    else:
        attacker_kingdom_name = await _kingdom_name(kingdoms_service, attack.attacker_kingdom_id)
    # battle-room-id-v2 (Drasah 22/10): the generated name becomes a
    # readable ⚔️ duel — ``⚔️-drasah-vs-bourgogne`` (the attacker's
    # name VS the DEFENDER KINGDOM's name — the defender PLAYER is
    # unknown at the declaration). A numbered suffix separates
    # simultaneous attacks on the same names.
    name = _room_channel_name(guild, "🗡️", shown_name, defender_name)
    overwrites = _attack_salon_overwrites(guild)
    position = int(getattr(declare, "position", 0) or 0) + 1
    try:
        channel = await guild.create_text_channel(
            name,
            category=getattr(declare, "category", None),
            position=position,
            overwrites=overwrites,
        )
    except Exception:
        logger.warning("KINGDOM ATTACK: attack salon creation failed", exc_info=True)
        return
    await _record_attack_channel_safe(attack, int(channel.id))
    content = str(strings["announce_v1"]).format(
        attacker=f"<@{shown_id}>",
        kingdom=_kingdom_mention(guild, attacker_kingdom_name),
        defender=_kingdom_mention(guild, defender_name),
        date=_fmt_paris_full(attack.expires_at, strings),
    )
    if getattr(attack, "traquenard", False):
        content += str(strings["announce_v1_traquenard"])
    if str(getattr(attack, "kind", "")) == "gaia":
        gaia_max = int(getattr(wiring.attacks_service, "gaia_max_participants", 7) or 7)
        content += str(strings["announce_v1_gaia"]).format(max=gaia_max)
    try:
        message = await channel.send(content, view=_attack_salon_view(strings, attack))
        await _record_attack_base_message_safe(attack, int(message.id))
    except Exception:
        logger.warning("KINGDOM ATTACK: attack salon base message failed", exc_info=True)
        return
    # attaque-ergonomie-v1 (Drasah 28/10): no more declaration
    # THREAD — the « Déroulé de l'attaque & droits du défenseur »
    # sheet lives in the ⚔️ info button of the declare panel now
    # (ephemeral, single window). The thread read badly under the
    # announcement and nobody opened it.


async def _record_attack_channel_safe(attack: Any, channel_id: int) -> None:
    """Store the attack salon ref through the attacks service (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return
    try:
        await wiring.attacks_service.record_attack_channel(str(attack.id), int(channel_id))
    except Exception:
        logger.warning("KINGDOM ATTACK: attack salon ref not stored", exc_info=True)


async def _record_attack_base_message_safe(attack: Any, message_id: int) -> None:
    """Store the attack salon's base-message ref (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return
    try:
        await wiring.attacks_service.record_attack_base_message(str(attack.id), int(message_id))
    except Exception:
        logger.warning("KINGDOM ATTACK: attack salon base ref not stored", exc_info=True)


class KingdomAttackLobbyModal(discord.ui.Modal):
    """Final step — the game lobby URL (the territory is already chosen)."""

    def __init__(
        self,
        strings: dict[str, str],
        map_key: str,
        cover_lord_id: str | None,
        kingdom_name: str,
        *,
        is_gaia: bool = False,
    ) -> None:
        super().__init__(title=strings["modal_title"][:45])
        self.strings = strings
        self.map_key = map_key
        self.cover_lord_id = cover_lord_id
        self.kingdom_name = kingdom_name
        self.is_gaia = is_gaia
        # Drasah 18/10: the modal is GAÏA-ONLY now (a player attack
        # declares directly — an empty modal is rejected by Discord
        # and killed the whole declare flow, live beta 18/10). The
        # Gaïa free-for-all keeps the field: the first declarer's URL
        # is the meeting point of the battle.
        self.lobby_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            label=strings["modal_lobby"][:45], max_length=200, required=True
        )
        self.add_item(self.lobby_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Declare the attack (Gaïa free-for-all or player attack), confirm, announce."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = self.strings
        wiring = _wiring()
        attacks = wiring.attacks_service
        await interaction.response.defer(ephemeral=True)
        try:
            attack = await attacks.declare_gaia_attack(
                str(interaction.user.id),
                self.map_key,
                (self.lobby_input.value if self.lobby_input else "").strip(),
            )
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(strings["failed"].format(message=message), ephemeral=True)
            return
        note: Any = await _followup_send_ephemeral(
            interaction,
            strings["declared_note"].format(map=attack.map_key, time=_fmt_paris(attack.expires_at)),
        )
        # battle-ai-rdv-v1: the Gaïa declared note dies at the meeting
        # time too (same helper as the player itinerary note).
        _arm_declared_note_timer(
            str(attack.id),
            interaction,
            int(getattr(note, "id", 0) or 0),
            attack.expires_at,
        )
        await _open_attack_salon_safe(interaction.guild, wiring, strings, attack)
        await refresh_attack_panel_safe(interaction.guild, _locale(interaction))


# ----------------------------------------------------------------------
# The persistent defend button (one per DECLARED attack)
# ----------------------------------------------------------------------
async def _resolve_attack_id_by_key_async(key: str) -> str:
    """Resolve a board-button digest back to an attack id (service scan).

    Check 73 live: the defend/join buttons carried the RAW attack id
    (~64 chars) — the custom_id blew Discord's 100-char cap (50035)
    and the board edit died, so the defend button never appeared. The
    id rides a 16-hex digest now (``_room_key``), resolved back here.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return ""
    try:
        for attack in await wiring.attacks_service.attacks():
            if _room_key(str(attack.id)) == key:
                return str(attack.id)
    except Exception:
        logger.warning("KINGDOM ATTACK: board key resolution failed", exc_info=True)
    return ""


class KingdomAttackDefendButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:defend:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof defend button of one waiting attack."""

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:defend:{_room_key(attack_id)}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.attack_id = attack_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAttackDefendButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(
            await _resolve_attack_id_by_key_async(match["key"]),
            _strings(_locale(interaction))["defend_button"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Take the defense — the button NEVER disappears (effet de surprise).

        restructuration-serveur-v1 b (Drasah 19/10): the button stays
        whatever happens — the SURPRISE rides the answers, not the
        button. A lord outside the defending kingdom gets « Vous ne
        pouvez défendre ce territoire » ; the first lord of the
        defending kingdom registers the defense with NO public sign
        (the salon base message stays untouched) ; another lord of the
        same kingdom gets « Le royaume est déjà défendu ».

        defend-probe-leak (Drasah 29/10): the WRONG-KINGDOM check runs
        FIRST — an outsider (the attacker's camp probing the button)
        must NEVER learn whether a defense is already in place. The
        « already defended » answer exists only for the defending
        kingdom's own lords.
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _lord_of, _open_unique_ephemeral, _wiring
        )
        from kingdoms.mods.kingdoms.models import AttackState

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await _open_unique_ephemeral(interaction, strings["no_service"])
            return
        try:
            attack = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id)
        except Exception:
            await _open_unique_ephemeral(interaction, strings["gaia_gone"])
            return
        if attack.is_over:
            await _open_unique_ephemeral(interaction, strings["gaia_gone"])
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        # defend-probe-leak (Drasah 29/10): ANY lord outside the
        # defending kingdom gets the SAME generic refusal — probing the
        # button must never reveal whether a defense is already taken.
        if lord is None or str(getattr(lord, "kingdom_id", "") or "") != str(attack.defender_kingdom_id):
            await _open_unique_ephemeral(interaction, strings["defend_wrong_kingdom"])
            return
        if getattr(attack, "state", None) is not AttackState.DECLARED and not getattr(attack, "ai_defender", False):
            # A defense is already in place — the kingdom is defended
            # (the defender's NAME stays hidden until the reveal).
            await _open_unique_ephemeral(interaction, strings["defend_already"])
            return
        if getattr(attack, "state", None) is not AttackState.DECLARED:
            await _open_unique_ephemeral(interaction, strings["gaia_gone"])
            return
        await interaction.response.defer(ephemeral=True)
        try:
            attack = await wiring.attacks_service.respond_defense(self.attack_id, str(interaction.user.id))
        except Exception as exc:
            await interaction.followup.send(_combat_failure_note(exc, strings), ephemeral=True)
            return
        await interaction.followup.send(
            strings["defense_done"].format(map=attack.map_key, time=_fmt_paris(attack.expires_at)),
            ephemeral=True,
        )
        # Drasah 18/10 — effet de surprise : plus d'annonce publique du
        # défenseur. Le message de base du salon d'attaque ne bouge PAS
        # (aucun signe public) ; seul le royaume défenseur voit qui
        # défend (bouton « Gestion de la défense » des Remparts).


class KingdomDefenseManagerButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:defense-manager",
):
    """The 🛡️ « Gestion de la défense » button of the Remparts panel.

    Drasah 18/10 — effet de surprise : la défense n'est plus annoncée
    publiquement et le board Attaques ne nomme jamais le défenseur.
    Ce bouton (visible du royaume défenseur dans ses Remparts) montre
    l'état de chaque attaque subie et QUI défend — la réassurance
    privée du royaume.
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:attack:defense-manager",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomDefenseManagerButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["defense_manager_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Show the kingdom's ongoing attacks and their defenders (ephemeral)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _lord_of, _open_unique_ephemeral, _wiring
        )

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or wiring.kingdoms_service is None:
            await _open_unique_ephemeral(interaction, strings["defense_manager_no_service"])
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None or getattr(lord, "kingdom_id", None) is None:
            await _open_unique_ephemeral(interaction, strings["defense_manager_not_defender"])
            return
        kingdom_id = str(lord.kingdom_id)
        kingdom_name = await _kingdom_name(wiring.kingdoms_service, kingdom_id)
        rows: list[str] = []
        try:
            ongoing = [
                a
                for a in await wiring.attacks_service.attacks()
                if not a.is_over and str(getattr(a, "defender_kingdom_id", "")) == kingdom_id
            ]
        except Exception:
            ongoing = []
        from kingdoms.mods.kingdoms.models import AttackState

        for attack in ongoing:
            if getattr(attack, "state", None) is AttackState.DEFENDED and not attack.ai_defender:
                defender = await _lord_name(wiring.kingdoms_service, attack.defender_lord_id)
                state = strings["defense_manager_defended"].format(lord=defender)
            else:
                state = strings["defense_manager_waiting"]
            rows.append(
                strings["defense_manager_row"].format(
                    map=attack.map_key, time=_fmt_paris(attack.expires_at), state=state
                )
            )
        body = "\n".join(rows) if rows else strings["defense_manager_empty"]
        # restructuration-serveur-v1 b: the defender may WITHDRAW their
        # own defense — one ↩️ button per attack THIS lord defends (the
        # draft-start threshold lives service side, ``pick_ban_lead_minutes``).
        view = discord.ui.View(timeout=600)
        for attack in ongoing:
            if (
                not getattr(attack, "revealed", False)
                and getattr(attack, "state", None) is AttackState.DEFENDED
                and str(getattr(attack, "defender_lord_id", "") or "") == str(lord.id)
            ):
                view.add_item(KingdomCancelDefenseButton(str(attack.id), strings["cancel_defense_button"]))
        await _open_unique_ephemeral(
            interaction,
            strings["defense_manager_title"].format(kingdom=kingdom_name) + "\n" + body,
            view=view if list(view.children) else None,  # type: ignore[arg-type]
        )


# ----------------------------------------------------------------------
# planification-attaque-v1 (Drasah 23/10/2026) — the planning surface
# ----------------------------------------------------------------------
def _combat_failure_note(exc: Exception, strings: dict[str, str]) -> str:
    """Map a combat-action failure to its player-facing note.

    taverne-v1 (Drasah 24/10): the engaged and the TAVERN locks carry
    their own FR/EN lines — every other failure keeps the generic
    ``failed`` fallback with the raw message.
    """
    from kingdoms.mods.kingdoms.attacks import LordEngagedError, LordTavernLockedError

    if isinstance(exc, LordTavernLockedError):
        return str(strings["lord_tavern_locked"])
    if isinstance(exc, LordEngagedError):
        return str(strings["lord_engaged"])
    message = getattr(exc, "message", None) or str(exc)
    return str(strings["failed"]).format(message=message)


def _map_label(wiring: Any, map_key: str) -> str:
    """The display name of a map key (fallback: the raw key)."""
    catalog = {
        str(getattr(entry, "key", "")): entry
        for entry in getattr(getattr(wiring.kingdoms_service, "config", None), "maps", None) or ()
    }
    return str(getattr(catalog.get(str(map_key), None), "display_name", None) or map_key)


async def _plan_enemy_territory_options(wiring: Any, own_kingdom_id: str, strings: dict[str, str]) -> list[Any]:
    """The plan choice options: every enemy player territory (cap 25)."""
    import discord

    territories_service = wiring.territories_service
    kingdoms_service = wiring.kingdoms_service
    if territories_service is None or kingdoms_service is None:
        return []
    gaia_ids = {str(k.id) for k in await kingdoms_service.kingdoms() if getattr(k, "is_gaia", False)}
    options: list[discord.SelectOption] = []
    for territory in await territories_service.territories():
        owner = str(territory.owner_kingdom_id)
        if owner == str(own_kingdom_id) or owner in gaia_ids or getattr(territory, "sacred", False):
            continue
        options.append(
            discord.SelectOption(
                label=_map_label(wiring, str(territory.map_key))[:100],
                value=str(territory.map_key)[:100],
                description=_map_description(
                    getattr(getattr(kingdoms_service, "config", None), "maps", None)
                    and {
                        str(getattr(entry, "key", "")): entry
                        for entry in getattr(getattr(kingdoms_service, "config", None), "maps", None)
                    }.get(str(territory.map_key), None),
                    strings,
                ),
            )
        )
        if len(options) >= 25:
            break
    return options


async def _plan_confirm_step(
    wiring: Any,
    interaction: Any,
    strings: dict[str, str],
    own_kingdom_id: str,
    choice1: str,
    meeting_at: Any,
) -> None:
    """The planning confirm step — récap + the optional choices 2 and 3."""
    import discord

    state: dict[str, Any] = {"choice2": None, "choice3": None}
    options = await _plan_enemy_territory_options(wiring, own_kingdom_id, strings)
    view = discord.ui.View(timeout=600)

    def _extras() -> list[str]:
        extras = [choice1]
        if state.get("choice2"):
            extras.append(str(state["choice2"]))
        if state.get("choice3"):
            extras.append(str(state["choice3"]))
        return extras

    def _remaining_options() -> list[discord.SelectOption]:
        taken = set(_extras())
        return [option for option in options if str(option.value) not in taken][:25]

    async def _confirm(target: discord.Interaction[Any]) -> None:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        attacks_service = _wiring().attacks_service
        if attacks_service is None:
            await target.followup.send(strings["plan_no_service"], ephemeral=True)
            return
        try:
            plan = await attacks_service.plan_attack(
                str(target.user.id), _extras(), meeting_at
            )
        except Exception as exc:
            from kingdoms.mods.kingdoms.attacks import LordEngagedError

            message = (
                strings["lord_tavern_locked"]
                if isinstance(exc, LordTavernLockedError)
                else (
                    strings["lord_engaged"]
                    if isinstance(exc, LordEngagedError)
                    else strings["plan_failed"].format(
                        message=getattr(exc, "message", None) or str(exc)
                    )
                )
            )
            await target.followup.send(message, ephemeral=True)
            return
        content = _plan_recap_content(wiring, plan, strings)
        view = discord.ui.View(timeout=None)
        view.add_item(KingdomPlanCancelButton(plan.id, strings["plan_cancel_button"]))
        dm = await _dm_user_safe(target.client, str(target.user.id), content, view)
        if dm is not None:
            await attacks_service.record_plan_dm(
                plan.id, int(getattr(dm, "channel_id", 0) or 0), int(getattr(dm, "id", 0) or 0)
            )
            await target.followup.send(strings["plan_created_note"], ephemeral=True)
        else:
            await target.followup.send(strings["plan_no_dm"], ephemeral=True)
        try:
            await target.response.edit_message(content=strings["plan_created_note"], view=None)
        except Exception:
            logger.debug("kingdoms: plan confirm ephemeral edit failed", exc_info=True)

    confirm = discord.ui.Button(
        label=str(strings["plan_confirm_button"])[:80],
        custom_id="kingdoms:attack:plan:confirm",
        style=discord.ButtonStyle.success,
    )
    confirm.callback = _confirm  # type: ignore[method-assign, assignment]
    view.add_item(confirm)
    for slot, key in (("choice2", "plan_choice2_placeholder"), ("choice3", "plan_choice3_placeholder")):
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"kingdoms:attack:plan:{slot}",
            placeholder=str(strings[key])[:100],
            options=_remaining_options(),
            min_values=1,
            max_values=1,
        )

        async def on_pick(target: discord.Interaction[Any], slot: str = slot) -> None:
            values = getattr(target, "data", None) or {}
            picked = [str(v) for v in values.get("values", [])]
            if picked:
                state[slot] = picked[0]
            # Redraw both selects without the taken choices.
            for item in view.children:
                if getattr(item, "custom_id", "") == "kingdoms:attack:plan:choice2":
                    item.options = _remaining_options()  # type: ignore[union-attr]
                elif getattr(item, "custom_id", "") == "kingdoms:attack:plan:choice3":
                    rest = [o for o in _remaining_options() if str(o.value) != str(state.get("choice2"))][:25]
                    item.options = rest  # type: ignore[union-attr]
            await target.response.edit_message(content=_confirm_content(), view=view)

        select.callback = on_pick  # type: ignore[method-assign, assignment]
        view.add_item(select)

    def _confirm_content() -> str:
        lines = [
            strings["plan_confirm_title"].format(
                map=_map_label(wiring, choice1), time=_fmt_paris(meeting_at)
            ),
            strings["plan_dm_budget"],
            strings["plan_disclaimer"],
        ]
        return "\n".join(lines)

    try:
        await interaction.response.edit_message(content=_confirm_content(), view=view)
    except Exception:
        logger.debug("kingdoms: plan confirm step edit failed", exc_info=True)


def _plan_recap_content(wiring: Any, plan: Any, strings: dict[str, str]) -> str:
    """The full player-facing récap of a plan (DM body)."""
    from kingdoms.mods.kingdoms.attacks import AttackService  # noqa: F401 — typing only

    lines = [strings["plan_dm_header"].format(map=_map_label(wiring, str(plan.active_choice or "")))]
    for index, choice in enumerate(plan.choices, start=1):
        lines.append(
            strings["plan_choices_line"].format(
                n=index,
                map=_map_label(wiring, str(choice)),
                active=strings["plan_active_tag"] if index - 1 == int(plan.active_index) else "",
            )
        )
    attacks_service = getattr(wiring, "attacks_service", None)
    declare_at: Any = plan.meeting_at
    try:
        declare_at = attacks_service._plan_declare_at(plan)  # type: ignore[attr-defined]
    except Exception:
        pass
    lines.append(
        strings["plan_dm_time"].format(
            time=_fmt_paris(plan.meeting_at), declare_time=_fmt_paris(declare_at)
        )
    )
    lines.append(strings["plan_dm_budget"])
    lines.append(strings["plan_disclaimer"])
    return "\n".join(lines)


def _plan_meeting_from_selections(day_offset: int, hour: int, minute: int, now: datetime) -> datetime:
    """Compose the RDV datetime from the wizard dropdowns (Europe/Paris).

    planification-attaque-v2 (Drasah 24/10): the free-text JJ/MM HH:MM
    modal is replaced by dropdowns « Jour / Heure / Minute » — the 24h
    golden rule makes « aujourd'hui » and « demain » the only useful
    days, so ``day_offset`` is 0 or 1. Refuses the past and anything
    beyond 24h (the service re-checks — this is the early feedback).
    """
    tz = ZoneInfo("Europe/Paris")
    now_local = now.astimezone(tz)
    day = (now_local + timedelta(days=day_offset)).date()
    meeting_at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    if meeting_at <= now_local:
        raise ValueError("the meeting must be in the future")
    if meeting_at - now_local > timedelta(hours=24):
        raise ValueError("the meeting must be within 24h")
    return meeting_at


async def _plan_time_step(
    wiring: Any,
    interaction: Any,
    strings: dict[str, str],
    own_kingdom_id: str,
    choice1: str,
) -> None:
    """The RDV-hour step of the planning wizard — dropdowns + Valider.

    planification-attaque-v2: THREE dropdowns (Jour 0/1, Heure 00-23,
    Minute 00/15/30/45) and a ✅ Valider button replace the modal —
    « libre, c'est pas ouf » (Drasah 24/10).
    """
    import discord

    state: dict[str, int] = {"day": 0, "hour": 20, "minute": 0}
    tz = ZoneInfo("Europe/Paris")
    now_local = game_now().astimezone(tz)
    today = now_local.date()
    day_select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:attack:plan:time-day",
        placeholder=str(strings["plan_time_day_label"])[:100],
        options=[
            discord.SelectOption(
                label=str(strings["plan_time_day_today"]).format(
                    date=today.strftime("%d/%m")
                )[:100],
                value="0",
            ),
            discord.SelectOption(
                label=str(strings["plan_time_day_tomorrow"]).format(
                    date=(today + timedelta(days=1)).strftime("%d/%m")
                )[:100],
                value="1",
            ),
        ],
        min_values=1,
        max_values=1,
    )
    hour_select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:attack:plan:time-hour",
        placeholder=str(strings["plan_time_hour_label"])[:100],
        options=[
            discord.SelectOption(label=f"{value:02d}h", value=str(value))
            for value in range(24)
        ],
        min_values=1,
        max_values=1,
    )
    minute_select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:attack:plan:time-minute",
        placeholder=str(strings["plan_time_minute_label"])[:100],
        options=[
            discord.SelectOption(label=f"{value:02d}", value=str(value))
            for value in (0, 15, 30, 45)
        ],
        min_values=1,
        max_values=1,
    )

    async def _pick(target: discord.Interaction[Any], key: str) -> None:
        values = getattr(target, "data", None) or {}
        picked = [str(v) for v in values.get("values", [])]
        if picked:
            state[key] = int(picked[0])
        await target.response.edit_message(content=_hint(), view=view)

    def _hint() -> str:
        lines = [str(strings["plan_wizard_title"]), strings["plan_time_hint"]]
        return "\n".join(lines)

    for select, key in ((day_select, "day"), (hour_select, "hour"), (minute_select, "minute")):

        async def on_pick(
            target: discord.Interaction[Any], key: str = key
        ) -> None:
            await _pick(target, key)

        select.callback = on_pick  # type: ignore[method-assign, assignment]

    validate = discord.ui.Button(
        label=str(strings["plan_time_validate_button"])[:80],
        custom_id="kingdoms:attack:plan:time-validate",
        style=discord.ButtonStyle.success,
    )

    async def _validate(target: discord.Interaction[Any]) -> None:
        try:
            meeting_at = _plan_meeting_from_selections(
                state["day"], state["hour"], state["minute"], game_now()
            )
        except ValueError:
            await target.response.edit_message(
                content=strings["plan_time_invalid"], view=view
            )
            return
        await _plan_confirm_step(
            wiring, target, strings, own_kingdom_id, choice1, meeting_at
        )

    validate.callback = _validate  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(day_select)
    view.add_item(hour_select)
    view.add_item(minute_select)
    view.add_item(validate)
    await interaction.response.edit_message(content=_hint(), view=view)


class KingdomPlanButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:plan$",
):
    """The 📅 « Planifier une attaque » button of the declare panel."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:attack:plan",
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomPlanButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["plan_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Step 1 of the planning wizard — the choice-1 territory select."""
        import discord

        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _lord_of, _open_unique_ephemeral, _wiring
        )

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or wiring.kingdoms_service is None:
            await _open_unique_ephemeral(interaction, strings["plan_no_service"])
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None or getattr(lord, "kingdom_id", None) is None:
            await _open_unique_ephemeral(
                interaction, strings["defense_manager_not_defender"]
            )
            return
        from kingdoms.mods.kingdoms.attacks import LordEngagedError, LordTavernLockedError

        try:
            await wiring.attacks_service._refuse_engaged(str(interaction.user.id), game_now())
            wiring.attacks_service._refuse_tavern(lord, game_now())
        except LordEngagedError:
            await _open_unique_ephemeral(interaction, strings["lord_engaged"])
            return
        except LordTavernLockedError:
            await _open_unique_ephemeral(interaction, strings["lord_tavern_locked"])
            return
        options = await _plan_enemy_territory_options(wiring, str(lord.kingdom_id), strings)
        if not options:
            await _open_unique_ephemeral(interaction, strings["no_target"])
            return
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:attack:plan:choice1",
            placeholder=str(strings["choose_territory"]).split("—")[0].strip()[:100],
            options=options,
            min_values=1,
            max_values=1,
        )

        async def on_pick(target: discord.Interaction[Any]) -> None:
            values = getattr(target, "data", None) or {}
            picked = [str(v) for v in values.get("values", [])]
            if not picked:
                return
            await _plan_time_step(
                wiring, target, strings, str(lord.kingdom_id), picked[0]
            )

        select.callback = on_pick  # type: ignore[method-assign, assignment]
        view = discord.ui.View(timeout=600)
        view.add_item(select)
        lines = [str(strings["plan_wizard_title"]), strings["plan_disclaimer"]]
        await _open_unique_ephemeral(interaction, "\n".join(lines), view=view)


class KingdomPlanCancelButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:plan:cancel:(?P<key>[0-9a-f]{16})",
):
    """The ❌ cancel button of one live plan (free — nothing consumed)."""

    def __init__(self, plan_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:plan:cancel:{_room_key(plan_id)}",
                style=discord.ButtonStyle.danger,
            )
        )
        self.plan_id = plan_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomPlanCancelButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(
            await _resolve_plan_id_by_key_async(match["key"]),
            _strings(_locale(interaction))["plan_cancel_button"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Cancel the plan and edit the ephemeral in place."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await interaction.response.send_message(strings["plan_no_service"], ephemeral=True)
            return
        try:
            await wiring.attacks_service.cancel_plan(self.plan_id, str(interaction.user.id))
        except Exception:
            await interaction.response.send_message(
                strings["plan_failed"].format(message="plannification introuvable"), ephemeral=True
            )
            return
        try:
            await interaction.response.edit_message(
                content=strings["plan_cancelled_ephemeral"], view=None
            )
        except Exception:
            await interaction.followup.send(strings["plan_cancelled_ephemeral"], ephemeral=True)


async def _resolve_plan_id_by_key_async(key: str) -> str:
    """Resolve the hashed plan key back to its id (restart-proof)."""
    for plan in await _all_plans_async():
        if _room_key(str(plan.id)) == str(key):
            return str(plan.id)
    return ""


async def _all_plans_async() -> list[Any]:
    """Every plan of the current data set (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return []
    try:
        return await wiring.attacks_service.plans()
    except Exception:
        logger.debug("kingdoms: plan scan failed", exc_info=True)
        return []


class KingdomAttackManagerButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:attack-manager$",
):
    """The ⚔️ « Gestion d'attaque » button of the Remparts panel.

    planification-attaque-v1 (Drasah 23/10): the kingdom members see
    the DECLARED attacks AND the PLANNED ones (every lord's plans with
    their 3 choices visible) — the kingdom's private war room.
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:attack:attack-manager",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAttackManagerButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["plan_manager_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Show the kingdom's declared + planned attacks (ephemeral)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _lord_of, _open_unique_ephemeral, _wiring
        )
        from kingdoms.mods.kingdoms.models import PlanState

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or wiring.kingdoms_service is None:
            await _open_unique_ephemeral(interaction, strings["plan_no_service"])
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None or getattr(lord, "kingdom_id", None) is None:
            await _open_unique_ephemeral(
                interaction, strings["defense_manager_not_defender"]
            )
            return
        kingdom_id = str(lord.kingdom_id)
        kingdom_name = await _kingdom_name(wiring.kingdoms_service, kingdom_id)
        sections: list[str] = [strings["plan_manager_title"].format(kingdom=kingdom_name)]
        attacks = [
            a
            for a in await wiring.attacks_service.attacks()
            if not a.is_over
            and str(getattr(a, "attacker_kingdom_id", "")) == kingdom_id
        ]
        plans = [
            p
            for p in await wiring.attacks_service.plans()
            if p.state is PlanState.PLANNED and str(p.kingdom_id) == kingdom_id
        ]
        if not attacks and not plans:
            sections.append(strings["plan_manager_empty"])
        if attacks:
            sections.append(strings["plan_manager_declared_section"])
            for attack in attacks:
                state = getattr(attack, "state", None)
                state_label = str(getattr(state, "value", "") or "")
                sections.append(
                    strings["plan_manager_declared_row"].format(
                        map=_map_label(wiring, str(attack.map_key)),
                        time=_fmt_paris(attack.expires_at),
                        state=state_label,
                    )
                )
        if plans:
            sections.append(strings["plan_manager_planned_section"])
            for plan in plans:
                lord_name = await _lord_name(wiring.kingdoms_service, str(plan.lord_id))
                for index, choice in enumerate(plan.choices, start=1):
                    sections.append(
                        strings["plan_choices_line"].format(
                            n=index,
                            map=_map_label(wiring, str(choice)),
                            active=strings["plan_active_tag"]
                            if index - 1 == int(plan.active_index)
                            else "",
                        )
                    )
                sections.append(
                    strings["plan_manager_planned_row"].format(
                        map=_map_label(wiring, str(plan.active_choice or "")),
                        time=_fmt_paris(plan.meeting_at),
                        lord=lord_name,
                        index=int(plan.active_index) + 1,
                        total=len(plan.choices),
                    )
                )
        await _open_unique_ephemeral(interaction, "\n".join(sections))


async def _update_plan_dm_safe(bot: Any, plan: Any, content: str) -> bool:
    """Edit the plan's récap DM in place — False when impossible."""
    channel_id = int(getattr(plan, "dm_channel_id", 0) or 0)
    message_id = int(getattr(plan, "dm_message_id", 0) or 0)
    if not channel_id or not message_id:
        return False
    try:
        channel = getattr(bot, "get_channel", lambda _id: None)(channel_id)
        if channel is None:
            return False
        message = channel.get_partial_message(message_id)
        await message.edit(content=content)
        return True
    except Exception:
        logger.debug("kingdoms: plan DM edit failed", exc_info=True)
    return False


async def _run_plan_events_safe(
    bot: Any,
    guild: Any,
    attacks_service: Any,
    kingdoms_service: Any,
    strings: dict[str, str],
    wiring: Any,
) -> int:
    """Play the due plans: execute/switch/break + the DM récap edits.

    The executed declarations open their attack salon exactly like a
    manual one; every plan event reaches the player through his récap
    DM (edited in place, a fresh DM when the refs are lost).
    """
    dirty = 0
    from kingdoms.mods.kingdoms.models import PlanState

    for plan in await attacks_service.plan_events():
        dirty += 1
        executed_attack = None
        if plan.last_event == "executed" and plan.executed_attack_id:
            executed_attack = next(
                (a for a in await attacks_service.attacks() if str(a.id) == str(plan.executed_attack_id)),
                None,
            )
        # Build the event note FIRST (it reads last_event_map), then
        # clear the flag service side.
        note = ""
        if plan.last_event == "executed" and executed_attack is not None:
            note = strings["plan_event_executed"].format(
                map=_map_label(wiring, str(executed_attack.map_key)),
                time=_fmt_paris(executed_attack.expires_at),
            )
        elif plan.last_event == "switched":
            note = strings["plan_event_switched"].format(
                old=_map_label(wiring, str(plan.last_event_map or "")),
                map=_map_label(wiring, str(plan.active_choice or "")),
            )
        elif plan.last_event in ("broken_by_declaration", "broken_target"):
            note = strings["plan_event_broken"].format(
                map=_map_label(wiring, str(plan.last_event_map or plan.active_choice or ""))
            )
        elif plan.last_event == "broken_budget":
            note = strings["plan_event_broken"].format(
                map=_map_label(wiring, str(plan.active_choice or ""))
            )
        elif plan.last_event == "broken_engaged":
            note = strings["lord_engaged"]
        elif plan.last_event == "broken_tavern":
            note = strings["lord_tavern_locked"]
        elif plan.last_event == "broken_lords_day":
            note = strings["plan_event_broken"].format(
                map=_map_label(wiring, str(plan.active_choice or ""))
            )
        elif plan.last_event == "cancelled":
            note = strings["plan_event_cancelled"]
        if plan.state is not PlanState.PLANNED:
            # The final states carry the event note as the DM's last
            # body — the récap becomes the receipt.
            recap = _plan_recap_content(wiring, plan, strings)
            content = recap + "\n\n" + note
        else:
            content = _plan_recap_content(wiring, plan, strings) + "\n\n" + note
        sent = await _update_plan_dm_safe(bot, plan, content)
        if not sent:
            dm = await _dm_user_safe(bot, str(plan.lord_id), content)
            if dm is not None:
                await attacks_service.record_plan_dm(
                    str(plan.id),
                    int(getattr(dm, "channel_id", 0) or 0),
                    int(getattr(dm, "id", 0) or 0),
                )
        await attacks_service.clear_plan_event(str(plan.id))
        # The executed declarations open their salon + refresh the panel.
        if executed_attack is not None:
            await _open_attack_salon_safe(guild, wiring, strings, executed_attack)
    return dirty


class KingdomAttackJoinButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:join:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof join button of one open Gaïa battle.

    Drasah 14/10: a Gaïa territory is attacked through the declare
    flow like any kingdom; other lords then JOIN the battle from the
    panel (1v1v1…, one lord per kingdom, up to the participant
    ceiling) — the lobby URL of the first declarer is the meeting
    point.
    """

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:join:{_room_key(attack_id)}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.attack_id = attack_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAttackJoinButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(
            await _resolve_attack_id_by_key_async(match["key"]),
            _strings(_locale(interaction))["gaia_join_button"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Join the open Gaïa battle of this button's attack."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _open_unique_ephemeral, _wiring
        from kingdoms.mods.kingdoms.models import AttackKind

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await _open_unique_ephemeral(interaction, strings["no_service"])
            return
        battle = next(
            (a for a in await wiring.attacks_service.attacks() if str(a.id) == self.attack_id and not a.is_over),
            None,
        )
        if battle is None or getattr(battle, "kind", None) is not AttackKind.GAIA or battle.state.value != "declared":
            await _open_unique_ephemeral(interaction, strings["gaia_gone"])
            return
        await interaction.response.defer(ephemeral=True)
        try:
            attack = await wiring.attacks_service.declare_gaia_attack(
                str(interaction.user.id),
                battle.map_key,
                str(battle.lobby_url or ""),
            )
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(strings["failed"].format(message=message), ephemeral=True)
            return
        await interaction.followup.send(
            strings["gaia_join_note"].format(
                map=attack.map_key,
                count=len(attack.participants),
                time=_fmt_paris(attack.expires_at),
            ),
            ephemeral=True,
        )
        await _attack_salon_send_safe(
            interaction.guild,
            attack,
            strings["gaia_join_announce"].format(
                lord=interaction.user.display_name,
                map=attack.map_key,
                count=len(attack.participants),
            ),
        )


# ----------------------------------------------------------------------
# The cancellation buttons (restructuration-serveur-v1 b)
# ----------------------------------------------------------------------
class KingdomAttackCancelButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:cancel:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof ❌ Annuler button of an attack salon.

    The DECLARANT alone, inside the 10-minute window — budgets refunded,
    and the salon is purged right away (axis ouvert Drasah — default
    followed: direct purge, no « annulée » leftover message).
    """

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:cancel:{_room_key(attack_id)}",
                style=discord.ButtonStyle.danger,
            )
        )
        self.attack_id = attack_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAttackCancelButton:
        """Resolve the key back to an attack id (restart-safe)."""
        return cls(
            await _resolve_attack_id_by_key_async(match["key"]),
            _strings(_locale(interaction))["cancel_attack_button"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Cancel the attack, purge its salon, confirm (ephemeral)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _open_unique_ephemeral, _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await _open_unique_ephemeral(interaction, strings["no_service"])
            return
        await interaction.response.defer(ephemeral=True)
        try:
            attack = await wiring.attacks_service.cancel_attack(self.attack_id, str(interaction.user.id))
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(strings["failed"].format(message=message), ephemeral=True)
            return
        salon = _attack_salon(interaction.guild, attack)
        if salon is not None:
            try:
                await salon.delete()
            except Exception:
                logger.warning("KINGDOM ATTACK: attack salon purge failed", exc_info=True)
        try:
            await wiring.attacks_service.mark_attack_salon_cleaned(str(attack.id))
        except Exception:
            logger.warning("KINGDOM ATTACK: attack salon flag failed", exc_info=True)
        await interaction.followup.send(strings["cancel_attack_done"], ephemeral=True)


class KingdomCancelDefenseButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:defend:cancel:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof ↩️ « Retirer ma défense » button (defense manager).

    Drasah 19/10 (b): the defender may withdraw at any moment EXCEPT
    once the draft started — the threshold rides ``pick_ban_lead_minutes``
    (service side). The button lives in the Remparts defense-manager
    ephemeral view, on the rows the clicking lord defends themself.
    """

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:defend:cancel:{_room_key(attack_id)}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.attack_id = attack_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomCancelDefenseButton:
        """Resolve the key back to an attack id (restart-safe)."""
        return cls(
            await _resolve_attack_id_by_key_async(match["key"]),
            _strings(_locale(interaction))["cancel_defense_button"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Withdraw the defense — budget refunded (ephemeral confirm)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _open_unique_ephemeral, _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await _open_unique_ephemeral(interaction, strings["no_service"])
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await wiring.attacks_service.cancel_defense(self.attack_id, str(interaction.user.id))
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(strings["failed"].format(message=message), ephemeral=True)
            return
        await interaction.followup.send(strings["cancel_defense_done"], ephemeral=True)


# ----------------------------------------------------------------------
# The history of the resolved confrontations (restructuration-serveur-v1 b)
# ----------------------------------------------------------------------
async def _history_lines(wiring: Any, strings: dict[str, str]) -> list[str]:
    """Build the timestamped history lines (resolved confrontations, desc)."""
    from kingdoms.mods.kingdoms.models import AttackState

    if wiring.attacks_service is None or wiring.kingdoms_service is None:
        return []
    try:
        attacks = [a for a in await wiring.attacks_service.attacks() if a.is_over]
    except Exception:
        return []
    attacks.sort(key=lambda a: str(getattr(a, "resolved_at", None) or ""), reverse=True)
    lines: list[str] = []
    for attack in attacks:
        resolved = getattr(attack, "resolved_at", None)
        date = _fmt_paris(resolved) if resolved is not None else "—"
        attacker = await _kingdom_name(wiring.kingdoms_service, attack.attacker_kingdom_id)
        defender = await _kingdom_name(wiring.kingdoms_service, attack.defender_kingdom_id)
        if attack.state is AttackState.CANCELLED:
            outcome = strings["history_outcome_cancelled"]
        elif attack.winner_kingdom_id:
            winner = await _kingdom_name(wiring.kingdoms_service, attack.winner_kingdom_id)
            outcome = strings["history_outcome_win"].format(kingdom=winner)
        elif attack.state is AttackState.EXPIRED:
            outcome = strings["history_outcome_double"]
        else:
            outcome = strings["history_outcome_expired"]
        lines.append(
            strings["history_line"].format(
                date=date, map=attack.map_key, attacker=attacker, defender=defender, outcome=outcome
            )
        )
    return lines


def _history_page_view(
    interaction: discord.Interaction, strings: dict[str, str], pages: list[str], page: int
) -> discord.ui.View:
    """Build the ephemeral history pager view (prev/next, closures)."""

    async def _show(target: discord.Interaction, target_page: int) -> None:
        await target.response.edit_message(
            content="\n".join(
                (
                    strings["history_title"],
                    "",
                    pages[target_page],
                    "",
                    strings["history_page"].format(page=target_page + 1, pages=len(pages)),
                )
            ),
            view=_history_page_view(target, strings, pages, target_page),
        )

    async def _on_prev(target: discord.Interaction) -> None:
        await _show(target, max(0, page - 1))

    async def _on_next(target: discord.Interaction) -> None:
        await _show(target, min(len(pages) - 1, page + 1))

    view = discord.ui.View(timeout=600)
    if page > 0:
        prev: discord.ui.Button[discord.ui.View] = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
        prev.callback = _on_prev  # type: ignore[method-assign,assignment]
        view.add_item(prev)
    if page < len(pages) - 1:
        nxt: discord.ui.Button[discord.ui.View] = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)
        nxt.callback = _on_next  # type: ignore[method-assign,assignment]
        view.add_item(nxt)
    return view


class KingdomAttackHistoryButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:history",
):
    """The restart-proof 📜 Historique button of the declare panel (b).

    Ephemeral, paginated, timestamped view of the RESOLVED
    confrontations (axe ouvert format — default followed: one line per
    attack, date de résolution desc, winner/forfeit/cancelled).
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:attack:history",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAttackHistoryButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["history_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Show the paginated history of the resolved confrontations."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _open_unique_ephemeral, _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await _open_unique_ephemeral(interaction, strings["no_service"])
            return
        # attaque-ergonomie-v1 (Drasah 28/10): the history button
        # must NEVER go silent — a crash inside the builder still
        # answers the player (empty history page).
        try:
            lines = await _history_lines(wiring, strings)
        except Exception:
            logger.warning("KINGDOM ATTACK: history build failed", exc_info=True)
            lines = []
        if not lines:
            await _open_unique_ephemeral(interaction, strings["history_empty"])
            return
        pages = ["\n".join(lines[start : start + 10]) for start in range(0, len(lines), 10)]
        await _open_unique_ephemeral(
            interaction,
            "\n".join(
                (
                    strings["history_title"],
                    "",
                    pages[0],
                    "",
                    strings["history_page"].format(page=1, pages=len(pages)),
                )
            ),
            view=_history_page_view(interaction, strings, pages, 0),
        )


class KingdomAttackInfoButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:info",
):
    """attaque-ergonomie-v1 (Drasah 28/10): the former declaration

    thread's « Déroulé de l'attaque & droits du défenseur » sheet —
    one ⚔️ button of the declare panel now, answered as a single
    ephemeral window (the thread under the announcement read
    badly and nobody opened it).
    """

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:attack:info",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAttackInfoButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["attack_info_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the attack & defense rundown sheet (single window)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _open_unique_ephemeral

        strings = _strings(_locale(interaction))
        await _open_unique_ephemeral(interaction, str(strings["thread_body"]))


# ----------------------------------------------------------------------
# The DM pick & ban view (T-5)
# ----------------------------------------------------------------------
class KingdomPickBanView(discord.ui.View):
    """The ephemeral DM view: ranked picks, one validate button.

    Drasah 18/10 v2: NO sabotage select anymore (the Sabotage/Faucon
    moves to the battle room) and a BLIND resolution — the validating
    player NEVER sees the adversary civilizations (the live beta
    asymmetry: the second validator knew everything first). When the
    draft resolves, BOTH DMs are edited (anti-spam) into the ready
    prompt carrying the « Prêt » button.
    """

    def __init__(
        self,
        attack_id: str,
        player_id: str,
        side: str,
        hand: list[str],
        locale: str,
    ) -> None:
        super().__init__(timeout=600)
        self.attack_id = attack_id
        self.player_id = player_id
        self.side = side
        strings = _strings(locale)
        self.strings = strings
        self.rank_selects: list[discord.ui.Select[Any]] = []
        for index in range(min(3, len(hand))):
            select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder=strings["pickban_pick"].format(n=index + 1)[:100],
                custom_id=f"kingdoms:attack:pickban:pick:{index}",
                options=[discord.SelectOption(label=str(civ)[:100], value=str(civ)) for civ in hand][:25],
                min_values=1 if index == 0 else 0,
                max_values=1,
            )
            select.callback = self._make_on_select()  # type: ignore[method-assign]
            self.rank_selects.append(select)
            self.add_item(select)
        button: discord.ui.Button[discord.ui.View] = discord.ui.Button(
            label=strings["pickban_validate"][:80],
            custom_id="kingdoms:attack:pickban:validate",
            style=discord.ButtonStyle.primary,
        )
        button.callback = self._on_validate  # type: ignore[method-assign]
        self.add_item(button)

    def _make_on_select(self) -> Any:
        """Ack a select interaction (live beta 18/10 fix).

        The selects had no callback: Discord showed « the application
        did not respond » and no civilization could ever be picked. The
        acknowledged interaction refreshes the select values held by
        THIS view instance — `_on_validate` reads them back at submit
        time.
        """

        async def _on_select(interaction: discord.Interaction) -> None:
            try:
                await interaction.response.defer()
            except Exception:
                logger.warning("KINGDOM ATTACK: pick & ban select ack failed", exc_info=True)

        return _on_select

    async def _on_validate(self, interaction: discord.Interaction) -> None:
        """Submit the ranked picks, then validate (D77, blind)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = self.strings
        wiring = _wiring()
        if wiring.attacks_service is None:
            await _unique_notice(interaction, strings["no_service"])
            return
        picks: list[str] = []
        for select in self.rank_selects:
            picks.extend(select.values)
        # pickban-doublon-v1 (Drasah 28/10): the old single message
        # answered « classe tes civs » to BOTH a missing pick and a
        # DUPLICATE — the duplicate case now has its own wording.
        # PATCH 1.0 item 1 (Drasah 29/10): the refusals ride the fenêtre
        # unique registry — a new refusal replaces the previous one,
        # and the corrected validation (below) closes the stale
        # warning instead of leaving it stacked under the DM.
        if len(set(picks)) != len(picks):
            await _unique_notice(interaction, strings["pickban_duplicate"])
            return
        if len(picks) != len(self.rank_selects):
            await _unique_notice(
                interaction, strings["pickban_missing"].format(count=len(self.rank_selects))
            )
            return
        try:
            await wiring.attacks_service.set_picks(self.attack_id, self.player_id, picks)
            attack = await wiring.attacks_service.validate_pickban(self.attack_id, self.player_id)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await _unique_notice(interaction, strings["failed"].format(message=message))
            return
        # Symmetry (Drasah 18/10 v2): the SAME answer for the first
        # and the second validator — nobody sees the adversary civs.
        # PATCH 1.0 item 1: the validated picks clear the stale
        # refusal window (doublon / classe d'abord) — the DM edit is
        # the only surviving surface.
        await _close_stale_notice(str(interaction.user.id))
        content = strings["pickban_waiting"]
        try:
            await interaction.response.edit_message(content=content, view=None)
        except Exception:
            logger.warning("KINGDOM ATTACK: pick & ban DM edit failed", exc_info=True)
        # chef-mercenaire-v1 T2c: a consumed draft use moves the
        # Alliances rubrique — refresh the picker's kingdom views.
        try:
            from kingdoms.mods.kingdoms.kingdom_persistent import (
                _refresh_realm_views_for_kingdom_safe,
            )

            if wiring.kingdoms_service is not None:
                fresh = next(
                    (
                        item
                        for item in await wiring.kingdoms_service.lords()
                        if str(item.id) == str(self.player_id)
                    ),
                    None,
                )
                if fresh is not None and fresh.kingdom_id:
                    await _refresh_realm_views_for_kingdom_safe(
                        interaction.guild, str(fresh.kingdom_id)
                    )
        except Exception:
            logger.warning("KINGDOM ATTACK: mercenary realm refresh failed", exc_info=True)
        pickban: dict[str, Any] = dict(attack.pickban or {})
        if not pickban.get("resolved_pickban"):
            return
        # Drasah 18/10 v4: the draft resolved — edit BOTH DMs into the
        # REVEAL (the adversary's shuffled civs) + the sabotage prompt
        # (anti-spam, no new DM): the phase happens HERE, before the RDV.
        await _edit_pickban_dms_reveal(interaction.client, attack, wiring.kingdoms_service, strings)


def _ready_key(attack_id: str, player_id: str) -> str:
    """Return the 16-hex digest keying the Prêt button (custom ids stay short)."""
    return hashlib.sha256(f"{attack_id}:{player_id}".encode()).hexdigest()[:16]


def _room_key(attack_id: str) -> str:
    """Return the 16-hex digest keying a battle-room component."""
    return hashlib.sha256(str(attack_id).encode()).hexdigest()[:16]


async def _sabotage_open_available(wiring: Any, attack: Any, side: str) -> bool:
    """Whether this side can still sabotage (sabotage-dm-fresh-v1).

    True while the side owns a usable Sabotage charge or a ready
    Faucon AND the 2-per-attack cap is not reached — the 🗡️ DM button
    wears this state (blue = can sabotage, red = done/dry).
    """
    if wiring is None:
        return True
    pickban = dict(attack.pickban or {})
    used = len([str(civ) for civ in (pickban.get(f"{side}_sabotages") or [])])
    if used >= 2:
        return False
    view = await _sabotage_menu_view(wiring, attack, side, _strings("fr"))
    return view is not None and bool(list(view.children))


async def _sabotage_dm_view(
    wiring: Any, attack: Any, side: str, strings: dict[str, str]
) -> discord.ui.View:
    """Build the persistent sabotage-phase view of one player's DM (v4).

    Two restart-proof buttons: open the sabotage/Faucon menu, or Pass
    (settle the side). A player with neither sabotage nor Faucon sees
    the notice when OPENING the menu (availability is dynamic).
    sabotage-dm-fresh-v1 (Drasah 30/10): the 🗡️ button carries the
    availability — blue (Primary) while the side can still sabotage,
    red (Danger) once the stock is dry or the 2-cap is reached. The
    DM is re-edited after every pick, so the color follows the live
    state.
    """
    player_id = str(attack.attacker_lord_id if side == "attacker" else attack.defender_lord_id)
    available = await _sabotage_open_available(wiring, attack, side)
    view = discord.ui.View(timeout=None)
    view.add_item(
        KingdomSabotageOpenButton(
            str(attack.id), player_id, strings["sab_open_button"], available=available
        )
    )
    view.add_item(KingdomSabotagePassButton(str(attack.id), player_id, strings["sab_pass_button"]))
    return view


async def _edit_dm_message_safe(
    bot: Any,
    ref: Any,
    content: str,
    view: discord.ui.View | None,
    files: list[discord.File] | None = None,
) -> bool:
    """Edit a stored DM message (channel_id + message_id) — best effort.

    dm-crest-edit-fix (Drasah 24/10, corrected T3b 25/10): the edit
    used a PartialMessage, then a full Message but with ``files=`` —
    ``Message.edit`` accepts NO ``files`` kwarg (only
    ``attachments``), so every reveal edit carrying the blason PNGs
    raised TypeError and fell back to a NEW DM. The edit now passes
    ``attachments`` (only when files exist); a lost or deleted
    message still falls back.
    """
    try:
        channel = getattr(bot, "get_channel", lambda _id: None)(int(ref["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(ref["channel_id"]))
        message = await channel.fetch_message(int(ref["message_id"]))
        kwargs: dict[str, Any] = {"content": content, "view": view}
        if files is not None:
            kwargs["attachments"] = files
        await message.edit(**kwargs)
        return True
    except Exception:
        logger.warning("KINGDOM ATTACK: DM edit failed — falling back to a new DM", exc_info=True)
        return False


async def _sabotage_reveal_payload(
    wiring: Any, attack: Any, side: str, strings: dict[str, str]
) -> tuple[str, str, discord.ui.View, list[discord.File] | None]:
    """Build one side's REVEAL DM content + view + crossed mosaic.

    sabotage-dm-fresh-v1 (Drasah 30/10): the payload is shared by the
    initial reveal AND every post-pick refresh — the DM stays the ONE
    living surface, the mosaic wears the ❌ crosses of the civs this
    side already sabotaged, the stock line and the 🗡️ button color
    follow the live state. The order is SEEDED (stable across every
    refresh — a fresh shuffle would reorder the mosaic at each snipe).
    """
    pickban = dict(attack.pickban or {})
    player_id = str(attack.attacker_lord_id if side == "attacker" else attack.defender_lord_id)
    enemy = "defender" if side == "attacker" else "attacker"
    shuffled = _reveal_civs(f"{attack.id}:{enemy}", pickban.get(f"{enemy}_picks"))
    civs = ", ".join(shuffled) or "—"
    content = strings["pickban_reveal_dm"].format(
        map=attack.map_key, civs=civs, deadline=_fmt_paris(attack.expires_at)
    )
    # sabotage-ux-v1 (Drasah 22/10): the reveal keeps the stock
    # line visible (the sabotage phase follows right after).
    if wiring is not None:
        content = f"{content}\n\n{await _sabotage_stock_line(wiring, attack, side, strings)}"
    view = await _sabotage_dm_view(wiring, attack, side, strings)
    used = {str(civ) for civ in (pickban.get(f"{side}_sabotages") or [])}
    # blasons-affichage-v1 (Drasah 28/10): the revealed civs wear
    # ONE composite mosaic — each name painted above its crest
    # (the alliances-lisibilite-v1 style), in the SAME shuffled
    # order as the names (the raw pick order stays hidden). The
    # origin badge is STRIPPED here (revelation 28/10): the ⭐ is
    # useless on a reveal, the Alliances panel keeps its own.
    mosaic = await civ_crest_mosaic_file(
        [(civ, "", civ) for civ in shuffled],
        filename="reveal.png",
        with_origin_badges=False,
        crossed=used,
    )
    files = [mosaic] if mosaic is not None else None
    return player_id, content, view, files


async def _refresh_sabotage_dm_safe(
    bot: Any, wiring: Any, attack: Any, side: str, strings: dict[str, str]
) -> None:
    """Edit one side's Reveal DM after a sabotage pick (dm-fresh-v1).

    Option A (Drasah 30/10): the DM is re-edited in place with the
    crossed mosaic + fresh stock line; a dead or lost message falls
    back to a NEW DM so the surface is never lost. Best effort — a
    failing refresh never breaks the pick itself.
    """
    player_id, content, view, files = await _sabotage_reveal_payload(wiring, attack, side, strings)
    if not player_id:
        return
    ref = (attack.pickban or {}).get(f"{side}_dm") or {}
    edited = False
    if ref.get("channel_id") and ref.get("message_id"):
        edited = await _edit_dm_message_safe(bot, ref, content, view, files)
    if not edited:
        await _dm_user_safe(bot, player_id, content, view, files)


async def _edit_pickban_dms_reveal(bot: Any, attack: Any, kingdoms_service: Any, strings: dict[str, str]) -> None:
    """Edit both draft DMs into the REVEAL + sabotage prompt (v4).

    Drasah 18/10 v4: as soon as both sides validated, each player
    receives the adversary's 3 civilizations in RANDOM order (the
    ranking stays hidden) followed by the sabotage question. The refs
    come from the pickban store (``record_pickban_dm``); a failed edit
    (restart gap, expired message) falls back to a NEW DM.
    """
    pickban: dict[str, Any] = dict(attack.pickban or {})
    wiring = None
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
    except Exception:
        wiring = None
    for side, player_id in (
        ("attacker", str(attack.attacker_lord_id)),
        ("defender", str(attack.defender_lord_id or "")),
    ):
        if not player_id or (side == "defender" and attack.ai_defender):
            continue
        _pid, content, view, files = await _sabotage_reveal_payload(wiring, attack, side, strings)
        ref = pickban.get(f"{side}_dm") or {}
        edited = False
        if ref.get("channel_id") and ref.get("message_id"):
            edited = await _edit_dm_message_safe(bot, ref, content, view, files)
        if not edited:
            await _dm_user_safe(bot, player_id, content, view, files)


async def _edit_pickban_dms_default_reveal(
    bot: Any, attack: Any, kingdoms_service: Any, strings: dict[str, str]
) -> None:
    """Edit both draft DMs into the DEFAULTED draft's reveal (FR live).

    timeline-timers-v1 (Drasah 23/10): when the anti-blocking default
    ran at T (a side never ranked its civs), the draft DMs never got
    their reveal — they stayed stuck on « waiting for the adversary »
    forever. This edits both DMs (own picks already ranked, adversary
    civs in random order, crests attached — NO sabotage view: the
    default wiped every sabotage and no Faucon applies), falling
    back to a fresh DM when the refs are lost. The probe is
    idempotent service-side, so a restart replays nothing.
    """
    pickban: dict[str, Any] = dict(attack.pickban or {})
    for side, player_id in (
        ("attacker", str(attack.attacker_lord_id)),
        ("defender", str(attack.defender_lord_id or "")),
    ):
        if not player_id or (side == "defender" and attack.ai_defender):
            continue
        enemy = "defender" if side == "attacker" else "attacker"
        shuffled = _reveal_civs(f"{attack.id}:{enemy}", pickban.get(f"{enemy}_picks"))
        civs = ", ".join(shuffled) or "—"
        content = strings["pickban_default_reveal_dm"].format(map=attack.map_key, civs=civs)
        # blasons-affichage-v1 (Drasah 28/10): the defaulted reveal
        # wears the SAME one-mosaic look — each name painted above
        # its crest, in the SAME shuffled order as the names, WITHOUT
        # the origin badge (the ⭐ is useless on a reveal).
        mosaic = await civ_crest_mosaic_file(
            [(civ, "", civ) for civ in shuffled],
            filename="reveal.png",
            with_origin_badges=False,
        )
        files = [mosaic] if mosaic is not None else None
        ref = pickban.get(f"{side}_dm") or {}
        edited = False
        if ref.get("channel_id") and ref.get("message_id"):
            edited = await _edit_dm_message_safe(bot, ref, content, None, files)
        if not edited:
            await _dm_user_safe(bot, player_id, content, None, files)


async def _edit_pickban_dms_phase_done(bot: Any, attack: Any, strings: dict[str, str]) -> None:
    """Edit both DMs into the « sabotage phase over » notice (v4)."""
    pickban: dict[str, Any] = dict(attack.pickban or {})
    content = strings["sab_phase_done"].format(map=attack.map_key)
    for side, player_id in (
        ("attacker", str(attack.attacker_lord_id)),
        ("defender", str(attack.defender_lord_id or "")),
    ):
        if not player_id or (side == "defender" and attack.ai_defender):
            continue
        ref = pickban.get(f"{side}_dm") or {}
        edited = False
        if ref.get("channel_id") and ref.get("message_id"):
            edited = await _edit_dm_message_safe(bot, ref, content, None)
        if not edited:
            await _dm_user_safe(bot, player_id, content, None)


async def _sweep_sabotage_dms_on_open_safe(bot: Any, attack: Any, strings: dict[str, str]) -> None:
    """Clear the sabotage DMs' buttons when the meeting opens (best effort).

    dm-expiration-v1 (Drasah 27/10, appliqué 28/10): once the deadline
    is past, the MP buttons must DISAPPEAR — the UI mirrors the dead
    phase, not just a server-side rejection. The settled flow already
    edits both DMs (the ⚔️ Passer callback); the T-defaulted flow
    (a camp never settled — ``score_due`` flips ``sabotage_resolved``
    service-side) left the 🗡️/⚔️ buttons rendered forever. Called from
    the tick at every meeting open: the edit is idempotent (the
    settled flow re-edits the same content), it only ever REMOVES
    components — a late sweep never revives a dead phase.
    """
    await _edit_pickban_dms_phase_done(bot, attack, strings)


async def _open_meeting_and_room_safe(bot: Any, attack: Any, wiring: Any, strings: dict[str, str], locale: str) -> None:
    """Open the meeting + the battle room once both camps settled (v4).

    Drasah 18/10 v4: the vocal is created the moment the two players
    finished the sabotage phase (before T) — the room only carries the
    URL / Prêt / result buttons.
    """
    if wiring.attacks_service is None:
        return
    try:
        fresh = await wiring.attacks_service.open_meeting_now(str(attack.id))
    except Exception:
        logger.warning("KINGDOM ATTACK: early meeting open failed", exc_info=True)
        return
    if fresh is None:
        return
    for guild in list(getattr(bot, "guilds", [])):
        await _open_battle_room(bot, guild, fresh, wiring.kingdoms_service, locale)


async def _resolve_sabotage_key(wiring: Any, key: str) -> tuple[str, str]:
    """Resolve a sabotage key back to (attack_id, player_id) — best effort."""
    if wiring.attacks_service is None:
        return ("", "")
    try:
        for attack in await wiring.attacks_service.attacks():
            if str(getattr(attack, "state", "")) != "defended":
                continue
            pickban = dict(attack.pickban or {})
            if not pickban.get("resolved_pickban") or pickban.get("sabotage_resolved"):
                continue
            for player_id in _score_players(attack):
                if _ready_key(str(attack.id), player_id) == key:
                    return (str(attack.id), player_id)
    except Exception:
        logger.warning("KINGDOM ATTACK: sabotage key resolution failed", exc_info=True)
    return ("", "")


class KingdomSabotageOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:sab:open:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof 🗡️ Sabotage / Faucon button of the draft DM (v4)."""

    def __init__(self, attack_id: str, player_id: str, label: str, *, available: bool = True) -> None:
        key = _ready_key(attack_id, player_id)
        # sabotage-dm-fresh-v1 (Drasah 30/10): the 🗡️ button carries the
        # availability — blue (Primary) while the side can still
        # sabotage, red (Danger) once the stock is dry or the 2-per-
        # attack cap is reached.
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:sab:open:{key}",
                style=(
                    discord.ButtonStyle.primary
                    if available
                    else discord.ButtonStyle.danger
                ),
            )
        )
        self.attack_id = attack_id
        self.player_id = player_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomSabotageOpenButton:
        """Resolve the DM key back to an attack and player (restart-safe)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        label = _strings(_locale(interaction))["sab_open_button"]
        wiring = _wiring()
        attack_id, player_id = await _resolve_sabotage_key(wiring, match["key"])
        return cls(attack_id, player_id, label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral sabotage menu of this player (or the notice)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await _unique_notice(interaction, strings["no_service"])
            return
        try:
            attack = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id)
        except Exception:
            await _unique_notice(interaction, strings["no_service"])
            return
        if attack.is_over or (attack.pickban or {}).get("sabotage_resolved"):
            await _unique_notice(interaction, strings["sab_over"])
            return
        user_id = str(interaction.user.id)
        side = _room_side_of(attack, user_id)
        if side == "":
            await _unique_notice(interaction, strings["sab_not_player"])
            return
        view = await _sabotage_menu_view(wiring, attack, side, strings)
        if view is None or not list(view.children):
            # Drasah 18/10 v4: no techno bought and no usable Faucon.
            await _unique_notice(interaction, strings["sab_no_resources"])
            return
        # sabotage-ux-v1 (Drasah 22/10): ONE ephemeral panel per player,
        # edited in place at every action — the panel carries the X/2
        # récap, the removed civs and the stock line, and self-destructs
        # at the 15-minute webhook token limit.
        # sabotage-visuel-v1 (Drasah 29/10, PATCH 1.0 item 2): the panel
        # rides the enemy-picks MOSAIC (❌ crossed sabotages) and its
        # opening closes the stale refusal window of the fenêtre unique
        # registry — the panel itself keeps its own lifecycle
        # (_SABOTAGE_PANELS) and is never registered.
        await _close_stale_notice(user_id)
        content, _menu, file = await _sabotage_panel_content(wiring, attack, side, strings)
        panel_kwargs: dict[str, Any] = {"view": view}
        if file is not None:
            panel_kwargs["files"] = [file]
        await interaction.response.send_message(content, ephemeral=True, **panel_kwargs)
        key = (str(attack.id), user_id)
        _SABOTAGE_PANELS.pop(key, None)
        _SABOTAGE_PANELS[key] = {"interaction": interaction}
        _arm_sabotage_panel_timer(key)


class KingdomSabotagePassButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:sab:pass:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof ⚔️ Passer button of the draft DM (v4).

    Settles the clicking side; when BOTH sides settled (or the AI
    defender is the second side), both DMs are edited into the
    « phase over — join the voice room at the RDV » notice.
    """

    def __init__(self, attack_id: str, player_id: str, label: str) -> None:
        key = _ready_key(attack_id, player_id)
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:sab:pass:{key}",
                style=discord.ButtonStyle.success,
            )
        )
        self.attack_id = attack_id
        self.player_id = player_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomSabotagePassButton:
        """Resolve the DM key back to an attack and player (restart-safe)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        label = _strings(_locale(interaction))["sab_pass_button"]
        wiring = _wiring()
        attack_id, player_id = await _resolve_sabotage_key(wiring, match["key"])
        return cls(attack_id, player_id, label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Settle the side; the last one closes the phase (both DMs edited)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await _unique_notice(interaction, strings["no_service"])
            return
        try:
            fresh = await wiring.attacks_service.sabotage_settle(self.attack_id, self.player_id)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await _unique_notice(interaction, strings["failed"].format(message=message))
            return
        # sabotage-ux-v1 (Drasah 22/10): the Passer click closes the
        # sabotage panel — the player is done with the phase, the panel
        # goes away at once (best effort).
        await _delete_sabotage_panel((self.attack_id, self.player_id))
        if (fresh.pickban or {}).get("sabotage_resolved"):
            await _edit_pickban_dms_phase_done(interaction.client, fresh, strings)
            await _unique_notice(
                interaction, strings["sab_phase_done"].format(map=fresh.map_key)
            )
            # Drasah 18/10 v4: both camps answered — the vocal opens
            # at once, BEFORE T (the room carries URL/Prêt/result).
            await _open_meeting_and_room_safe(interaction.client, fresh, wiring, strings, _locale(interaction))
            return
        await _unique_notice(interaction, strings["sab_settled"])


async def _send_pickban_dm(bot: Any, attack: Any, kingdoms_service: Any, locale: str) -> None:
    """Send the pick & ban DM to each human player (Drasah 18/10 v2)."""
    strings = _strings(locale)
    wiring = None
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
    except Exception:
        wiring = None
    # chef-mercenaire-v1 T2c (Drasah 25/10): each player's DM hand
    # carries HIS live mercenary civilization — the adversary line
    # stays the plain kingdom hand (the copy belongs to the holder).
    hand = await _pickban_hand_safe(
        wiring, kingdoms_service, attack.attacker_kingdom_id, str(attack.attacker_lord_id)
    )
    adversary = await _pickban_hand_safe(
        wiring, kingdoms_service, attack.defender_kingdom_id, str(attack.defender_lord_id)
    )
    adversary_hand = await _hand_of(kingdoms_service, attack.defender_kingdom_id)
    theirs = await _hand_of(kingdoms_service, attack.attacker_kingdom_id)
    view = KingdomPickBanView(
        str(attack.id),
        str(attack.attacker_lord_id),
        "attacker",
        hand,
        locale,
    )
    # sabotage-ux-v1 (Drasah 22/10): the draft DM carries the stock
    # line — sabotage-consommable-v1: one charge is spent per snipe,
    # the Faucon flies 48h.
    stock_attacker = await _sabotage_stock_line(wiring, attack, "attacker", strings) if wiring else ""
    message = await _dm_user_safe(
        bot,
        str(attack.attacker_lord_id),
        strings["pickban_dm"].format(
            map=attack.map_key,
            count=min(3, len(hand)) or 1,
            deadline=_fmt_paris(attack.expires_at),
            hand=", ".join(hand) or "—",
            theirs=", ".join(adversary_hand) or "—",
        )
        + (f"\n\n{stock_attacker}" if stock_attacker else ""),
        view,
    )
    if message is not None:
        await _record_pickban_dm_safe(str(attack.id), str(attack.attacker_lord_id), message)
    if not attack.ai_defender and attack.defender_lord_id:
        view = KingdomPickBanView(
            str(attack.id),
            str(attack.defender_lord_id),
            "defender",
            adversary,
            locale,
        )
        stock_defender = await _sabotage_stock_line(wiring, attack, "defender", strings) if wiring else ""
        message = await _dm_user_safe(
            bot,
            str(attack.defender_lord_id),
            strings["pickban_dm"].format(
                map=attack.map_key,
                count=min(3, len(adversary)) or 1,
                deadline=_fmt_paris(attack.expires_at),
                hand=", ".join(adversary) or "—",
                theirs=", ".join(theirs) or "—",
            )
            + (f"\n\n{stock_defender}" if stock_defender else ""),
            view,
        )
        if message is not None:
            await _record_pickban_dm_safe(str(attack.id), str(attack.defender_lord_id), message)


async def _record_pickban_dm_safe(attack_id: str, player_id: str, message: Any) -> None:
    """Store the DM refs so the resolution can edit the SAME message."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return
    try:
        channel = getattr(message, "channel", None)
        await wiring.attacks_service.record_pickban_dm(
            attack_id, player_id, int(getattr(channel, "id", 0)), int(getattr(message, "id", 0))
        )
    except Exception:
        logger.warning("KINGDOM ATTACK: pick & ban DM refs not stored", exc_info=True)


async def _pickban_hand_safe(
    wiring: Any,
    kingdoms_service: Any,
    kingdom_id: str | None,
    player_id: str,
) -> list[str]:
    """Return one player's draft hand — his mercenary civ included (T2c)."""
    attacks_service = getattr(wiring, "attacks_service", None)
    if attacks_service is not None and hasattr(attacks_service, "pickban_hand"):
        try:
            return await attacks_service.pickban_hand(kingdom_id or "", player_id)
        except Exception:
            logger.warning("KINGDOM ATTACK: pickban hand read failed", exc_info=True)
    return await _hand_of(kingdoms_service, kingdom_id)


async def _hand_of(kingdoms_service: Any, kingdom_id: str | None) -> list[str]:
    """Return the kingdom's civilizations (its Alliances hand, D77)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _kingdom_by_id_safe

    kingdom = await _kingdom_by_id_safe(kingdoms_service, kingdom_id or "")
    return list(getattr(kingdom, "civilizations", None) or []) if kingdom else []


async def _dm_user_safe(
    bot: Any,
    user_id: str,
    content: str,
    view: discord.ui.View | None = None,
    files: list[discord.File] | None = None,
) -> Any:
    """Best-effort DM to one player — returns the message (or None)."""
    try:
        user = getattr(bot, "get_user", lambda _id: None)(user_id)
        if user is None:
            user = await bot.fetch_user(int(user_id))
        if user is not None:
            # ``files`` only rides along when present: a caller-less
            # signature (tests, DM bridges) must keep working.
            if files:
                return await user.send(content, view=view, files=files)
            return await user.send(content, view=view)
    except Exception:
        # The message stays generic on purpose: _dm_user_safe carries
        # BOTH the pick & ban DMs and the RDV DMs (live beta 18/10: the
        # log said « pick & ban DM failed » for a dying RDV DM — the
        # 50035 custom_id rejection read like a draft bug).
        logger.warning("KINGDOM ATTACK: player DM failed (%s)", user_id, exc_info=True)
    return None


# ----------------------------------------------------------------------
# The scheduler entry (restart-safe through the service flags)
# ----------------------------------------------------------------------
async def _guild_locale(bot: Any, guild_id: str) -> str:
    """Return the guild's locale through the logs service (FR default)."""
    logs = getattr(bot, "logs_service", None)
    if logs is None:
        return "fr"
    try:
        return str(await logs.get_locale(guild_id))
    except Exception:
        return "fr"


async def run_attack_phases_safe(bot: Any) -> None:
    """Drive the D77 phases for every guild: reveal, DMs, rooms, forfeits.

    attaques-panel-spam (Drasah 15/10): the panel is redeployed ONLY
    when a phase was actually played this tick — an idle tick touches
    nothing (no delete, no send, no edit). The mutation hooks keep
    their own refresh: they are the other legitimate content change.

    Drasah 18/10 v2: the score_due tick opens the BATTLE ROOM (voice +
    role + text-in-voice civs) instead of sending RDV DMs; the lone
    result declarations confirm through score_confirm_due and the
    resolved rooms are cleaned up by the tick.
    """
    attacks_service = getattr(bot, "kingdoms_attacks_service", None)
    kingdoms_service = getattr(bot, "kingdoms_service", None)
    if attacks_service is None:
        return
    _register_voice_listener(bot)
    for guild in list(getattr(bot, "guilds", [])):
        locale = await _guild_locale(bot, str(guild.id))
        strings = _strings(locale)
        # restructuration-serveur-v1 b: every announcement routes to the
        # attack's OWN salon (no more central Attaques salon).
        dirty = await _run_phases_announce_safe(bot, guild, attacks_service, kingdoms_service, strings, locale)
        # The resolved meetings free their battle room (channel +
        # role) AND their per-attack salon — whatever the resolution
        # (declared, forfeit, admin, cancelled).
        dirty += await _cleanup_battle_rooms_safe(bot, guild, attacks_service)
        if dirty:
            await deploy_attack_panel(_declare_channel(guild), locale, kingdoms_service, attacks_service)


async def _run_phases_announce_safe(
    bot: Any,
    guild: Any,
    attacks_service: Any,
    kingdoms_service: Any,
    strings: dict[str, str],
    locale: str,
) -> int:
    """Play the per-attack-salon phases of one guild — never raise."""
    dirty = 0
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        dirty += await _run_plan_events_safe(
            bot, guild, attacks_service, kingdoms_service, strings, _wiring()
        )
        dirty += await _run_reveals_safe(guild, attacks_service, kingdoms_service, strings)
        pickbans = await attacks_service.pick_ban_due()
        for attack in pickbans:
            await _send_pickban_dm(bot, attack, kingdoms_service, locale)
        dirty += len(pickbans)
        # Drasah 22/10 battle-ai-rdv-v1: against the AI defender the
        # draft resolves on the tick — the AI validates its 3 random
        # picks at ``ai_validate_at`` (30-90 s) so the attacker cannot
        # tell instantly that the adversary is a bot; once both sides
        # validated, the reveal DMs go out exactly like a human draft.
        ai_drafts = await attacks_service.ai_draft_due()
        for attack in ai_drafts:
            await _edit_pickban_dms_reveal(bot, attack, kingdoms_service, strings)
        dirty += len(ai_drafts)
        # timeline-timers-v1 (Drasah 23/10): a defaulted draft (a side
        # never ranked its civs at T) still owed the players their
        # reveal — without this the draft DMs stayed stuck on
        # « waiting for the adversary » forever.
        defaults = await attacks_service.default_reveal_due()
        for attack in defaults:
            await _edit_pickban_dms_default_reveal(bot, attack, kingdoms_service, strings)
        dirty += len(defaults)
        # battle-ai-rdv-v1: the AI « clicks Prêt » at ``ai_ready_at``
        # (30-90 s after the attacker's Prêt) — the flip opens the
        # same scheduled event + footer refresh as a human ready pair.
        ai_readies = await attacks_service.ai_ready_due()
        for attack in ai_readies:
            await _create_room_event_safe(bot, attack, strings)
            await _refresh_room_message_safe(bot, attack, locale)
        dirty += len(ai_readies)
        # Drasah 18/10 v2: at T the meeting opens a BATTLE ROOM
        # (dedicated role, voice channel under « Champs de bataille »,
        # the shuffled civs in its text channel) and DMs the link to
        # both players.
        scores = await attacks_service.score_due()
        for attack in scores:
            # dm-expiration-v1 (Drasah 27/10): a meeting that opens
            # on the T-default (a camp never settled its sabotage)
            # must not leave the Saboter/Passer buttons rendered in
            # the DMs — the sweep re-edits both DMs WITHOUT their
            # view (idempotent in the settled flow, which already
            # carries the same « phase over » content).
            await _sweep_sabotage_dms_on_open_safe(bot, attack, strings)
            await _open_battle_room(bot, guild, attack, kingdoms_service, locale)
        dirty += len(scores)
        # A lone result declaration holds score_confirm_minutes, then
        # the declared result applies (fait foi) — announced like any
        # resolution.
        confirmed = await attacks_service.score_confirm_due()
        for attack in confirmed:
            winner = await _kingdom_name(kingdoms_service, attack.winner_kingdom_id)
            await _attack_salon_send_safe(
                guild,
                attack,
                strings["score_resolved"].format(
                    winner=winner, map=attack.map_key, game=(attack.score or {}).get("game_id") or "—"
                ),
            )
            # sabotage-ux-v1 (Drasah 22/10): the room ping wears the
            # winner too — the players see the outcome at a glance.
            await _stamp_room_ping_winner_safe(bot, attack, winner, strings)
            await _announce_conquest_safe(bot, kingdoms_service, attack, locale)
        dirty += len(confirmed)
        # attaques-phases-v2: the 3h no-result reminder (DM only, no forfeit).
        reminders = await attacks_service.score_reminder_due()
        for attack in reminders:
            await _send_score_reminder_dm(bot, attack, locale)
        dirty += len(reminders)
        # bug n°2 (Check 59, Drasah 16/10): 15h without a declared
        # result — the dispute auto-opens, the admins arbitrate; no
        # meeting hangs forever.
        disputes = await attacks_service.dispute_due()
        for attack in disputes:
            await _attack_salon_send_safe(guild, attack, strings["auto_dispute"].format(map=attack.map_key))
        dirty += len(disputes)
        dirty += await _run_forfeits_safe(guild, attacks_service, kingdoms_service, strings)
        dirty += await _run_noshows_safe(bot, guild, attacks_service, kingdoms_service, strings)
    except Exception:
        logger.warning("KINGDOM ATTACK: phase run failed", exc_info=True)
    return dirty


async def _run_reveals_safe(guild: Any, attacks_service: Any, kingdoms_service: Any, strings: dict[str, str]) -> int:
    """Announce the T-15 reveals in the attack's own salon (b).

    restructuration-serveur-v1 b (Drasah 19/10): the reveal names the
    DEFENDER at last — « ⚔️ Bataille en vue (défenseur : X) » when a
    lord answered, « 🏳️ Assaut irrésistible » when nobody did (the AI
    fallback still announces at the meeting time). The cover (if any)
    drops on the same beat.
    """
    from kingdoms.mods.kingdoms.models import AttackState

    played = 0
    for attack in await attacks_service.reveal_due():
        played += 1
        if attack.cover_lord_id:
            # bluff-externe-v1 (Drasah 29/10): the reveal puts
            # EVERYTHING clear — the real attacker AND his kingdom,
            # under the cover of whom and of which kingdom.
            cover_note = await _attack_salon_send_safe(
                guild,
                attack,
                strings["reveal_cover"].format(
                    map=attack.map_key,
                    attacker=await _lord_name(kingdoms_service, attack.attacker_lord_id),
                    attacker_kingdom=await _kingdom_name(
                        kingdoms_service, attack.attacker_kingdom_id
                    ),
                    cover=await _lord_name(kingdoms_service, attack.cover_lord_id),
                    cover_kingdom=await _lord_kingdom_name(
                        kingdoms_service, attack.cover_lord_id
                    )
                    or "—",
                ),
            )
            await _record_reveal_message_safe(attacks_service, attack, cover_note)
        if attack.ai_defender:
            note = strings["reveal_no_defense"]
        elif getattr(attack, "state", None) is AttackState.DEFENDED:
            note = strings["reveal_battle"].format(defender=await _lord_name(kingdoms_service, attack.defender_lord_id))
        else:
            note = strings["reveal_no_defense"]
        sent = await _attack_salon_send_safe(guild, attack, note)
        await _record_reveal_message_safe(attacks_service, attack, sent)
    return played


async def _record_reveal_message_safe(attacks_service: Any, attack: Any, message: Any) -> None:
    """Record one reveal note ref (salon-nettoyage-v1, best effort).

    Drasah 29/10: the T-15 reveal notes (« ⚔️ Bataille en vue … ») are
    swept when the battle room opens its scoreboard — the refs live in
    ``score["reveal_message_refs"]`` (restart-safe)."""
    if message is None:
        return
    try:
        channel = getattr(message, "channel", None)
        await attacks_service.record_attack_reveal_message(
            str(attack.id), int(getattr(channel, "id", 0)), int(message.id)
        )
    except Exception:
        logger.warning("KINGDOM ATTACK: reveal message ref not stored", exc_info=True)


async def _run_forfeits_safe(guild: Any, attacks_service: Any, kingdoms_service: Any, strings: dict[str, str]) -> int:
    """Announce the meeting-time forfeits of the expired attacks.

    Returns the number of attacks resolved (attaques-panel-spam: any
    resolution changes the panel content, announced or not).
    """
    played = 0
    for attack in await attacks_service.expire_stale():
        played += 1
        if attack.ai_defender:
            # The [T-15, T] window closed with no defender — the bot
            # AI takes the defense at the meeting time (Drasah 17/10).
            await _attack_salon_send_safe(
                guild,
                attack,
                strings["reveal_ai"].format(
                    map=attack.map_key,
                    kingdom=await _kingdom_name(kingdoms_service, attack.defender_kingdom_id),
                    level=attack.effects.get("ai_level", 1) if attack.effects else 1,
                ),
            )
        side = (attack.pickban or {}).get("forfeit_side")
        if not side:
            continue
        kingdom = await _kingdom_name(kingdoms_service, attack.winner_kingdom_id)
        if side == "both":
            await _attack_salon_send_safe(
                guild, attack, strings["forfeit_both"].format(map=attack.map_key, kingdom=kingdom)
            )
        else:
            await _attack_salon_send_safe(
                guild,
                attack,
                strings["forfeit_side"].format(map=attack.map_key, side=strings[f"side_{side}"], kingdom=kingdom),
            )
    return played


# ----------------------------------------------------------------------
# The battle room (Drasah 18/10 v2 — the RDV lives in a voice room)
# ----------------------------------------------------------------------
_VOICE_LISTENER_REGISTERED = False


def _register_voice_listener(bot: Any) -> None:
    """Register the voice-presence listener once (restart-safe flag)."""
    global _VOICE_LISTENER_REGISTERED
    if _VOICE_LISTENER_REGISTERED:
        return
    add_listener = getattr(bot, "add_listener", None)
    if add_listener is None:
        return

    async def _on_voice_state_update(member: Any, before: Any, after: Any) -> None:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        channel = getattr(after, "channel", None)
        if channel is None or getattr(member, "bot", False):
            return
        wiring = _wiring()
        if wiring.attacks_service is None:
            return
        try:
            await wiring.attacks_service.record_voice_join(
                int(getattr(channel, "id", 0)), str(getattr(member, "id", ""))
            )
        except Exception:
            logger.warning("KINGDOM ATTACK: voice join record failed", exc_info=True)

    try:
        add_listener(_on_voice_state_update, "on_voice_state_update")
        _VOICE_LISTENER_REGISTERED = True
    except Exception:
        logger.warning("KINGDOM ATTACK: voice listener registration failed", exc_info=True)


def _shuffled_civs(picks: Any) -> list[str]:
    """Shuffle one side's 3 picks — the ranking must stay hidden."""
    import random as _random

    pool = [str(civ) for civ in (picks or [])]
    _random.shuffle(pool)
    return pool


def _reveal_civs(seed: str, picks: Any) -> list[str]:
    """Seeded shuffle of one side's 3 picks (sabotage-dm-fresh-v1).

    The reveal order must stay STABLE across the initial reveal and
    every post-pick DM refresh — a fresh random shuffle would reorder
    the mosaic at each snipe. The seed is private to the bot, so the
    pick ranking stays hidden.
    """
    import random as _random

    pool = [str(civ) for civ in (picks or [])]
    _random.Random(f"reveal:{seed}").shuffle(pool)
    return pool


def _final_civ(pickban: dict[str, Any], side: str) -> str:
    """Return the FINAL civilization of one side (post-sabotage, v4).

    The played civ is the FIRST ranked pick the enemy sabotages did
    not strike — the ranking itself stays secret forever.
    """
    picks = [str(civ) for civ in (pickban.get(f"{side}_picks") or [])]
    enemy = "defender" if side == "attacker" else "attacker"
    struck = [str(civ) for civ in (pickban.get(f"{enemy}_sabotages") or [])]
    return next((civ for civ in picks if civ not in struck), "")


async def _draft_sheet_embed(attack: Any, kingdoms_service: Any, strings: dict[str, str]) -> discord.Embed:
    """blasons-affichage-v1 (Drasah 28/10): the 📜 « Voir la draft » sheet.

    Drasah's two-column layout: each side's 3 picks in RANDOM order
    (the ranking itself stays secret forever, like the reveal DMs)
    under its player's name — 1️⃣ 2️⃣ 3️⃣ numbering, the sabotaged
    picks struck through: ~~Mongols~~ ❌.
    """
    pickban = dict(attack.pickban or {})
    attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
    if attack.ai_defender:
        defender = str(strings["defender_ai"])
    else:
        defender = await _lord_name(kingdoms_service, attack.defender_lord_id or "")

    async def _side_picks(side: str) -> str:
        picks = [str(civ) for civ in (pickban.get(f"{side}_picks") or [])]
        enemy = "defender" if side == "attacker" else "attacker"
        struck = {str(civ) for civ in (pickban.get(f"{enemy}_sabotages") or [])}
        shuffled = _shuffled_civs(picks)
        marks = ("1️⃣", "2️⃣", "3️⃣")
        return (
            "\n".join(
                f"{marks[i]} {f'~~{civ}~~ ❌' if civ in struck else civ}"
                for i, civ in enumerate(shuffled[: len(marks)])
            )
            or "—"
        )

    embed = discord.Embed(
        title=str(strings["draft_sheet"]).format(map=attack.map_key),
        description=str(strings["draft_sheet_help"]),
    )
    embed.add_field(name=f"⚔️ {attacker}", value=await _side_picks("attacker"), inline=True)
    embed.add_field(name=f"🛡️ {defender}", value=await _side_picks("defender"), inline=True)
    return embed


def _battle_room_overwrites(guild: Any, role: Any) -> dict[Any, discord.PermissionOverwrite]:
    """Build the battle-room channel overwrites (players' role only).

    Check 75 live: the @everyone deny locked the BOT itself out of the
    channel it had just created — the room message died with 403 50001
    Missing Access and the whole flow (roles, DMs, refs) collapsed
    with it. The bot carries its own overwrites.
    """
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        role: discord.PermissionOverwrite(connect=True, view_channel=True),
    }
    me = getattr(guild, "me", None)
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            send_messages=True,
            read_message_history=True,
        )
    everyone = getattr(guild, "default_role", None)
    if everyone is not None:
        overwrites[everyone] = discord.PermissionOverwrite(connect=False, view_channel=False)
    return overwrites


async def _grant_battle_room_role_safe(guild: Any, role: Any, player_id: str) -> None:
    """Give the battle-room role to one player (best effort).

    The member cache is often cold — ``fetch_member`` is the fallback.
    """
    member = None
    try:
        member = guild.get_member(int(player_id))
    except (TypeError, ValueError):
        member = None
    if member is None:
        try:
            member = await guild.fetch_member(int(player_id))
        except Exception:
            member = None
    if member is not None:
        try:
            await member.add_roles(role)
        except Exception:
            logger.warning("KINGDOM ATTACK: battle room role grant failed", exc_info=True)


async def _room_view(
    attack: Any,
    kingdoms_service: Any,
    strings: dict[str, str],
    *,
    id_pending: bool = False,
) -> discord.ui.View:
    """Build the battle-room button set (battle-room-id-v1/v2).

    battle-room-id-v2 (Drasah 22/10): ONE row, a real split —
    ``⚔️ Prêt | 🔗 ID | 🏆 Déclarer | Prêt 🛡️`` (attacker left,
    defender right). The Prêt buttons appear once the ID is set (the
    clicker must be that side's player), 🏆 Déclarer le vainqueur
    once BOTH players are Prêt. ``id_pending`` locks the ID entry
    while a player types (anti double-saisie of conflicting IDs).
    """
    score = dict(attack.score or {})
    view = discord.ui.View(timeout=None)
    # blasons-vs-v1 (Drasah 28/10): the 📜 draft button sits on its
    # OWN row above the ID/Prêt controls — the ephemeral sheet
    # (single window) answers each side's 3 civs, snipes barred.
    view.add_item(KingdomDraftViewButton(str(attack.id), strings["draft_button"]))
    url_set = bool(str(attack.lobby_url or "").strip())
    if url_set:
        attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
        view.add_item(
            KingdomRoomReadyButton(
                str(attack.id),
                strings["room_ready_attacker_button"].format(player=attacker),
                side="attacker",
            )
        )
    if id_pending:
        view.add_item(KingdomRoomUrlButton(str(attack.id), strings["room_id_pending_button"], disabled=True))
    else:
        label = strings["room_id_modify_button"] if url_set else strings["room_id_button"]
        style = discord.ButtonStyle.danger if url_set else discord.ButtonStyle.secondary
        view.add_item(KingdomRoomUrlButton(str(attack.id), label, style=style))
    if score.get("all_ready"):
        view.add_item(KingdomRoomResultButton(str(attack.id), strings["result_button"]))
    if url_set and attack.defender_lord_id and not attack.ai_defender:
        defender = await _lord_name(kingdoms_service, attack.defender_lord_id)
        view.add_item(
            KingdomRoomReadyButton(
                str(attack.id),
                strings["room_ready_defender_button"].format(player=defender),
                side="defender",
            )
        )
    return view


_ROOM_SCORE_WIDTH = 46


def _score_center(text: str) -> str:
    """Center a neutral line of the scoreboard (monospace block)."""
    text = text[:_ROOM_SCORE_WIDTH]
    pad = _ROOM_SCORE_WIDTH - len(text)
    return " " * (pad // 2) + text + " " * (pad - pad // 2)


def _score_row(left: str, right: str, vs: str) -> str:
    """One symmetric scoreboard row — left field, centered VS, right field."""
    half = _ROOM_SCORE_WIDTH // 2
    left = left[: half - 2]
    right = right[: half - 2]
    vs_field = f" {vs} ".center(4) if vs else " " * 4
    return left.ljust(half - 2) + vs_field + right.rjust(half - 2)


async def _room_body_text(attack: Any, kingdoms_service: Any, strings: dict[str, str]) -> str:
    """Build the battle-room scoreboard (battle-room-id-v1, Drasah 22/10).

    Symmetric view: the neutral infos (map, game ID) centered on top,
    the attacker on the LEFT and the defender on the RIGHT, each with
    their OWN Prêt state. A monospace block
    keeps the alignment stable whatever the name lengths — a Prêt
    click edits the player's half in place, never a new message.
    blasons-affichage-v1 (Drasah 28/10): the civs line and the text
    VS are GONE — the painted banner above already carries the names
    and the civilizations, the block keeps the pseudos + Prêt states.
    """
    score = dict(attack.score or {})
    ready = {str(player) for player in (score.get("ready") or [])}
    url = str(attack.lobby_url or "").strip()
    attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
    defender = await _lord_name(kingdoms_service, attack.defender_lord_id)

    def _status(player_id: str | None) -> str:
        if not url:
            # attaque-ergonomie-v1 (Drasah 28/10): with no game ID
            # yet the sides stay EMPTY — the single centered « 🔗
            # en attente de l'ID » line below says it all (no
            # duplicated 🆔 reminder).
            return ""
        if player_id and str(player_id) in ready:
            return strings["room_side_ready"]
        return strings["room_side_wait"]

    attacker_status = _status(attack.attacker_lord_id)
    if attack.ai_defender:
        # battle-ai-rdv-v1 (Drasah 22/10): the RAW level (1-5, Traquenard
        # aware) rides the scoreboard — never an invented label.
        level = int((attack.effects or {}).get("ai_level", 1) or 1)
        defender_status = strings["room_side_ai"].format(level=level)
    else:
        defender_status = _status(attack.defender_lord_id)
    id_line = strings["room_scoreboard_id"].format(id=url) if url else strings["room_side_none"]
    rows = [
        _score_center(strings["room_scoreboard_map"].format(map=attack.map_key)),
        _score_center(id_line),
        "",
        # blasons-affichage-v1 (Drasah 28/10, doublon retiré): the
        # painted banner above the scoreboard already carries the
        # names AND the civs — the text block keeps the pseudos and
        # the Prêt states only, no text VS, no civs line.
        _score_row(attacker, defender, ""),
        _score_row(attacker_status, defender_status, ""),
    ]
    body = "```\n" + "\n".join(rows) + "\n```"
    if attack.ai_defender:
        # battle-ai-rdv-v1 (Drasah 22/10, version courte validée): the
        # multiplayer-ID disclaimer sits right under the scoreboard.
        level = int((attack.effects or {}).get("ai_level", 1) or 1)
        body += "\n" + strings["room_ai_disclaimer"].format(level=level)
    if score.get("all_ready"):
        body += "\n" + strings["room_scoreboard_footer"]
    return body


async def _refresh_room_message_safe(bot: Any, attack: Any, locale: str, *, id_pending: bool = False) -> None:
    """Best-effort refresh of the room's single message (ID → Prêt → 🏆)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    score = dict(attack.score or {})
    # restructuration-serveur-v1 b: the centralized meeting message
    # lives in the ATTACK SALON (meeting_channel_id/meeting_message_id)
    # — the voice-room refs (room_channel_id) stay untouched, the voice
    # presence tracking maps its joins through them.
    channel_id = int(score.get("meeting_channel_id") or 0)
    message_id = int(score.get("meeting_message_id") or 0)
    if not channel_id or not message_id or wiring.kingdoms_service is None:
        return
    strings = _strings(locale)
    for guild in list(getattr(bot, "guilds", [])):
        channel = getattr(guild, "get_channel", lambda _id: None)(channel_id)
        if channel is None:
            continue
        message = next(
            (item for item in getattr(channel, "messages", []) if int(getattr(item, "id", 0)) == message_id),
            None,
        )
        try:
            if message is None and hasattr(channel, "get_partial_message"):
                message = channel.get_partial_message(message_id)
            if message is not None:
                await message.edit(
                    content=await _room_body_text(attack, wiring.kingdoms_service, strings),
                    view=await _room_view(attack, wiring.kingdoms_service, strings, id_pending=id_pending),
                )
        except Exception:
            logger.warning("KINGDOM ATTACK: battle room refresh failed", exc_info=True)


async def _announce_conquest_safe(bot: Any, kingdoms_service: Any, attack: Any, locale: str) -> None:
    """Post the conquest recap in Géopolitique + its detail thread.

    Drasah 18/10 v5: every resolved meeting tells its story in the
    « Géopolitique » salon — the title announces who took (or kept)
    the map, a dedicated thread carries the details (players, civs,
    sabotage, embuscade, game ID, broken sacred bond).
    """
    strings = _strings(locale)
    winner_id = str(getattr(attack, "winner_kingdom_id", "") or "")
    if not winner_id:
        return
    attacker_won = winner_id == str(attack.attacker_kingdom_id)
    winner = await _kingdom_name(kingdoms_service, winner_id)
    title_key = "conquest_title" if attacker_won else "conquest_defense_title"
    thread_key = "conquest_thread" if attacker_won else "conquest_thread_defense"
    title = strings[title_key].format(winner=winner, map=attack.map_key)
    detail = await _conquest_detail(kingdoms_service, attack, strings, attacker_won)
    for guild in list(getattr(bot, "guilds", [])):
        channel = _geopolitics_channel(guild)
        if channel is None:
            continue
        try:
            message = await channel.send(title)
        except Exception:
            logger.warning("KINGDOM ATTACK: conquest recap post failed", exc_info=True)
            continue
        try:
            thread = await channel.create_thread(name=strings[thread_key][:100], message=message)
            await thread.send(detail)
        except Exception:
            logger.warning("KINGDOM ATTACK: conquest thread failed — flat fallback", exc_info=True)
            try:
                await channel.send(detail)
            except Exception:
                logger.warning("KINGDOM ATTACK: conquest detail post failed", exc_info=True)


async def _conquest_detail(kingdoms_service: Any, attack: Any, strings: dict[str, str], attacker_won: bool) -> str:
    """Build the conquest recap's detail body (the thread content)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    pickban: dict[str, Any] = dict(attack.pickban or {})
    attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
    attacker_kingdom = await _kingdom_name(kingdoms_service, attack.attacker_kingdom_id)
    defender_kingdom = await _kingdom_name(kingdoms_service, attack.defender_kingdom_id)
    lines = [strings["conquest_attacker_line"].format(player=attacker, kingdom=attacker_kingdom)]
    if getattr(attack, "ai_defender", False):
        level = int((attack.effects or {}).get("ai_level") or 1)
        defender = defender_kingdom
        lines.append(strings["conquest_ai_defense"].format(kingdom=defender_kingdom, level=level))
    else:
        defender = await _lord_name(kingdoms_service, attack.defender_lord_id)
        lines.append(strings["conquest_defense_line"].format(player=defender, kingdom=defender_kingdom))
    lines.append(strings["conquest_civ_line"].format(player=attacker, civ=_final_civ(pickban, "attacker")))
    defender_civ = _final_civ(pickban, "defender")
    if defender_civ:
        lines.append(strings["conquest_civ_line"].format(player=defender, civ=defender_civ))
    if getattr(attack, "embuscade", False):
        lines.append(strings["conquest_embuscade_line"])
    for side in ("attacker", "defender"):
        sabotages = [str(civ) for civ in (pickban.get(f"{side}_sabotages") or [])]
        if sabotages:
            lines.append(
                strings["conquest_sabotages_line"].format(
                    side=strings[f"conquest_side_{side}"], civs=", ".join(sabotages)
                )
            )
    game = str((attack.score or {}).get("game_id") or attack.lobby_url or "")
    lines.append(strings["conquest_id_line"].format(game=game or strings["conquest_none"]))
    # The defeated player's sacred bond breaks when they were married.
    loser_id = str(attack.defender_lord_id) if attacker_won else str(attack.attacker_lord_id)
    if loser_id and not (attacker_won and getattr(attack, "ai_defender", False)):
        lord = await _lord_of(kingdoms_service, loser_id)
        married = str(getattr(lord, "married_civilization", "") or "") if lord else ""
        if married:
            lines.append(
                strings["conquest_married_lost"].format(
                    player=await _lord_name(kingdoms_service, loser_id), civ=married
                )
            )
    return "\n".join(lines)


async def _meeting_files(pickban: dict[str, Any]) -> list[discord.File]:
    """blasons-affichage-v1 (Drasah 28/10): the meeting message's banner.

    ONE composite image (the alliances-lisibilite-v1 mosaic style):
    [crest + nom de l'attaquant][badge VS][crest + nom du défenseur] —
    the names painted above the crests, Drasah's VS badge between
    them. An absent banner (nothing resolvable) degrades to the
    text alone, and the later content-only edits keep the attachment.
    """
    banner = await meeting_vs_mosaic_file(
        _final_civ(pickban, "attacker"), _final_civ(pickban, "defender")
    )
    return [banner] if banner is not None else []


async def _open_battle_room(bot: Any, guild: Any, attack: Any, kingdoms_service: Any, locale: str) -> None:
    """Open the battle room of a meeting (Drasah 18/10 v2).

    A dedicated role (given to both players), a voice channel under
    the provisioned « Champs de Bataille » category (user_limit 2,
    access locked to the role — battle-room-categorie-v1: the
    category is never created here) and a text-in-voice message
    carrying the SHUFFLED civilizations of each player + the
    persistent URL/result buttons. Both players get a DM pointing at
    the room.
    """
    strings = _strings(locale)
    score = attack.score or {}
    if score.get("room_channel_id"):
        return  # already opened (tick replay after a restart)
    try:
        attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
        defender = await _lord_name(kingdoms_service, attack.defender_lord_id)
        role = await guild.create_role(name=strings["room_role"].format(map=attack.map_key)[:100])
        # battle-room-categorie-v1 (Drasah 19/10): the room joins the
        # PROVISIONED « Champs de Bataille » category — never a new one
        # (the legacy exact match on « Champs de bataille » missed the
        # provisioned « Champs de Bataille » and created a duplicate).
        category = _battlefield_category(guild)
        if category is None:
            logger.warning("KINGDOM ATTACK: battlefield category not found — battle room created at the guild root")
        channel = await guild.create_voice_channel(
            _room_channel_name(guild, "⚔️", attacker, defender),
            category=category,
            user_limit=2,
            overwrites=_battle_room_overwrites(guild, role),
        )
        await record_battle_room_safe(attack, channel.id, role.id)
        # Drasah 18/10 v4: the sabotage phase happened BEFORE the RDV —
        # the room shows each player's FINAL civilization (the first
        # ranked pick the enemy sabotages did not strike).
        # Drasah 18/10 v5: the room lives on ONE message — 🔗 ID first,
        # ✅ Prêt once the ID is set, 🏆 once both players are Prêt.
        # Drasah 18/10 v5: the meeting lives on ONE message — 🔗 ID
        # first, ✅ Prêt once the ID is set, 🏆 once both players are Prêt.
        # restructuration-serveur-v1 b: that message now lives in the
        # ATTACK SALON (visible to all, buttons guarded to the two
        # players) — the voice channel keeps only the voice.
        salon = _attack_salon(guild, attack)
        if salon is None:
            # Best effort (Check 75 lesson): a missing salon never buries
            # the role grants, the room refs nor the room DMs.
            logger.warning("KINGDOM ATTACK: no attack salon for the meeting message")
        else:
            try:
                # blasons-affichage-v1 (Drasah 28/10): the meeting
                # message wears ONE composite banner — [crest + nom
                # attaquant][VS][crest + nom défenseur], the Alliances
                # mosaic style. The later content-only edits keep it
                # (attachments survive a Discord edit that does not
                # pass ``files``).
                pickban = dict(attack.pickban or {})
                message = await salon.send(
                    await _room_body_text(attack, kingdoms_service, strings),
                    view=await _room_view(attack, kingdoms_service, strings),
                    files=await _meeting_files(pickban),
                )
                await _record_meeting_message_safe(attack, int(salon.id), int(message.id))
                # battle-room-id-v2 (Drasah 22/10): the salon PINGS the
                # two players the moment the room opens — they cannot
                # miss the scoreboard. Never the AI (no ping-able user).
                if attack.defender_lord_id and not attack.ai_defender:
                    ping = await salon.send(
                        strings["room_ping_message"].format(
                            attacker=f"<@{attack.attacker_lord_id}>",
                            defender=f"<@{attack.defender_lord_id}>",
                        )
                    )
                else:
                    ping = await salon.send(
                        strings["room_ping_attacker_only"].format(attacker=f"<@{attack.attacker_lord_id}>")
                    )
                # sabotage-ux-v1 (Drasah 22/10): the ping is the message
                # the players watch — record its ref so every resolution
                # can stamp the 🏆 winner on it.
                await _record_ping_message_safe(attack, int(salon.id), int(ping.id))
            except Exception:
                logger.warning("KINGDOM ATTACK: meeting message post failed", exc_info=True)
            # salon-nettoyage-v1 (Drasah 29/10, live report 18:13 →
            # 18:21): the scoreboard just replaced the pre-room
            # messages — the declaration announce and the T-15 reveal
            # notes are swept so the salon reads in ONE glance.
            await _sweep_pre_room_announces_safe(guild, attack)
        for player_id in _score_players(attack):
            await _grant_battle_room_role_safe(guild, role, player_id)
        # room-dm-v2 (Drasah 28/10, sa maquette) : « Tiens-toi prêt » —
        # le vocal D'UNE part, le salon textuel de l'attaque D'AUTRE
        # part, puis la FENÊTRE DE TEMPS (la ligne T+5 du service :
        # ready_deadline, affichée à Paris) et les encouragements.
        # Un salon absent (config cassée, cas Check 75) ou une deadline
        # absente dégrade le DM en omettant la ligne — jamais en erreur.
        salon_mention = f"<#{salon.id}>" if salon is not None else ""
        deadline = int((attack.score or {}).get("ready_deadline") or 0)
        dm = strings["room_dm"].format(map=attack.map_key, room=f"<#{channel.id}>", salon=salon_mention)
        if deadline > 0:
            dm += strings["room_dm_deadline"].format(
                deadline=_fmt_paris(datetime.fromtimestamp(deadline, tz=UTC))
            )
        dm += strings["room_dm_luck"]
        for player_id in _score_players(attack):
            await _dm_user_safe(bot, player_id, dm)
    except Exception:
        logger.warning("KINGDOM ATTACK: battle room opening failed", exc_info=True)


async def _sweep_pre_room_announces_safe(guild: Any, attack: Any) -> None:
    """Delete the pre-room messages of the attack salon (best effort).

    salon-nettoyage-v1 (Drasah 29/10): once the battle room's
    scoreboard is up, the declaration announce (the salon's base
    message — its 🛡️/❌ buttons are dead at the meeting anyway) and the
    T-15 reveal notes (« ⚔️ Bataille en vue … ») are noise. The refs
    come from the score (``attack_base_message_id`` +
    ``reveal_message_refs``) — restart-safe, replay-proof (the room
    opening is idempotent). A lost ref or a missing permission never
    breaks the room opening.
    """
    salon = _attack_salon(guild, attack)
    if salon is None:
        return
    score = dict(getattr(attack, "score", None) or {})
    message_ids: list[int] = []
    base_id = int(score.get("attack_base_message_id") or 0)
    if base_id:
        message_ids.append(base_id)
    for ref in score.get("reveal_message_refs") or []:
        try:
            message_ids.append(int(ref["message_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    for message_id in message_ids:
        try:
            message = await salon.fetch_message(message_id)
            await message.delete()
        except Exception:
            logger.debug("kingdoms: pre-room announce sweep skipped", exc_info=True)


async def record_battle_room_safe(attack: Any, channel_id: int, role_id: int) -> None:
    """Store the room refs through the attacks service (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return
    try:
        await wiring.attacks_service.record_battle_room(str(attack.id), int(channel_id), int(role_id))
    except Exception:
        logger.warning("KINGDOM ATTACK: battle room refs not stored", exc_info=True)


async def _record_meeting_message_safe(attack: Any, channel_id: int, message_id: int) -> None:
    """Store the meeting message ref (attack salon, b) — the refresh target."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return
    try:
        await wiring.attacks_service.record_meeting_message(str(attack.id), int(channel_id), int(message_id))
    except Exception:
        logger.warning("KINGDOM ATTACK: meeting message ref not stored", exc_info=True)


async def _record_ping_message_safe(attack: Any, channel_id: int, message_id: int) -> None:
    """Store the room's PING message ref (sabotage-ux-v1, best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return
    try:
        await wiring.attacks_service.record_ping_message(str(attack.id), int(channel_id), int(message_id))
    except Exception:
        logger.warning("KINGDOM ATTACK: room ping ref not stored", exc_info=True)


async def _stamp_room_ping_winner_safe(bot: Any, attack: Any, winner_name: str, strings: dict[str, str]) -> None:
    """Stamp the 🏆 winner on the room's ping message (sabotage-ux-v1).

    Drasah 22/10: every resolution (immediate declaration, confirmed
    tick, no-show) edits the « ⚔️ … à vos armes ! » ping of the salon so
    the players see the outcome at a glance. Best effort — the
    resolution itself never depends on the stamp.
    """
    score = dict(attack.score or {})
    channel_id = int(score.get("room_ping_channel_id") or 0)
    message_id = int(score.get("room_ping_message_id") or 0)
    if not channel_id or not message_id:
        return
    stamp = strings["room_ping_winner"].format(winner=winner_name)
    for guild in list(getattr(bot, "guilds", [])):
        channel = getattr(guild, "get_channel", lambda _id: None)(channel_id)
        if channel is None:
            continue
        message = next(
            (item for item in getattr(channel, "messages", []) if int(getattr(item, "id", 0)) == message_id),
            None,
        )
        try:
            if message is None and hasattr(channel, "get_partial_message"):
                message = channel.get_partial_message(message_id)
            if message is not None:
                existing = str(getattr(message, "content", "") or "")
                if stamp in existing:
                    return
                await message.edit(content=f"{existing}\n{stamp}")
            return
        except Exception:
            logger.warning("KINGDOM ATTACK: room ping winner stamp failed", exc_info=True)


async def _delete_battle_room_safe(bot: Any, channel_id: int, role_id: Any) -> None:
    """Delete the battle room channel + role on every guild (best effort)."""
    try:
        for guild in list(getattr(bot, "guilds", [])):
            channel = getattr(guild, "get_channel", lambda _id: None)(channel_id)
            if channel is not None:
                await channel.delete()
            if role_id is not None:
                role = getattr(guild, "get_role", lambda _id: None)(int(role_id))
                if role is not None and hasattr(role, "delete"):
                    await role.delete()
    except Exception:
        logger.warning("KINGDOM ATTACK: battle room cleanup failed", exc_info=True)


async def _cleanup_battle_rooms_safe(bot: Any, guild: Any, attacks_service: Any) -> int:
    """Delete the battle room (channel + role) AND the salon of every resolved meeting.

    restructuration-serveur-v1 b: the per-attack salon follows the
    same pattern as the voice room — it disappears
    ``room_cleanup_delay_minutes`` after the winner is known (the
    recap Géopolitique + detail thread live on, unaffected). A salon
    already purged at a CANCELLATION rides its own flag.
    """
    played = 0
    try:
        attacks = await attacks_service.attacks()
    except Exception:
        logger.warning("KINGDOM ATTACK: battle room scan failed", exc_info=True)
        return 0
    delay_minutes = int(getattr(attacks_service, "room_cleanup_delay_minutes", 30) or 30)
    now = datetime.now(UTC)
    for attack in attacks:
        score = attack.score or {}
        if not attack.is_over:
            continue
        resolved_at = getattr(attack, "resolved_at", None)
        if resolved_at is not None:
            deadline = resolved_at + timedelta(minutes=delay_minutes)
            if now < deadline:
                continue
        # Drasah 18/10 v4: the room only disappears 30 minutes after a
        # winner is known — never mid-game (live incident 18/10: the
        # room died while the players were still playing).
        if score.get("room_channel_id") and not score.get("room_cleaned"):
            played += await _cleanup_one_battle_room(bot, attacks_service, attack, score)
        if score.get("attack_channel_id") and not score.get("attack_salon_cleaned"):
            played += await _cleanup_one_attack_salon(guild, attacks_service, attack)
    await _sweep_battle_room_leftovers_safe(bot, attacks_service, attacks)
    return played


_BATTLE_ROLE_NAME_PREFIXES: tuple[str, ...] = ("Bataille ", "Battle ")
"""The room-role name prefixes (FR/EN) — the orphan sweep's filter."""


async def _sweep_battle_room_leftovers_safe(bot: Any, attacks_service: Any, attacks: list[Any]) -> int:
    """Delete the battle-room leftovers a flagged cleanup never deleted.

    battle-role-sweep-v1 (Drasah 20/10, live lockout incident): the
    cleanup marks a resolved meeting ``room_cleaned`` even when Discord
    REFUSED the actual delete — the 20/10 permission lockout left every
    room channel/role alive but flagged clean, never retried (the
    orphan « Bataille … » roles Drasah saw pile up in the server). This
    sweep re-runs the deletion on every RESOLVED meeting past its
    cleanup delay, flag or not: a leftover channel or role is deleted
    by its recorded id, and a battle role whose ref was never stored (a
    record failure) is caught by its name claimed by no live meeting.
    Idempotent: a clean guild touches nothing, a live meeting keeps
    its room whatever its flag says.
    """
    delay_minutes = int(getattr(attacks_service, "room_cleanup_delay_minutes", 30) or 30)
    due, keep_role_ids = _battle_room_sweep_due(attacks, delay_minutes, datetime.now(UTC))
    swept = await _sweep_recorded_leftovers_safe(bot, due)
    swept += await _sweep_orphan_battle_roles_safe(bot, keep_role_ids)
    if swept:
        logger.info("KINGDOM ATTACK: swept %d battle room leftover(s)", swept)
    return swept


def _battle_room_sweep_due(attacks: list[Any], delay_minutes: int, now: datetime) -> tuple[list[Any], set[int]]:
    """Return the overdue-resolved attacks and the live rooms' role ids.

    An attack is due when it is over and past its cleanup delay (a
    meeting without a resolution timestamp is overdue at once — the
    same semantics as the cleanup pass). The keep-set holds every room
    role id still claimed by a live or not-yet-due meeting: the orphan
    name sweep never touches those.
    """
    keep_role_ids: set[int] = set()
    due: list[Any] = []
    for attack in attacks:
        score = attack.score or {}
        try:
            role_id = int(score.get("room_role_id") or 0)
        except (TypeError, ValueError):
            role_id = 0
        if role_id:
            keep_role_ids.add(role_id)
        if not attack.is_over:
            continue
        resolved_at = getattr(attack, "resolved_at", None)
        deadline = resolved_at + timedelta(minutes=delay_minutes) if resolved_at is not None else None
        if deadline is None or now >= deadline:
            keep_role_ids.discard(role_id)
            due.append(attack)
    return due, keep_role_ids


def _ref_as_int(score: dict[str, Any], key: str) -> int:
    """Read one recorded room ref as an int (0 when absent/invalid)."""
    try:
        return int(score.get(key) or 0)
    except (TypeError, ValueError):
        return 0


async def _sweep_recorded_leftovers_safe(bot: Any, due: list[Any]) -> int:
    """Re-delete the recorded room refs of every overdue-resolved meeting."""
    swept = 0
    for attack in due:
        score = attack.score or {}
        room_channel_id = _ref_as_int(score, "room_channel_id")
        salon_channel_id = _ref_as_int(score, "attack_channel_id")
        role_id = _ref_as_int(score, "room_role_id")
        if not room_channel_id and not salon_channel_id and not role_id:
            continue
        try:
            for guild in list(getattr(bot, "guilds", []) or []):
                for channel_id in (room_channel_id, salon_channel_id):
                    if not channel_id:
                        continue
                    channel = getattr(guild, "get_channel", lambda _id: None)(channel_id)
                    if channel is not None and hasattr(channel, "delete"):
                        await channel.delete()
                        swept += 1
                if role_id:
                    role = getattr(guild, "get_role", lambda _id: None)(role_id)
                    if role is not None and hasattr(role, "delete"):
                        await role.delete()
                        swept += 1
        except Exception:
            logger.warning("KINGDOM ATTACK: battle room leftover sweep failed", exc_info=True)
    return swept


async def _sweep_orphan_battle_roles_safe(bot: Any, keep_role_ids: set[int]) -> int:
    """Delete the name-matched battle roles claimed by no live meeting.

    The « Bataille … » / « Battle … » names are the mod's own room-role
    pattern (FR/EN strings) — a leftover with no recorded ref (the ref
    store failed at the room open) is swept; a claimed role is kept.
    """
    swept = 0
    try:
        for guild in list(getattr(bot, "guilds", []) or []):
            for role in list(getattr(guild, "roles", []) or []):
                name = str(getattr(role, "name", ""))
                if not name.startswith(_BATTLE_ROLE_NAME_PREFIXES):
                    continue
                try:
                    role_id = int(getattr(role, "id", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if role_id in keep_role_ids:
                    continue  # claimed by a live (or not-yet-due) meeting
                if hasattr(role, "delete"):
                    await role.delete()
                    swept += 1
    except Exception:
        logger.warning("KINGDOM ATTACK: battle role orphan sweep failed", exc_info=True)
    return swept


async def _cleanup_one_battle_room(bot: Any, attacks_service: Any, attack: Any, score: dict[str, Any]) -> int:
    """Delete one resolved meeting's voice room (channel + role) and flag it."""
    await _delete_battle_room_safe(bot, int(score["room_channel_id"]), score.get("room_role_id"))
    try:
        await attacks_service.mark_room_cleaned(str(attack.id))
    except Exception:
        logger.warning("KINGDOM ATTACK: battle room cleanup flag failed", exc_info=True)
    return 1


async def _cleanup_one_attack_salon(guild: Any, attacks_service: Any, attack: Any) -> int:
    """Delete one resolved attack's salon (b) and flag it cleaned."""
    salon = _attack_salon(guild, attack)
    if salon is not None:
        try:
            await salon.delete()
        except Exception:
            logger.warning("KINGDOM ATTACK: attack salon cleanup failed", exc_info=True)
    try:
        await attacks_service.mark_attack_salon_cleaned(str(attack.id))
    except Exception:
        logger.warning("KINGDOM ATTACK: attack salon cleanup flag failed", exc_info=True)
    return 1


class KingdomDraftViewButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:room:draft:(?P<key>[0-9a-f]{16})",
):
    """blasons-vs-v1 (Drasah 28/10): the 📜 « Voir la draft » button

    of the meeting message — each side's 3 civs in random order,
    the sabotaged picks struck through, in THE player's single
    ephemeral window (the ranking stays secret forever).
    """

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:room:draft:{_room_key(attack_id)}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.attack_id = attack_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomDraftViewButton:
        """Resolve the room key back to an attack id (restart-safe)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        attack_id = await _resolve_room_attack_id_async(match["key"])
        return cls(attack_id, _strings(_locale(interaction))["draft_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer the draft sheet in the single ephemeral window."""
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _open_unique_ephemeral,
            _wiring,
        )

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await _open_unique_ephemeral(interaction, strings["no_service"])
            return
        try:
            attack = next(
                item_ for item_ in await wiring.attacks_service.attacks() if str(item_.id) == self.attack_id
            )
        except Exception:
            await _open_unique_ephemeral(interaction, strings["gaia_gone"])
            return
        embed = await _draft_sheet_embed(attack, wiring.kingdoms_service, strings)
        await _open_unique_ephemeral(interaction, "", embed=embed)


class KingdomRoomUrlButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:room:url:(?P<key>[0-9a-f]{16})",
):
    """The persistent 🔗 button of the battle room (URL share)."""

    def __init__(
        self,
        attack_id: str,
        label: str,
        *,
        style: Any = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:room:url:{_room_key(attack_id)}",
                style=style or discord.ButtonStyle.secondary,
                disabled=disabled,
            )
        )
        self.attack_id = attack_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRoomUrlButton:
        """Resolve the room key back to an attack id (restart-safe).

        battle-room-id-v1: a room whose ID is already set rebuilds the
        button as the RED « Modifier l'ID » action.
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        attack_id = await _resolve_room_attack_id_async(match["key"])
        label = strings["room_id_button"]
        style: Any = discord.ButtonStyle.secondary
        wiring = _wiring()
        if wiring.attacks_service is not None and attack_id:
            try:
                current = next(item_ for item_ in await wiring.attacks_service.attacks() if str(item_.id) == attack_id)
            except Exception:
                current = None
            if current is not None and str(current.lobby_url or "").strip():
                label = strings["room_id_modify_button"]
                style = discord.ButtonStyle.danger
        return cls(attack_id, label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the game URL modal — and LOCK the entry for the other player.

        battle-room-id-v1 (Drasah 22/10): the moment one player clicks,
        the shared view refreshes with the ID button locked (« saisie
        en cours ») so the other player cannot open a conflicting
        modal. If the player cancels the modal, a background grace
        task unlocks the entry again.
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        # restructuration-serveur-v1 b: the meeting message is public —
        # its buttons are reserved to the two players (clean refusal).
        try:
            current = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id)
        except Exception:
            current = None
        if current is None or _room_side_of(current, str(interaction.user.id)) == "":
            await interaction.response.send_message(strings["room_not_player"], ephemeral=True)
            return
        # Instant lock: the ID button disappears for the other player.
        await _refresh_room_message_safe(interaction.client, current, _locale(interaction), id_pending=True)

        async def _unlock_later() -> None:
            try:
                await asyncio.sleep(300)
                fresh = next(
                    (item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id),
                    None,
                )
                if fresh is not None and not fresh.is_over and not str(fresh.lobby_url or "").strip():
                    await _refresh_room_message_safe(interaction.client, fresh, _locale(interaction))
            except Exception:
                logger.debug("kingdoms: room ID unlock sweep skipped", exc_info=True)

        unlock_task = asyncio.create_task(_unlock_later())
        _ROOM_UNLOCK_TASKS.add(unlock_task)
        unlock_task.add_done_callback(_ROOM_UNLOCK_TASKS.discard)
        await interaction.response.send_modal(KingdomRoomUrlModal(self.attack_id, str(interaction.user.id)))


class KingdomRoomUrlModal(discord.ui.Modal):
    """The battle-room game URL modal (Drasah 18/10 v2)."""

    def __init__(self, attack_id: str, player_id: str) -> None:
        strings = _strings("fr")
        super().__init__(title=strings["score_url_modal_title"][:45], timeout=300)
        self.attack_id = attack_id
        self.player_id = player_id
        self.strings = strings
        self.url_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=strings["score_url_modal_label"][:45],
            placeholder=strings["score_url_modal_placeholder"][:100],
            required=True,
            max_length=200,
        )
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Store the URL and post it in the battle room's text channel."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = self.strings
        wiring = _wiring()
        if wiring.attacks_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        url = str(self.url_input.value or "").strip()
        try:
            attack = await wiring.attacks_service.set_lobby_url(self.attack_id, self.player_id, url)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(strings["failed"].format(message=message), ephemeral=True)
            return
        # Drasah 18/10 v5 + battle-room-id-v1: no extra post — the room's
        # single message refreshes in place (the ID line + the unlocked
        # per-side ✅ Prêt buttons; a CHANGED ID resets both Prêts).
        await _refresh_room_message_safe(interaction.client, attack, _locale(interaction))
        # battle-room-id-v2 (Drasah 22/10): NO ephemeral bubble at all —
        # the scoreboard IS the feedback (the ID shows centered, the
        # Prêts visibly reset); the click defers silently.
        await interaction.response.defer(ephemeral=True)


class KingdomRoomResultButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:room:result:(?P<key>[0-9a-f]{16})",
):
    """The persistent 🏆 button of the battle room (result declaration)."""

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:room:result:{_room_key(attack_id)}",
                style=discord.ButtonStyle.primary,
            )
        )
        self.attack_id = attack_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRoomResultButton:
        """Resolve the room key back to an attack id (restart-safe)."""
        label = _strings(_locale(interaction))["result_button"]
        return cls(await _resolve_room_attack_id_async(match["key"]), label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Ephemeral winner flow (battle-room-id-v2, Drasah 22/10).

        No pending declaration: a winner select → an EXPLICIT
        confirmation (anti-missclick — ✏️ Modifier loops back to the
        select). Another player's pending declaration: the clicker
        can no longer declare a winner — only ✅ Confirmer le
        résultat or ⚖️ Contester. Every ephemeral view self-destructs
        (idle timer re-armed at each interaction), the receipts after
        5 minutes — the dispute message STAYS until the admins
        arbitrate.
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            attack = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id)
        except Exception:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        if attack.is_over or str(getattr(attack, "state", "")) != "meeting":
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        if _room_side_of(attack, str(interaction.user.id)) == "":
            # restructuration-serveur-v1 b: the meeting message is
            # public — its buttons are reserved to the two players.
            await interaction.response.send_message(strings["room_not_player"], ephemeral=True)
            return
        if not (attack.score or {}).get("all_ready"):
            # Drasah 18/10 v5: no 🏆 before both players are Prêt — a
            # stale click (an old view) is politely refused.
            await interaction.response.send_message(strings["result_not_ready"], ephemeral=True)
            return
        score = dict(attack.score or {})
        if score.get("disputed"):
            # battle-room-id-v2: once the dispute is open the flow is
            # frozen — the ephemeral reminder STAYS (no timer) until
            # the admins arbitrate.
            await interaction.response.send_message(
                strings["score_disputed"].format(game=str(attack.lobby_url or "") or "—"),
                ephemeral=True,
            )
            return
        player_id = str(interaction.user.id)
        others = {
            declarer: declaration
            for declarer, declaration in dict(score.get("declarations") or {}).items()
            if str(declarer) != player_id
        }
        if others:
            declarer, declaration = next(iter(others.items()))
            await _send_room_review(interaction, wiring, strings, attack, str(declarer), str(declaration["winner"]))
            return
        await _send_room_select(interaction, wiring, strings, attack)


_ROOM_FLOW_IDLE_SECONDS = 10
_ROOM_RECEIPT_SECONDS = 300


async def _room_select_view(
    wiring: Any,
    strings: dict[str, str],
    attack: Any,
    on_choice: Any,
) -> discord.ui.View:
    """Build the ephemeral winner select (stage 1 of the v2 flow)."""
    view = discord.ui.View(timeout=None)

    async def _on_winner(target: discord.Interaction) -> None:
        await on_choice(target, str(select.values[0]))

    attacker_name = await _kingdom_name(wiring.kingdoms_service, attack.attacker_kingdom_id)
    defender_name = await _kingdom_name(wiring.kingdoms_service, attack.defender_kingdom_id)
    select: discord.ui.Select[Any] = discord.ui.Select(
        placeholder=strings["result_select_placeholder"][:100],
        options=[
            discord.SelectOption(label=str(attacker_name)[:100], value=str(attack.attacker_kingdom_id)),
            discord.SelectOption(label=str(defender_name)[:100], value=str(attack.defender_kingdom_id)),
        ],
        min_values=1,
        max_values=1,
    )
    select.callback = _on_winner  # type: ignore[method-assign,assignment]
    view.add_item(select)
    return view


async def _send_room_select(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    attack: Any,
    *,
    via_edit: bool = False,
) -> None:
    """Post (or edit back to) the winner select stage (v2).

    The ✏️ Modifier of the confirmation loops back HERE — the
    declaration never lands without an explicit confirm.
    """
    attacker_name = await _kingdom_name(wiring.kingdoms_service, attack.attacker_kingdom_id)
    defender_name = await _kingdom_name(wiring.kingdoms_service, attack.defender_kingdom_id)

    async def _on_choice(target: discord.Interaction, winner_kingdom_id: str) -> None:
        if str(getattr(target.user, "id", "")) != str(interaction.user.id):
            await target.response.defer()
            return
        winner_name = attacker_name if winner_kingdom_id == str(attack.attacker_kingdom_id) else defender_name
        await _edit_room_confirm(target, wiring, strings, attack, winner_kingdom_id, winner_name)

    view = await _room_select_view(wiring, strings, attack, _on_choice)
    title = strings["result_select_title"]
    if via_edit:
        await interaction.response.edit_message(content=title, view=view)
    else:
        await interaction.response.send_message(title, view=view, ephemeral=True)
    key = (str(attack.id), str(getattr(interaction.user, "id", "")))
    _arm_ephemeral_timer(key, interaction, _ROOM_FLOW_IDLE_SECONDS)


async def _edit_room_confirm(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    attack: Any,
    winner_kingdom_id: str,
    winner_name: str,
) -> None:
    """Edit the ephemeral into the explicit confirmation stage (v2)."""
    view = discord.ui.View(timeout=None)
    confirm: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["result_confirm_button"], style=discord.ButtonStyle.success
    )
    change: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["result_edit_button"], style=discord.ButtonStyle.secondary
    )

    async def _on_confirm(target: discord.Interaction) -> None:
        if str(getattr(target.user, "id", "")) != str(interaction.user.id):
            await target.response.defer()
            return
        await _declare_room_score(
            target,
            wiring,
            strings,
            str(attack.id),
            str(target.user.id),
            winner_kingdom_id,
            game_id=str(attack.lobby_url or ""),
            edit_response=True,
        )

    async def _on_change(target: discord.Interaction) -> None:
        if str(getattr(target.user, "id", "")) != str(interaction.user.id):
            await target.response.defer()
            return
        # Anti-missclick loop: back to the winner select, never a
        # silent declaration.
        await _send_room_select(target, wiring, strings, attack, via_edit=True)

    confirm.callback = _on_confirm  # type: ignore[method-assign,assignment]
    change.callback = _on_change  # type: ignore[method-assign,assignment]
    view.add_item(confirm)
    view.add_item(change)
    await interaction.response.edit_message(
        content=strings["result_confirm_title"].format(winner=winner_name), view=view
    )
    key = (str(attack.id), str(getattr(interaction.user, "id", "")))
    _arm_ephemeral_timer(key, interaction, _ROOM_FLOW_IDLE_SECONDS)


async def _send_room_review(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    attack: Any,
    declarer_id: str,
    winner_kingdom_id: str,
) -> None:
    """Show the OTHER player's declaration — confirm or contest, never re-declare (v2)."""
    declarer_name = await _lord_name(wiring.kingdoms_service, declarer_id)
    winner_name = await _kingdom_name(wiring.kingdoms_service, winner_kingdom_id)
    contrary = (
        str(attack.defender_kingdom_id)
        if winner_kingdom_id == str(attack.attacker_kingdom_id)
        else str(attack.attacker_kingdom_id)
    )
    view = discord.ui.View(timeout=None)
    confirm: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["result_review_confirm_button"], style=discord.ButtonStyle.success
    )
    contest: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["result_review_contest_button"], style=discord.ButtonStyle.danger
    )

    async def _on_confirm(target: discord.Interaction) -> None:
        if str(getattr(target.user, "id", "")) != str(interaction.user.id):
            await target.response.defer()
            return
        await _declare_room_score(
            target,
            wiring,
            strings,
            str(attack.id),
            str(target.user.id),
            winner_kingdom_id,
            game_id=str(attack.lobby_url or ""),
            edit_response=True,
        )

    async def _on_contest(target: discord.Interaction) -> None:
        if str(getattr(target.user, "id", "")) != str(interaction.user.id):
            await target.response.defer()
            return
        # Contrary declaration → dispute, the admins arbitrate.
        await _declare_room_score(
            target,
            wiring,
            strings,
            str(attack.id),
            str(target.user.id),
            contrary,
            game_id=str(attack.lobby_url or ""),
            edit_response=True,
        )

    confirm.callback = _on_confirm  # type: ignore[method-assign,assignment]
    contest.callback = _on_contest  # type: ignore[method-assign,assignment]
    view.add_item(confirm)
    view.add_item(contest)
    await interaction.response.send_message(
        strings["result_review_title"].format(player=declarer_name, winner=winner_name),
        view=view,
        ephemeral=True,
    )
    key = (str(attack.id), str(getattr(interaction.user, "id", "")))
    _arm_ephemeral_timer(key, interaction, _ROOM_FLOW_IDLE_SECONDS)


async def _declare_room_score(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    attack_id: str,
    player_id: str,
    winner_kingdom_id: str,
    game_id: str = "",
    *,
    edit_response: bool = False,
) -> None:
    """Declare the result from the battle room (resolved or pending).

    Drasah 18/10 v5: the ID of the party entered earlier rides the
    declaration (no more « the game id is required »), ``resolved``
    stays ephemeral in the room and tells the story in Géopolitique.
    battle-room-id-v2: the flow answers on the ephemeral message it
    edits (``edit_response``) and its receipts self-destruct after
    5 minutes — the DISPUTE message stays until the admins arbitrate.
    """

    async def _respond(content: str, *, ephemeral: bool = True) -> None:
        if edit_response:
            await interaction.response.edit_message(content=content, view=None)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    if wiring.attacks_service is None or not attack_id:
        await _respond(strings["no_service"])
        return
    try:
        attack, outcome = await wiring.attacks_service.declare_score(attack_id, player_id, winner_kingdom_id, game_id)
    except Exception as exc:
        message = getattr(exc, "message", None) or str(exc)
        await _respond(strings["failed"].format(message=message))
        return
    key = (str(attack.id), str(getattr(interaction.user, "id", "")))
    if outcome == "disputed":
        # battle-room-id-v2: the dispute reminder STAYS in the player's
        # ephemeral lane until the admins arbitrate (no auto-delete).
        await _respond(strings["score_disputed"].format(game=game_id or "—"), ephemeral=True)
        await _dm_admins_dispute_safe(interaction.client, wiring, attack, strings)
        return
    if outcome == "already":
        await _respond(strings["score_already"])
        _arm_ephemeral_timer(key, interaction, _ROOM_RECEIPT_SECONDS)
        return
    if outcome == "resolved":
        winner = await _kingdom_name(wiring.kingdoms_service, winner_kingdom_id)
        await _respond(strings["score_resolved"].format(winner=winner, map=attack.map_key, game=game_id or "—"))
        # sabotage-ux-v1 (Drasah 22/10): the room ping wears the winner.
        await _stamp_room_ping_winner_safe(interaction.client, attack, winner, strings)
        await _announce_conquest_safe(interaction.client, wiring.kingdoms_service, attack, _locale(interaction))
        _arm_ephemeral_timer(key, interaction, _ROOM_RECEIPT_SECONDS)
        return
    minutes = 10
    try:
        minutes = int(wiring.attacks_service._config.attacks.score_confirm_minutes)
    except Exception:
        minutes = 10
    await _respond(
        strings["score_pending"].format(game=game_id or "—", minutes=minutes),
    )
    _arm_ephemeral_timer(key, interaction, _ROOM_RECEIPT_SECONDS)


class KingdomRoomReadyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:room:ready:(?P<key>[0-9a-f]{16})(?::(?P<side>attacker|defender))?",
):
    """The persistent ✅ Prêt button of the battle room (Drasah 18/10).

    battle-room-id-v1 (Drasah 22/10): the symmetric scoreboard carries
    ONE Prêt button PER SIDE (attacker left, defender right) — the
    clicker must be that side's player. The click records the
    readiness and edits the player's half of the scoreboard in place
    (NO ephemeral bubble); when BOTH players are ready the match is
    announced: a Discord scheduled event carrying the match infos is
    created and the room tells the players to start.
    """

    def __init__(self, attack_id: str, label: str, *, side: str | None = None) -> None:
        key = _room_key(attack_id)
        custom_id = f"kingdoms:attack:room:ready:{key}" if side is None else f"kingdoms:attack:room:ready:{key}:{side}"
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=custom_id,
                style=discord.ButtonStyle.success,
            )
        )
        self.attack_id = attack_id
        self.side = side

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomRoomReadyButton:
        """Resolve the room key back to an attack id (restart-safe)."""
        strings = _strings(_locale(interaction))
        side = match["side"] if "side" in match.groupdict() else None
        if side:
            label = strings[f"room_ready_{side}_button"].format(player=strings[f"room_side_{side}"])
        else:
            label = strings["room_ready_button"]
        return cls(await _resolve_room_attack_id_async(match["key"]), label, side=side)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Record the readiness — both ready creates the match event.

        Drasah 18/10 v4: the Prêt unlocks only once the lobby URL is
        shared (the 🔗 button) — no URL, no ready. battle-room-id-v1:
        the scoreboard shows the Prêt on the player's own side (edit
        in place, no ephemeral bubble).
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            current = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id)
        except Exception:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        side_of = _room_side_of(current, str(interaction.user.id))
        if side_of == "":
            # restructuration-serveur-v1 b: the meeting message is
            # public — its buttons are reserved to the two players.
            await interaction.response.send_message(strings["room_not_player"], ephemeral=True)
            return
        if self.side is not None and side_of != self.side:
            # battle-room-id-v1: each Prêt button belongs to ONE side.
            # battle-room-id-v2: the refusal self-destructs after 10 s
            # (no clutter in the player's ephemeral lane).
            await interaction.response.send_message(strings["room_not_your_side"], ephemeral=True)
            _arm_ephemeral_timer(
                ("ready-refusal", str(getattr(interaction, "id", "") or id(interaction))),
                interaction,
                10,
            )
            return
        if not str(current.lobby_url or "").strip():
            await interaction.response.send_message(strings["room_ready_needs_url"], ephemeral=True)
            return
        try:
            attack = await wiring.attacks_service.mark_ready(self.attack_id, str(interaction.user.id))
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(strings["failed"].format(message=message), ephemeral=True)
            return
        # battle-room-id-v1: the scoreboard refreshes in place — the
        # player's own half shows « ✅ Prêt », 🏆 appears once both are
        # Prêt. The click itself answers with a silent defer (no
        # ephemeral bubble, the view IS the feedback).
        await _refresh_room_message_safe(interaction.client, attack, _locale(interaction))
        await interaction.response.defer()
        score = attack.score or {}
        if not score.get("all_ready"):
            return
        if score.get("room_event_id"):
            return
        await _announce_room_start_safe(interaction, attack, strings)


def _room_side_of(attack: Any, user_id: str) -> str:
    """Resolve which side of the meeting the user speaks for."""
    if user_id == str(attack.attacker_lord_id):
        return "attacker"
    if user_id == str(attack.defender_lord_id) and not attack.ai_defender:
        return "defender"
    return ""


async def _sabotage_stock_line(wiring: Any, attack: Any, side: str, strings: dict[str, str]) -> str:
    """Build the « 🗡️ Sabotage : n · 🦅 Faucon : … » line (sabotage-ux-v1).

    Drasah 22/10: the draft & ban DM tells the player what he owns.
    sabotage-consommable-v1: one bought Sabotage charge is spent at
    the snipe — the line shows the REMAINING stock (it decrements
    after each use); the Faucon stays a volière slot + a 48h cooldown.
    Best effort: a failing lookup never breaks the DM.
    """
    kingdom_id = str(
        getattr(attack, "attacker_kingdom_id", "") if side == "attacker" else getattr(attack, "defender_kingdom_id", "")
    )
    if not kingdom_id:
        return ""
    sabotage = 0
    faucon = strings["sab_faucon_absent"]
    try:
        state = await wiring.attacks_service.technology_state(kingdom_id)
        sabotage = int(state.purchases.get("sabotage", 0))
    except Exception:
        sabotage = 0
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import _kingdom_by_id_safe

        kingdom = await _kingdom_by_id_safe(wiring.kingdoms_service, kingdom_id)
        if kingdom is not None and "faucon" in (kingdom.voliere_slots or []):
            ready = int(kingdom.voliere_cooldowns.get("faucon", 0) or 0)
            if ready > int(game_now().timestamp()):
                faucon = strings["sab_faucon_flying"].format(time=_fmt_paris(datetime.fromtimestamp(ready, tz=UTC)))
            else:
                faucon = strings["sab_faucon_ready"]
    except Exception:
        faucon = strings["sab_faucon_absent"]
    return strings["sab_stock_line"].format(sabotage=sabotage, faucon=faucon)


async def _sabotage_panel_content(
    wiring: Any, attack: Any, side: str, strings: dict[str, str]
) -> tuple[str, discord.ui.View | None, discord.File | None]:
    """Build the sabotage PANEL content + its rebuilt menu (sabotage-ux-v1).

    One panel, edited in place: the title carries the X/2 récap, the
    removed-civs line, the stock line. The menu view is None when no
    action is available anymore (the caller sweeps the panel).

    sabotage-visuel-v1 (Drasah 29/10, PATCH 1.0 item 2): the panel
    also carries the ENEMY picks as ONE crest mosaic, every sabotaged
    civilization wearing the ❌ cross — the mosaic is recomposed at
    every action so a fresh snipe crosses its blason at once. Best
    effort: a failing composition leaves the text panel standing.
    """
    pickban = dict(attack.pickban or {})
    used = [str(civ) for civ in (pickban.get(f"{side}_sabotages") or [])]
    lines = [strings["sab_panel_title"].format(used=len(used))]
    if used:
        lines.append(strings["sab_panel_used"].format(list=", ".join(used)))
    else:
        lines.append(strings["sab_panel_none"])
    lines.append("")
    lines.append(await _sabotage_stock_line(wiring, attack, side, strings))
    view = await _sabotage_menu_view(wiring, attack, side, strings)
    enemy = "defender" if side == "attacker" else "attacker"
    # sabotage-dm-fresh-v1 (Drasah 30/10): the panel mosaic wears the
    # SAME seeded order as the DM reveal — stable across re-renders
    # (the raw pick order would leak the hidden ranking anyway).
    enemy_picks = _reveal_civs(f"{attack.id}:{enemy}", pickban.get(f"{enemy}_picks"))
    file: discord.File | None = None
    if enemy_picks:
        try:
            file = await civ_crest_mosaic_file(
                [(civ, "", civ) for civ in enemy_picks],
                filename="sabotage.png",
                with_origin_badges=False,
                crossed=set(used),
            )
        except Exception:
            logger.warning("KINGDOM ATTACK: sabotage mosaic failed", exc_info=True)
    return "\n".join(lines), view, file


async def _sabotage_menu_view(wiring: Any, attack: Any, side: str, strings: dict[str, str]) -> discord.ui.View | None:
    """Build the ephemeral DM sabotage menu of one side (None when empty).

    Drasah 18/10 v4: the sabotage phase moved to the DMs, right after
    the pick-ban reveal. Both sides can act (Faucon or Sabotage techno).
    The Passer button is the persistent DM button — never here.
    """
    user_id = str(getattr(attack, "attacker_lord_id" if side == "attacker" else "defender_lord_id"))
    view = discord.ui.View(timeout=300)
    for action in ("faucon", "techno"):
        button = await _sabotage_action_button(wiring, attack, user_id, side, action, strings)
        if button is not None:
            view.add_item(button)
    if not list(view.children):
        return None
    return view


async def _sabotage_action_button(
    wiring: Any, attack: Any, user_id: str, side: str, action: str, strings: dict[str, str]
) -> discord.ui.Button[discord.ui.View] | None:
    """Build one DM sabotage action button, None when unavailable.

    Faucon: both sides, when the volière service is wired and the
    clicking lord carries a kingdom. Techno: both sides, when the
    kingdom OF THIS SIDE bought the Sabotage technology.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    if action == "faucon":
        if wiring.voliere_service is None:
            return None
        lord = await _lord_of(wiring.kingdoms_service, user_id)
        kingdom_id = str(getattr(lord, "kingdom_id", "") or "") if lord is not None else ""
        if not kingdom_id:
            return None
        # sabotage-garde-v1 (Drasah 28/10): the 🦅 button only shows
        # when the Faucon REALLY sits in the volière AND is back from
        # its cooldown — the SAME rule as the stock line. Before
        # this, a lord with NOTHING still got the button and the civ
        # pick died on a raw English error; now the menu stays empty
        # and the 🗡️ click answers sab_no_resources at once.
        from kingdoms.mods.kingdoms.kingdom_persistent import _kingdom_by_id_safe

        kingdom = await _kingdom_by_id_safe(wiring.kingdoms_service, kingdom_id)
        if kingdom is None or "faucon" not in (kingdom.voliere_slots or []):
            return None
        if int(kingdom.voliere_cooldowns.get("faucon", 0) or 0) > int(game_now().timestamp()):
            return None
    else:
        kingdom_id = str(attack.attacker_kingdom_id if side == "attacker" else attack.defender_kingdom_id)
        bought = False
        try:
            state = await wiring.attacks_service.technology_state(kingdom_id)
            bought = state.purchases.get("sabotage", 0) > 0
        except Exception:
            bought = False
        if not bought:
            return None
    button: discord.ui.Button[discord.ui.View] = discord.ui.Button(
        label=strings[f"sab_{action}"][:80], style=discord.ButtonStyle.secondary
    )

    async def _on_action(target: discord.Interaction) -> None:
        key = (str(attack.id), user_id)
        if str(getattr(target.user, "id", "")) != user_id:
            await target.response.defer()
            return
        # sabotage-ux-v1 (Drasah 22/10): the menu CLICK edits the panel
        # in place — no new bubble, the civ select replaces the buttons.
        try:
            fresh = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == str(attack.id))
        except Exception:
            fresh = attack
        content, menu, file = await _sabotage_panel_content(wiring, fresh, side, strings)
        civ_view = await _sabotage_civ_select_view(wiring, str(attack.id), side, action, strings)
        if civ_view is None:
            await target.response.edit_message(content=f"{content}\n\n{strings['sab_empty']}", view=menu)
            _arm_sabotage_panel_timer(key)
            return
        # sabotage-visuel-v1 (Drasah 29/10): the re-rendered MOSAIC rides
        # the edit as an ``attachments`` list — the ONLY way a live
        # interaction edit carries new files (never ``files=``).
        edit_kwargs: dict[str, Any] = {
            "content": f"{content}\n\n{strings['sab_choose']}",
            "view": civ_view,
            "attachments": [file] if file is not None else [],
        }
        await target.response.edit_message(**edit_kwargs)
        _arm_sabotage_panel_timer(key)

    button.callback = _on_action  # type: ignore[method-assign,assignment]
    return button


async def _sabotage_civ_select_view(
    wiring: Any, attack_id: str, side: str, action: str, strings: dict[str, str]
) -> discord.ui.View | None:
    """Build the enemy-civ select of one DM sabotage action (None when empty)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    try:
        attack = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == attack_id)
    except Exception:
        return None
    user_id = str(attack.attacker_lord_id if side == "attacker" else attack.defender_lord_id)
    pickban = dict(attack.pickban or {})
    enemy = "defender" if side == "attacker" else "attacker"
    already = [str(civ) for civ in (pickban.get(f"{side}_sabotages") or [])]
    options = [
        civ
        for civ in _reveal_civs(f"{attack.id}:{enemy}", pickban.get(f"{enemy}_picks"))
        if civ not in already
    ][:25]
    if not options:
        return None
    view = discord.ui.View(timeout=300)
    select: discord.ui.Select[Any] = discord.ui.Select(
        placeholder=strings["sab_choose"][:100],
        options=[discord.SelectOption(label=civ[:100], value=civ) for civ in options],
        min_values=1,
        max_values=1,
    )

    async def _on_pick(target: discord.Interaction) -> None:
        if str(getattr(target.user, "id", "")) != user_id:
            await target.response.defer()
            return
        civ = str(select.values[0])
        try:
            if action == "faucon":
                lord = await _lord_of(wiring.kingdoms_service, user_id)
                kingdom_id = str(getattr(lord, "kingdom_id", "") or "") if lord is not None else ""
                await wiring.voliere_service.faucon_sabotage(kingdom_id, attack_id, civ, now=datetime.now(UTC))
            else:
                await wiring.attacks_service.sabotage_techno(attack_id, user_id, civ)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await _unique_notice(target, strings["failed"].format(message=message))
            return
        # sabotage-ux-v1 (Drasah 22/10): the pick edits the SAME panel —
        # récap X/2 + the removed civ + the stock line, no new bubble.
        # sabotage-visuel-v1 (Drasah 29/10, PATCH 1.0 item 2): the
        # panel re-renders its MOSAIC — the freshly sabotaged blason
        # wears the ❌ at once — and the edit carries the file through
        # ``attachments`` (the only live-edit path for new files).
        try:
            fresh = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == attack_id)
        except Exception:
            fresh = attack
        content, menu, file = await _sabotage_panel_content(wiring, fresh, side, strings)
        used = len([str(civ2) for civ2 in ((fresh.pickban or {}).get(f"{side}_sabotages") or [])])
        note = strings["sab_panel_done"].format(civ=civ, used=used)
        key = (attack_id, user_id)
        # sabotage-dm-fresh-v1 (Drasah 30/10): the DM Reveal is the ONE
        # living surface — it is re-edited in place right after the
        # pick (crossed mosaic + fresh stock), BEFORE the panel moves
        # on; a dead DM falls back to a NEW DM (option A).
        await _refresh_sabotage_dm_safe(
            getattr(target, "client", None), wiring, fresh, side, strings
        )
        more = used < 2 and menu is not None and bool(list(menu.children))
        if more:
            # Under the cap and still owning a charge — the panel asks
            # for a 2nd sabotage IN place: Oui re-opens the action menu,
            # Non closes the ephemeral for good.
            body = f"{content}\n\n{note}\n\n{strings['sab_again_ask']}"
            view: discord.ui.View | None = _sabotage_again_view(wiring, attack_id, side, strings)
        else:
            # Cap reached or stock dry — no question, the panel shows
            # the récap and sweeps itself away (the DM carries on).
            body = f"{content}\n\n{note}"
            view = None
        try:
            await target.response.edit_message(
                content=body,
                view=view,
                attachments=[file] if file is not None else [],
            )
        except Exception:
            # The edit died (token gap, restarted panel) — the fallback
            # re-sends the WHOLE panel (récap + note + mosaic), NEVER a
            # lone note bubble, and rides the fenêtre unique registry so
            # the flow's next answer closes it (PATCH 1.0 item 2).
            logger.warning("KINGDOM ATTACK: sabotage panel edit failed", exc_info=True)
            from kingdoms.mods.kingdoms.kingdom_persistent import _EPHEMERAL_WINDOWS

            await _close_stale_notice(user_id)
            fallback_kwargs: dict[str, Any] = {"view": view, "ephemeral": True}
            if file is not None:
                fallback_kwargs["files"] = [file]
            window = await target.followup.send(body, **fallback_kwargs)
            _EPHEMERAL_WINDOWS[user_id] = (target, time.monotonic(), window)
        if more:
            _arm_sabotage_panel_timer(key)
        else:
            # No action left — sweep the closed panel quickly.
            _arm_sabotage_panel_timer(key, seconds=_SABOTAGE_PANEL_SWEEP_SECONDS)

    select.callback = _on_pick  # type: ignore[method-assign,assignment]
    view.add_item(select)
    return view


def _sabotage_again_view(
    wiring: Any, attack_id: str, side: str, strings: dict[str, str]
) -> discord.ui.View:
    """The « one more sabotage ? » confirmation of the panel (dm-fresh-v1).

    Drasah 30/10: after a successful pick the panel asks IN place —
    Oui (green) re-opens the action menu, Non (red) closes the
    ephemeral at once. The DM Reveal stays the living surface either
    way (it was refreshed before the question).
    """
    view = discord.ui.View(timeout=300)

    async def _fresh() -> Any:
        try:
            return next(
                item for item in await wiring.attacks_service.attacks() if str(item.id) == attack_id
            )
        except Exception:
            return None

    async def _on_yes(target: discord.Interaction) -> None:
        attack = await _fresh()
        user_id = (
            str(attack.attacker_lord_id if side == "attacker" else attack.defender_lord_id)
            if attack is not None
            else ""
        )
        if not user_id or str(getattr(target.user, "id", "")) != user_id:
            await target.response.defer()
            return
        key = (attack_id, user_id)
        content, menu, file = await _sabotage_panel_content(wiring, attack, side, strings)
        if menu is None or not list(menu.children):
            # Nothing left after all — the récap stands, the panel sweeps.
            await target.response.edit_message(
                content=f"{content}\n\n{strings['sab_empty']}",
                view=None,
                attachments=[file] if file is not None else [],
            )
            _arm_sabotage_panel_timer(key, seconds=_SABOTAGE_PANEL_SWEEP_SECONDS)
            return
        await target.response.edit_message(
            content=f"{content}\n\n{strings['sab_choose']}",
            view=menu,
            attachments=[file] if file is not None else [],
        )
        _arm_sabotage_panel_timer(key)

    async def _on_no(target: discord.Interaction) -> None:
        attack = await _fresh()
        user_id = (
            str(attack.attacker_lord_id if side == "attacker" else attack.defender_lord_id)
            if attack is not None
            else str(getattr(target.user, "id", ""))
        )
        if attack is not None and str(getattr(target.user, "id", "")) != user_id:
            await target.response.defer()
            return
        # The player is done — the ephemeral closes NOW, the DM remains.
        await target.response.defer()
        await _delete_sabotage_panel((attack_id, user_id))

    yes = discord.ui.Button(label=strings["sab_again_yes"][:80], style=discord.ButtonStyle.success)
    no = discord.ui.Button(label=strings["sab_again_no"][:80], style=discord.ButtonStyle.danger)
    yes.callback = _on_yes  # type: ignore[method-assign,assignment]
    no.callback = _on_no  # type: ignore[method-assign,assignment]
    view.add_item(yes)
    view.add_item(no)
    return view


async def _announce_room_start_safe(interaction: discord.Interaction, attack: Any, strings: dict[str, str]) -> None:
    """Create the match scheduled event (best effort).

    Drasah 18/10 v5: both players clicked Prêt — the announcement
    lives in the ROOM MESSAGE's refreshed footer (no extra post, the
    vocal stays clean); only the server-calendar event is created here.
    Drasah 22/10 battle-ai-rdv-v1: the tick (AI ready flip) reuses the
    same event creation — the body lives in ``_create_room_event_safe``.
    """
    await _create_room_event_safe(interaction.client, attack, strings, interaction.channel)


async def _create_room_event_safe(client: Any, attack: Any, strings: dict[str, str], channel: Any = None) -> None:
    """Create the room's scheduled event from an interaction OR a tick.

    Drasah 22/10 battle-ai-rdv-v1: against the AI defender the ready
    flip happens in the phase tick (no interaction); the channel is
    then resolved from ``score["room_channel_id"]`` across the client's
    guilds. Best effort — never raises.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    guild = None
    if channel is None:
        score_probe = dict(attack.score or {})
        channel_id = int(score_probe.get("room_channel_id") or 0)
        for candidate in list(getattr(client, "guilds", []) or []):
            resolved = getattr(candidate, "get_channel", lambda _id: None)(channel_id)
            if resolved is not None:
                channel = resolved
                guild = candidate
                break
    else:
        guild = getattr(channel, "guild", None) or getattr(client, "get_guild", lambda _id: None)(
            getattr(channel, "guild_id", 0)
        )
    if guild is None:
        logger.warning("KINGDOM ATTACK: room match announce without a guild")
        return
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel, discord.TextChannel, discord.Thread)):
        logger.warning("KINGDOM ATTACK: room match announce outside a room channel")
        return
    score = dict(attack.score or {})
    attacker_name = await _kingdom_name(wiring.kingdoms_service, attack.attacker_kingdom_id)
    defender_name = await _kingdom_name(wiring.kingdoms_service, attack.defender_kingdom_id)
    try:
        event = await guild.create_scheduled_event(
            name=strings["room_event_name"].format(map=attack.map_key, attacker=attacker_name, defender=defender_name)[
                :100
            ],
            description=strings["room_event_description"].format(attacker=attacker_name, defender=defender_name),
            start_time=datetime.now(UTC),
            entity_type=discord.EntityType.voice,
            privacy_level=discord.PrivacyLevel.guild_only,
            channel=channel,
        )
        score["room_event_id"] = int(event.id)
    except Exception:
        logger.warning("KINGDOM ATTACK: room match event creation failed", exc_info=True)
    if int(score.get("room_event_id") or 0) > 0:
        try:
            if wiring.attacks_service is not None:
                await wiring.attacks_service.record_room_event(str(attack.id), int(score["room_event_id"]))
        except Exception:
            logger.warning("KINGDOM ATTACK: room event ref not stored", exc_info=True)


async def _resolve_room_attack_id_async(key: str) -> str:
    """Resolve the room key back to an attack id (the async service scan)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    if wiring.attacks_service is None:
        return ""
    try:
        for attack in await wiring.attacks_service.attacks():
            if _room_key(str(attack.id)) == key:
                return str(attack.id)
    except Exception:
        logger.warning("KINGDOM ATTACK: room key resolution failed", exc_info=True)
    return ""


class KingdomDisputeView(discord.ui.View):
    """The admin arbitration view of a disputed meeting (bot-admin only)."""

    def __init__(self, attack_id: str, locale: str) -> None:
        super().__init__(timeout=None)
        self.attack_id = attack_id
        strings = _strings(locale)
        self.strings = strings
        for choice, key in (
            ("attacker", "dispute_win_attacker"),
            ("defender", "dispute_win_defender"),
            ("both", "dispute_double"),
        ):
            button: discord.ui.Button[discord.ui.View] = discord.ui.Button(
                label=strings[key][:80],
                custom_id=f"kingdoms:attack:dispute:{choice}:{hashlib.sha256(str(attack_id).encode()).hexdigest()[:16]}",
                style=discord.ButtonStyle.danger,
            )
            button.callback = self._make_on_arbitrate(choice)  # type: ignore[method-assign]
            self.add_item(button)

    def _make_on_arbitrate(self, choice: str) -> Any:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        async def _on_arbitrate(interaction: discord.Interaction) -> None:
            strings = self.strings
            wiring = _wiring()
            if str(getattr(interaction.user, "id", "")) not in (wiring.bot_admins or ()):
                await interaction.response.send_message(strings["dispute_not_admin"], ephemeral=True)
                return
            if wiring.attacks_service is None:
                await interaction.response.send_message(strings["no_service"], ephemeral=True)
                return
            try:
                attack = await wiring.attacks_service.resolve_dispute(self.attack_id, choice)
            except Exception as exc:
                message = getattr(exc, "message", None) or str(exc)
                await interaction.response.send_message(strings["failed"].format(message=message), ephemeral=True)
                return
            if choice == "both":
                await interaction.response.send_message(
                    strings["dispute_forfeit"].format(map=attack.map_key), ephemeral=True
                )
            else:
                kingdom = await _kingdom_name(wiring.kingdoms_service, attack.winner_kingdom_id)
                await interaction.response.send_message(
                    strings["dispute_applied"].format(kingdom=kingdom, map=attack.map_key),
                    ephemeral=True,
                )

        return _on_arbitrate


def _score_players(attack: Any) -> list[str]:
    """Return the human players of the meeting (the AI never declares)."""
    players = [str(attack.attacker_lord_id)]
    if attack.defender_lord_id and not attack.ai_defender:
        players.append(str(attack.defender_lord_id))
    return players


async def _send_score_reminder_dm(bot: Any, attack: Any, locale: str) -> None:
    """Send the 3h no-result reminder DM to each human player (no auto-forfeit)."""
    content = _strings(locale)["score_reminder_dm"].format(map=attack.map_key)
    for player_id in _score_players(attack):
        await _dm_user_safe(bot, player_id, content)


async def _dm_admins_dispute_safe(bot: Any, wiring: Any, attack: Any, strings: dict[str, str]) -> None:
    """DM the bot-admins a disputed meeting with the arbitration buttons."""
    declarations = (attack.score or {}).get("declarations") or {}
    lines = [
        f"• <@{player}> → {decl.get('winner')} (game {decl.get('game_id')})" for player, decl in declarations.items()
    ]
    content = strings["dispute_admin_dm"].format(
        map=attack.map_key,
        declarations="\n".join(lines) or "—",
        game=(attack.score or {}).get("game_id") or "—",
    )
    view = KingdomDisputeView(str(attack.id), "fr")
    for admin_id in wiring.bot_admins or ():
        await _dm_user_safe(bot, str(admin_id), content, view)


async def _run_noshows_safe(
    bot: Any, guild: Any, attacks_service: Any, kingdoms_service: Any, strings: dict[str, str]
) -> int:
    """Announce the T+5 no-show resolutions of the meetings.

    Returns the number of meetings resolved (attaques-panel-spam: any
    resolution changes the panel content, announced or not).
    """
    played = 0
    for attack in await attacks_service.ready_due():
        played += 1
        noshow = (attack.score or {}).get("noshow")
        if not noshow:
            continue
        if noshow == "both":
            await _attack_salon_send_safe(guild, attack, strings["noshow_both"].format(map=attack.map_key))
            continue
        kingdom = await _kingdom_name(kingdoms_service, attack.winner_kingdom_id)
        await _attack_salon_send_safe(
            guild,
            attack,
            strings["noshow_side"].format(map=attack.map_key, side=strings[f"side_{noshow}"], kingdom=kingdom),
        )
        # sabotage-ux-v1 (Drasah 22/10): the room ping wears the winner.
        await _stamp_room_ping_winner_safe(bot, attack, kingdom, strings)
    return played
