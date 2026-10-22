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
        a guided ephemeral flow — kingdom → territory → (cover?) →
        lobby — so the attacker SEES the defense AI level, the patrol
        shields and the cadastre of every option before choosing.
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of, _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None:
            await interaction.response.send_message(strings["not_a_lord"], ephemeral=True)
            return
        content, view = await _kingdom_select_view(
            wiring, str(getattr(lord, "kingdom_id", "") or ""), strings, interaction
        )
        if view is None:
            await interaction.response.send_message(strings["no_target"], ephemeral=True)
            return
        await interaction.response.send_message(content, view=view, ephemeral=True)


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
    wiring: Any, own_kingdom_id: str, strings: dict[str, str], origin: Any = None
) -> tuple[str, discord.ui.View | None]:
    """Step 1 — the attackable kingdoms (patrol-shielded ones stay visible)."""
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
        content, view = await _territory_select_view(wiring, own_kingdom_id, chosen[0], strings, origin)
        if view is None:
            await target.response.edit_message(content=strings["no_target"], view=None)
            return
        await target.response.edit_message(content=content, view=view)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    lines = [str(strings["choose_kingdom"])]
    if protected:
        lines.append(str(strings["protected_line"]).format(kingdoms=" · ".join(protected)))
    lines.append(str(strings["traquenard_hint"]))
    return "\n".join(lines), view


async def _territory_select_view(
    wiring: Any, own_kingdom_id: str, kingdom_id: str, strings: dict[str, str], origin: Any = None
) -> tuple[str, discord.ui.View | None]:
    """Step 2 — the territories of the chosen kingdom (tags + cadastre)."""
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
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:attack:choose:territory",
        placeholder=str(strings["choose_territory"])[:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = getattr(target, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
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
            is_gaia=bool(getattr(kingdom, "is_gaia", False)),
        )

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    # restructuration-serveur-v1 b: the « ⚔️ Itinéraire » line reads the
    # delay LIVE from the config (player vs Gaïa target — never hardcoded,
    # the beta reconfigures and the Corbeau 24h follow-up overrides it
    # per-lord at the declaration itself).
    delay_minutes = _declare_delay_minutes(wiring, kingdom)
    lines = [str(strings["choose_territory"]).format(kingdom=f"**{kingdom.name}**")]
    if delay_minutes:
        lines.append(str(strings["itinerary_line"]).format(minutes=delay_minutes))
    lines.append(str(strings["traquenard_hint"]))
    return "\n".join(lines), view


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
        message = getattr(exc, "message", None) or str(exc)
        await target.followup.send(strings["failed"].format(message=message), ephemeral=True)
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


async def _kingdom_other_lords(wiring: Any, kingdom_id: str, except_user_id: str) -> list[Any]:
    """Return the kingdom's OTHER active lords (the bluff candidates).

    Drasah 18/10 v5: a lone lord cannot bluff anyone — the cover flow
    needs to know it BEFORE offering the OUI/NON question.
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
        if str(lord.kingdom_id) == str(kingdom_id)
        and not getattr(lord, "left", False)
        and str(lord.id) != str(except_user_id)
    ]


async def _open_cover_step(
    wiring: Any,
    interaction: discord.Interaction[Any],
    strings: dict[str, str],
    map_key: str,
    own_kingdom_id: str,
    *,
    is_gaia: bool = False,
) -> None:
    """Step 3 — the counter-espionage question (skipped without a charge).

    A Gaïa target skips the cover entirely: the free-for-all has no
    defender to bluff (Drasah 14/10 — Gaïa is attackable like any
    kingdom, the battle just accepts extra lords).

    Drasah 18/10 v5: a LONE lord cannot bluff anyone either — the
    question is skipped and an explicit note says why (the live beta
    showed a silent jump to the lobby instead).
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
    other_lords = await _kingdom_other_lords(wiring, own_kingdom_id, str(interaction.user.id))
    if not has_stock or not other_lords:
        await _declare_player_attack(wiring, interaction, strings, map_key, None)
        if has_stock:
            await interaction.followup.send(strings["cover_no_lords"], ephemeral=True)
        return
    view = discord.ui.View(timeout=600)

    async def open_lobby(cover_lord_id: str | None, target: discord.Interaction[Any]) -> None:
        await _declare_player_attack(wiring, target, strings, map_key, cover_lord_id)

    async def on_yes(target: discord.Interaction[Any]) -> None:
        lords = await _kingdom_other_lords(wiring, own_kingdom_id, str(target.user.id))
        if not lords:
            # Nobody left to bluff (a departure since the question was
            # shown) — declare openly and say WHY, never silently.
            await open_lobby(None, target)
            await target.followup.send(strings["cover_no_lords"], ephemeral=True)
            return
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:attack:choose:cover",
            placeholder=str(strings["choose_cover"])[:100],
            options=[
                discord.SelectOption(
                    label=str(getattr(lord, "display_name", lord.id))[:100],
                    value=str(lord.id)[:100],
                )
                for lord in lords
            ][:25],
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
    try:
        thread = await message.create_thread(
            name=str(strings["thread_title"]).format(
                kingdom=attacker_kingdom_name, defender=defender_name, map=attack.map_key
            )[:100]
        )
        await thread.send(str(strings["thread_body"]))
    except Exception:
        logger.warning("KINGDOM ATTACK: declaration thread failed", exc_info=True)


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
        """
        from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of, _wiring
        from kingdoms.mods.kingdoms.models import AttackState

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            attack = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id)
        except Exception:
            await interaction.response.send_message(strings["gaia_gone"], ephemeral=True)
            return
        if attack.is_over:
            await interaction.response.send_message(strings["gaia_gone"], ephemeral=True)
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if getattr(attack, "state", None) is not AttackState.DECLARED and not getattr(attack, "ai_defender", False):
            # A defense is already in place — the kingdom is defended
            # (the defender's NAME stays hidden until the reveal).
            await interaction.response.send_message(strings["defend_already"], ephemeral=True)
            return
        if getattr(attack, "state", None) is not AttackState.DECLARED:
            await interaction.response.send_message(strings["gaia_gone"], ephemeral=True)
            return
        if lord is None or str(getattr(lord, "kingdom_id", "") or "") != str(attack.defender_kingdom_id):
            await interaction.response.send_message(strings["defend_wrong_kingdom"], ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            attack = await wiring.attacks_service.respond_defense(self.attack_id, str(interaction.user.id))
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(strings["failed"].format(message=message), ephemeral=True)
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
        from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of, _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["defense_manager_no_service"], ephemeral=True)
            return
        lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
        if lord is None or getattr(lord, "kingdom_id", None) is None:
            await interaction.response.send_message(strings["defense_manager_not_defender"], ephemeral=True)
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
        await interaction.response.send_message(
            strings["defense_manager_title"].format(kingdom=kingdom_name) + "\n" + body,
            view=view if list(view.children) else None,  # type: ignore[arg-type]
            ephemeral=True,
        )


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
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring
        from kingdoms.mods.kingdoms.models import AttackKind

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        battle = next(
            (a for a in await wiring.attacks_service.attacks() if str(a.id) == self.attack_id and not a.is_over),
            None,
        )
        if battle is None or getattr(battle, "kind", None) is not AttackKind.GAIA or battle.state.value != "declared":
            await interaction.response.send_message(strings["gaia_gone"], ephemeral=True)
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
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
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
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
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
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        lines = await _history_lines(wiring, strings)
        if not lines:
            await interaction.response.send_message(strings["history_empty"], ephemeral=True)
            return
        pages = ["\n".join(lines[start : start + 10]) for start in range(0, len(lines), 10)]
        await interaction.response.send_message(
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
            ephemeral=True,
        )


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
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        picks: list[str] = []
        for select in self.rank_selects:
            picks.extend(select.values)
        if len(picks) != len(self.rank_selects) or len(set(picks)) != len(picks):
            await interaction.response.send_message(
                strings["pickban_missing"].format(count=len(self.rank_selects)), ephemeral=True
            )
            return
        try:
            await wiring.attacks_service.set_picks(self.attack_id, self.player_id, picks)
            attack = await wiring.attacks_service.validate_pickban(self.attack_id, self.player_id)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(strings["failed"].format(message=message), ephemeral=True)
            return
        # Symmetry (Drasah 18/10 v2): the SAME answer for the first
        # and the second validator — nobody sees the adversary civs.
        content = strings["pickban_waiting"]
        try:
            await interaction.response.edit_message(content=content, view=None)
        except Exception:
            logger.warning("KINGDOM ATTACK: pick & ban DM edit failed", exc_info=True)
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


def _sabotage_dm_view(attack_id: str, player_id: str, locale: str) -> discord.ui.View:
    """Build the persistent sabotage-phase view of one player's DM (v4).

    Two restart-proof buttons: open the sabotage/Faucon menu, or Pass
    (settle the side). A player with neither sabotage nor Faucon sees
    the notice when OPENING the menu (availability is dynamic).
    """
    strings = _strings(locale)
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomSabotageOpenButton(attack_id, player_id, strings["sab_open_button"]))
    view.add_item(KingdomSabotagePassButton(attack_id, player_id, strings["sab_pass_button"]))
    return view


async def _edit_dm_message_safe(
    bot: Any,
    ref: Any,
    content: str,
    view: discord.ui.View | None,
    files: list[discord.File] | None = None,
) -> bool:
    """Edit a stored DM message (channel_id + message_id) — best effort."""
    try:
        channel = getattr(bot, "get_channel", lambda _id: None)(int(ref["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(ref["channel_id"]))
        message = channel.get_partial_message(int(ref["message_id"]))
        await message.edit(content=content, view=view, files=files)
        return True
    except Exception:
        logger.warning("KINGDOM ATTACK: DM edit failed — falling back to a new DM", exc_info=True)
        return False


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
        enemy = "defender" if side == "attacker" else "attacker"
        shuffled = _shuffled_civs(pickban.get(f"{enemy}_picks"))
        civs = ", ".join(shuffled) or "—"
        content = strings["pickban_reveal_dm"].format(map=attack.map_key, civs=civs)
        # sabotage-ux-v1 (Drasah 22/10): the reveal keeps the stock
        # line visible (the sabotage phase follows right after).
        if wiring is not None:
            content = f"{content}\n\n{await _sabotage_stock_line(wiring, attack, side, strings)}"
        view = _sabotage_dm_view(str(attack.id), player_id, "fr")
        # civ-crests-v1 (Drasah 20/10): the revealed civs wear their
        # blasons — the crest PNGs attach under the text, in the SAME
        # shuffled order as the names (the raw pick order stays hidden).
        files = await civ_crest_files(shuffled)
        ref = pickban.get(f"{side}_dm") or {}
        edited = False
        if ref.get("channel_id") and ref.get("message_id"):
            edited = await _edit_dm_message_safe(bot, ref, content, view, files)
        if not edited:
            await _dm_user_safe(bot, player_id, content, view, files)


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

    def __init__(self, attack_id: str, player_id: str, label: str) -> None:
        key = _ready_key(attack_id, player_id)
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:sab:open:{key}",
                style=discord.ButtonStyle.primary,
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
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            attack = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == self.attack_id)
        except Exception:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        if attack.is_over or (attack.pickban or {}).get("sabotage_resolved"):
            await interaction.response.send_message(strings["sab_over"], ephemeral=True)
            return
        user_id = str(interaction.user.id)
        side = _room_side_of(attack, user_id)
        if side == "":
            await interaction.response.send_message(strings["sab_not_player"], ephemeral=True)
            return
        view = await _sabotage_menu_view(wiring, attack, side, strings)
        if view is None or not list(view.children):
            # Drasah 18/10 v4: no techno bought and no usable Faucon.
            await interaction.response.send_message(strings["sab_no_resources"], ephemeral=True)
            return
        # sabotage-ux-v1 (Drasah 22/10): ONE ephemeral panel per player,
        # edited in place at every action — the panel carries the X/2
        # récap, the removed civs and the stock line, and self-destructs
        # at the 15-minute webhook token limit.
        content, _ = await _sabotage_panel_content(wiring, attack, side, strings)
        await interaction.response.send_message(content, view=view, ephemeral=True)
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
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            fresh = await wiring.attacks_service.sabotage_settle(self.attack_id, self.player_id)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(strings["failed"].format(message=message), ephemeral=True)
            return
        # sabotage-ux-v1 (Drasah 22/10): the Passer click closes the
        # sabotage panel — the player is done with the phase, the panel
        # goes away at once (best effort).
        await _delete_sabotage_panel((self.attack_id, self.player_id))
        if (fresh.pickban or {}).get("sabotage_resolved"):
            await _edit_pickban_dms_phase_done(interaction.client, fresh, strings)
            await interaction.response.send_message(strings["sab_phase_done"].format(map=fresh.map_key), ephemeral=True)
            # Drasah 18/10 v4: both camps answered — the vocal opens
            # at once, BEFORE T (the room carries URL/Prêt/result).
            await _open_meeting_and_room_safe(interaction.client, fresh, wiring, strings, _locale(interaction))
            return
        await interaction.response.send_message(strings["sab_settled"], ephemeral=True)


async def _send_pickban_dm(bot: Any, attack: Any, kingdoms_service: Any, locale: str) -> None:
    """Send the pick & ban DM to each human player (Drasah 18/10 v2)."""
    strings = _strings(locale)
    hand = await _hand_of(kingdoms_service, attack.attacker_kingdom_id)
    adversary = await _hand_of(kingdoms_service, attack.defender_kingdom_id)
    view = KingdomPickBanView(
        str(attack.id),
        str(attack.attacker_lord_id),
        "attacker",
        hand,
        locale,
    )
    # sabotage-ux-v1 (Drasah 22/10): the draft DM carries the stock
    # line — Sabotage is NOT consumed by a use, the Faucon flies 48h.
    wiring = None
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
    except Exception:
        wiring = None
    stock_attacker = await _sabotage_stock_line(wiring, attack, "attacker", strings) if wiring else ""
    message = await _dm_user_safe(
        bot,
        str(attack.attacker_lord_id),
        strings["pickban_dm"].format(
            map=attack.map_key,
            count=min(3, len(hand)) or 1,
            hand=", ".join(hand) or "—",
            theirs=", ".join(adversary) or "—",
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
                hand=", ".join(adversary) or "—",
                theirs=", ".join(hand) or "—",
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
            await _attack_salon_send_safe(
                guild,
                attack,
                strings["reveal_cover"].format(
                    map=attack.map_key,
                    lord=await _lord_name(kingdoms_service, attack.attacker_lord_id),
                ),
            )
        if attack.ai_defender:
            note = strings["reveal_no_defense"]
        elif getattr(attack, "state", None) is AttackState.DEFENDED:
            note = strings["reveal_battle"].format(defender=await _lord_name(kingdoms_service, attack.defender_lord_id))
        else:
            note = strings["reveal_no_defense"]
        await _attack_salon_send_safe(guild, attack, note)
    return played


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


def _final_civ(pickban: dict[str, Any], side: str) -> str:
    """Return the FINAL civilization of one side (post-sabotage, v4).

    The played civ is the FIRST ranked pick the enemy sabotages did
    not strike — the ranking itself stays secret forever.
    """
    picks = [str(civ) for civ in (pickban.get(f"{side}_picks") or [])]
    enemy = "defender" if side == "attacker" else "attacker"
    struck = [str(civ) for civ in (pickban.get(f"{enemy}_sabotages") or [])]
    return next((civ for civ in picks if civ not in struck), "")


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
    their civilization and their OWN Prêt state. A monospace block
    keeps the alignment stable whatever the name lengths — a Prêt
    click edits the player's half in place, never a new message.
    """
    pickban: dict[str, Any] = dict(attack.pickban or {})
    score = dict(attack.score or {})
    ready = {str(player) for player in (score.get("ready") or [])}
    url = str(attack.lobby_url or "").strip()
    attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
    defender = await _lord_name(kingdoms_service, attack.defender_lord_id)

    def _status(player_id: str | None) -> str:
        if not url:
            return strings["room_side_none"]
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
    id_line = strings["room_scoreboard_id"].format(id=url) if url else strings["room_scoreboard_id_missing"]
    rows = [
        _score_center(strings["room_scoreboard_map"].format(map=attack.map_key)),
        _score_center(id_line),
        "",
        _score_row(attacker, defender, strings["room_vs"]),
        _score_row(_final_civ(pickban, "attacker"), _final_civ(pickban, "defender"), ""),
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
                # civ-crests-v1 (Drasah 20/10): the meeting message wears
                # both final civs' blasons, attached under the text. The
                # later content-only edits keep them (attachments survive
                # a Discord edit that does not pass ``files``).
                pickban = dict(attack.pickban or {})
                message = await salon.send(
                    await _room_body_text(attack, kingdoms_service, strings),
                    view=await _room_view(attack, kingdoms_service, strings),
                    files=await civ_crest_files(
                        [
                            _final_civ(pickban, "attacker"),
                            _final_civ(pickban, "defender"),
                        ]
                    ),
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
        for player_id in _score_players(attack):
            await _grant_battle_room_role_safe(guild, role, player_id)
        for player_id in _score_players(attack):
            await _dm_user_safe(
                bot,
                player_id,
                strings["room_dm"].format(
                    map=attack.map_key,
                    room=f"<#{channel.id}>",
                ),
            )
    except Exception:
        logger.warning("KINGDOM ATTACK: battle room opening failed", exc_info=True)


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

    Drasah 22/10: the draft & ban DM tells the player what he owns —
    the Sabotage techno is NOT consumed by a use (the stock stays),
    while the Faucon is a volière slot + a 48h cooldown. Best effort:
    a failing lookup never breaks the DM.
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
) -> tuple[str, discord.ui.View | None]:
    """Build the sabotage PANEL content + its rebuilt menu (sabotage-ux-v1).

    One panel, edited in place: the title carries the X/2 récap, the
    removed-civs line, the stock line. The menu view is None when no
    action is available anymore (the caller sweeps the panel).
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
    return "\n".join(lines), view


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
        content, menu = await _sabotage_panel_content(wiring, fresh, side, strings)
        civ_view = await _sabotage_civ_select_view(wiring, str(attack.id), side, action, strings)
        if civ_view is None:
            await target.response.edit_message(content=f"{content}\n\n{strings['sab_empty']}", view=menu)
            _arm_sabotage_panel_timer(key)
            return
        await target.response.edit_message(content=f"{content}\n\n{strings['sab_choose']}", view=civ_view)
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
    options = [civ for civ in _shuffled_civs(pickban.get(f"{enemy}_picks")) if civ not in already][:25]
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
            await target.response.send_message(strings["failed"].format(message=message), ephemeral=True)
            return
        # sabotage-ux-v1 (Drasah 22/10): the pick edits the SAME panel —
        # récap X/2 + the removed civ + the stock line, no new bubble.
        try:
            fresh = next(item for item in await wiring.attacks_service.attacks() if str(item.id) == attack_id)
        except Exception:
            fresh = attack
        content, menu = await _sabotage_panel_content(wiring, fresh, side, strings)
        used = len([str(civ) for civ in ((fresh.pickban or {}).get(f"{side}_sabotages") or [])])
        note = strings["sab_panel_done"].format(civ=civ, used=used)
        key = (attack_id, user_id)
        try:
            await target.response.edit_message(content=f"{content}\n\n{note}", view=menu)
        except Exception:
            logger.warning("KINGDOM ATTACK: sabotage panel edit failed", exc_info=True)
            await target.response.send_message(note, ephemeral=True)
        if menu is None or not list(menu.children):
            # No action left — sweep the closed panel quickly.
            _arm_sabotage_panel_timer(key, seconds=_SABOTAGE_PANEL_SWEEP_SECONDS)
        else:
            _arm_sabotage_panel_timer(key)

    select.callback = _on_pick  # type: ignore[method-assign,assignment]
    view.add_item(select)
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
