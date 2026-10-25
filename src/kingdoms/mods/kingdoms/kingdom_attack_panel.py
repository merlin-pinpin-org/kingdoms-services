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
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kingdoms.mods.kingdoms.civ_crests import civ_crest_files
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
        "embuscade_button": "🎯 Embuscade ({count} restant)",
        "embuscade_no_stock": "❌ Ton royaume n'a acheté aucune Embuscade sur le marché des technologies.",
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
            " le pseudo d'un autre seigneur, démasqué à la fin du Siège)"
        ),
        "cover_yes_button": "🕶️ OUI — bluffer",
        "cover_no_button": "❌ NON — à visage découvert",
        "choose_cover": "🕶️ Sous le pseudo de quel seigneur déclarer l'attaque ?",
        "cover_no_lords": (
            "🕵️ Ton royaume n'a aucun autre seigneur actif — impossible de bluffer :"
            " l'attaque part à visage découvert (aucune charge consommée)."
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
        "thread_title": "⚔️ {kingdom} 🆚 {defender} — {map}",
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
        "reveal_cover": ("🕶️ Reveal — l'attaque sur **{map}** était menée par **{lord}** (la couverture tombe)."),
        "reveal_ai": (
            "🤖 Personne n'a défendu **{map}** — l'IA du royaume **{kingdom}** prend la défense"
            " (niveau **{level}**/5) !"
        ),
        "reveal_defense": "🛡️ La défense de **{map}** est verrouillée — RDV **{time}** (Paris).",
        "pickban_dm": (
            "⚔️ **Pick & ban — {map}**\nClasse tes **{count}** pick de civilisation par ordre de préférence "
            "(ton classement ordonne les replis).\n"
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
        "pickban_waiting": "⏳ Picks enregistrés et validés — en attente de l'adversaire.",
        "pickban_reveal_dm": (
            "🎭 **Reveal — {map}**\nLes 3 civilisations potentielles de ton"
            " adversaire (ordre aléatoire) :\n**{civs}**\n\n🗡️ Veux-tu saboter"
            " ou utiliser un Faucon ? — jusqu'à 2 civilisations adverses"
            " retirées par camp.\n⚔️ Sinon, valide avec Passer — un joueur"
            " absent du salon vocal à l'heure du RDV est déclaré forfait !"
        ),
        "sab_open_button": "🗡️ Saboter / Utiliser un Faucon",
        "pickban_default_reveal_dm": (
            "🎭 **Reveal — {map}**\n⏱️ Le draft a été clôturé à l'heure : les"
            " civilisations jamais classées ont été tirées au sort (aucun"
            " sabotage ni Faucon).\nLes 3 civilisations potentielles de ton"
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
        "room_ping_winner": "🏆 Vainqueur : **{winner}**",
        "ready_button": "✅ Prêt — confirme ta présence",
        "ready_done": "✅ Présence confirmée — rejoins le salon de la bataille à l'heure du RDV.",
        "room_role": "Bataille {map}",
        "room_scoreboard_map": "⚔️ {map}",
        "room_scoreboard_id": "🆔 ID : {id}",
        "room_scoreboard_id_missing": "🆔 ID à renseigner (bouton ci-dessous)",
        "room_vs": "VS",
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
            "🏟️ **Le salon de la bataille est ouvert — {map}**\n"
            "Rejoins ton adversaire : {room} — le déroulé (ID de la partie, ✅ Prêt,"
            " résultat) vit dans le salon de l'attaque."
        ),
        "result_button": "🏆 Déclarer le vainqueur",
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
        "embuscade_button": "🎯 Ambush ({count} left)",
        "embuscade_no_stock": "❌ Your kingdom bought no Ambush on the technology market.",
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
            " another lord's name, unmasked at the end of the Siege)"
        ),
        "cover_yes_button": "🕶️ YES — bluff",
        "cover_no_button": "❌ NO — open-faced",
        "choose_cover": "🕶️ Under which lord's name should the attack be declared?",
        "cover_no_lords": (
            "🕵️ Your kingdom has no other active lord — no bluff possible: the"
            " attack is declared in the open (no charge consumed)."
        ),
        "announce_v1": (
            "⚔️ {attacker} of kingdom {kingdom} has launched an attack on kingdom"
            " {defender}!\n📅 {date}\nGood luck to the fighters! 🍀"
        ),
        "announce_v1_traquenard": ("\n🪤 A Traquenard is active — the defender kingdom's AI is Easy (6h window)!"),
        "date_full": "{weekday} {day}/{month}/{year} at {hour}:{minute}",
        "weekdays": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "thread_title": "⚔️ {kingdom} 🆚 {defender} — {map}",
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
        "reveal_cover": ("🕶️ Reveal — the attack on **{map}** was led by **{lord}** (the cover drops)."),
        "reveal_ai": (
            "🤖 Nobody defended **{map}** — the AI of kingdom **{kingdom}** takes the defense (level **{level}**/5)!"
        ),
        "reveal_defense": "🛡️ The defense of **{map}** is locked — meeting **{time}** (Paris).",
        "pickban_dm": (
            "⚔️ **Pick & ban — {map}**\nRank your **{count}** civilization picks by"
            " order of preference (your ranking orders the fallbacks).\n"
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
        "pickban_waiting": "⏳ Picks recorded and validated — waiting for the adversary.",
        "pickban_reveal_dm": (
            "🎭 **Reveal — {map}**\nYour adversary's 3 potential civilizations"
            " (random order):\n**{civs}**\n\n🗡️ Do you want to sabotage or use"
            " a Faucon? — up to 2 enemy civilizations removed per side.\n⚔️"
            " Otherwise confirm with Pass — a player missing from the voice"
            " room at the meeting time is a forfeit!"
        ),
        "sab_open_button": "🗡️ Sabotage / Use a Faucon",
        "pickban_default_reveal_dm": (
            "🎭 **Reveal — {map}**\n⏱️ The draft closed on time: the"
            " never-ranked civilizations were drawn at random (no sabotage,"
            " no Faucon).\nYour adversary's 3 potential civilizations"
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
        "room_ping_winner": "🏆 Winner: **{winner}**",
        "ready_button": "✅ Ready — confirm your presence",
        "ready_done": "✅ Presence confirmed — join the battle room at the meeting time.",
        "room_role": "Battle {map}",
        "room_scoreboard_map": "⚔️ {map}",
        "room_scoreboard_id": "🆔 Game ID: {id}",
        "room_scoreboard_id_missing": "🆔 Game ID to enter (button below)",
        "room_vs": "VS",
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
            "🏟️ **The battle room is open — {map}**\n"
            "Join your adversary: {room} — the rundown (game ID, ✅ Ready, result)"
            " lives in the attack's salon."
        ),
        "result_button": "🏆 Declare the winner",
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


async def _attack_salon_send_safe(guild: Any, attack: Any, content: str) -> None:
    """Best-effort announcement in the attack's own salon."""
    channel = _attack_salon(guild, attack)
    if channel is None:
        return
    try:
        await channel.send(content)
    except Exception:
        logger.warning("KINGDOM ATTACK: attack salon announcement failed", exc_info=True)


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

