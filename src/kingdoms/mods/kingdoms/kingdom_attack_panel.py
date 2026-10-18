"""Kingdoms mod attack phases Discord surface (D77).

The pinned panel of the Attaquer salon (marker ``<!-- kingdoms-attack
-->``) lists the ongoing attacks and carries the restart-proof
``kingdoms:attack:declare`` / ``kingdoms:attack:defend:<id>`` buttons.
The scheduler tick (``run_attack_phases_safe``) drives the phases:
the T-15 reveal announcement, the T-5 pick & ban DMs and the
meeting-time forfeits â every step replays safely after a restart
through the service-side flags.

Lesson Check 30: the persistent buttons read ONLY this module's
strings (FR/EN mirrors below).
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kingdoms.mods.kingdoms.game_clock import game_now

logger = logging.getLogger(__name__)

ATTACK_PANEL_MARKER = "<!-- kingdoms-attack -->"
ATTACK_BOARD_MARKER = "<!-- kingdoms-attacks-board -->"
_PARIS = ZoneInfo("Europe/Paris")

_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "panel_title": "âï¸ **Champ de bataille â Attaquer**",
        "panel_help": (
            "DÃ©clarez une attaque sur un territoire adverse â le RDV est fixÃ© Ã  l'heure affichÃ©e.\n"
            "DÃ©roulÃ© : **SiÃ¨ge** (la dÃ©fense s'ouvre Ã  T-15, dans le salon Attaques) â **Draft** "
            "(pick & ban en MP) â **RDV** (confirmez votre prÃ©sence) â **Score** (la game, "
            "dÃ©clarez le rÃ©sultat)."
        ),
        "panel_pointer": "â³ Attaques en cours et dÃ©fenses : rendez-vous dans le salon **Attaques**.",
        "board_title": "ð¡ï¸ **Attaques en cours**",
        "board_empty": "ðï¸ Aucune attaque en cours.",
        "panel_empty": "ðï¸ Aucune attaque en cours.",
        "attack_line": "âï¸ **{map}** â {attacker} ({kingdom}) ð {defender} â RDV **{time}** (Paris) Â· {phase}",
        "gaia_line": "ð¦ **{map}** â bataille GaÃ¯a Â· {count} combattant(s) â RDV **{time}** (Paris)",
        "gaia_join_button": "ð¦ Rejoindre la bataille",
        "gaia_join_note": (
            "ð¦ Tu as rejoint la bataille sur **{map}** ({count} combattants) â RDV **{time}** (heure de Paris)."
        ),
        "gaia_join_announce": "ð¦ **{lord}** rejoint la bataille sur **{map}** ({count} combattants) !",
        "gaia_gone": "â Cette bataille n'accepte plus de participants.",
        "announce_v1_gaia": (
            "\nð¦ D'autres seigneurs peuvent rejoindre la bataille via le bouton"
            " **Rejoindre** du panel Attaquer (jusqu'Ã  {max} combattants) !"
        ),
        "phase_siege": "SiÃ¨ge",
        "phase_draft": "Draft",
        "phase_ready": "RDV",
        "phase_score": "Score",
        "reveal_battle": "âï¸ Bataille en vue (dÃ©fenseur : {defender})",
        "reveal_battle_anon": "âï¸ Bataille en vue",
        "defense_manager_button": "ð¡ï¸ Gestion de la dÃ©fense",
        "defense_manager_title": "ð¡ï¸ Gestion de la dÃ©fense â attaques contre **{kingdom}**",
        "defense_manager_empty": "ðº Aucune attaque en cours contre ton royaume.",
        "defense_manager_row": "â¢ **{map}** â RDV **{time}** (Paris) Â· {state}",
        "defense_manager_defended": "dÃ©fendue par **{lord}** â",
        "defense_manager_waiting": "en attente de dÃ©fenseur â³",
        "defense_manager_not_defender": "â Tu n'es pas un seigneur actif d'un royaume.",
        "defense_manager_no_service": "â Le service des attaques est indisponible.",
        "reveal_no_defense": "ð³ï¸ Assaut irrÃ©sistible â aucune dÃ©fense",
        "defender_pending": "en attente de dÃ©fense",
        "defender_anon": "dÃ©fense en place ð",
        "defender_ai": "IA ð¤",
        "covered_tag": " ð¶ï¸",
        "declare_button": "âï¸ DÃ©clarer une attaque",
        "defend_button": "ð¡ï¸ DÃ©fendre",
        "modal_title": "âï¸ DÃ©clarer une attaque",
        "modal_map": "Territoire adverse (clÃ© de carte)",
        "modal_lobby": "URL du lobby de jeu",
        "modal_cover": "Seigneur de couverture (mention, optionnel)",
        "modal_traquenard": "Traquenard ? (oui/non)",
        "declared_note": "âï¸ Attaque dÃ©clarÃ©e sur **{map}** â RDV Ã  **{time}** (heure de Paris).",
        "declared_announce": (
            "âï¸ **{attacker}** ({kingdom}) attaque **{map}** ð {defender_kingdom} â RDV **{time}** (heure de Paris) !"
        ),
        "defense_done": "ð¡ï¸ **{map}** â tu portes la dÃ©fense de ton royaume ! RDV **{time}** (Paris).",
        "failed": "â {message}",
        "not_a_lord": "â Tu n'es pas un seigneur actif d'un royaume.",
        "no_service": "â Le service des attaques est indisponible.",
        "no_target": "â Aucun royaume adverse attaquable pour le moment.",
        "choose_kingdom": "ð¯ **Choisis le royaume Ã  attaquer :**",
        "protected_line": "ð¡ï¸ Sous patrouille (impossible d'attaquer) : {kingdoms}",
        "patrol_kingdom": "**{name}** jusqu'Ã  {end}h00",
        "ai_level_desc": "IA de dÃ©fense : {level}/5 Â· {count} territoire(s)",
        "choose_territory": "ðºï¸ **Choisis le territoire de {kingdom} Ã  attaquer :**",
        "traquenard_hint": (
            "ðª¤ Info : un royaume peut activer un Traquenard sur son adversaire â l'IA de"
            " dÃ©fense devient Facile pendant 6h et les mariages scellÃ©s prennent +2h de verrou."
        ),
        "cover_question": (
            "ð¶ï¸ Utiliser un contre-espionnage ? (1 charge â l'attaque sera annoncÃ©e sous"
            " le pseudo d'un autre seigneur, dÃ©masquÃ© Ã  la fin du SiÃ¨ge)"
        ),
        "cover_yes_button": "ð¶ï¸ OUI â bluffer",
        "cover_no_button": "â NON â Ã  visage dÃ©couvert",
        "choose_cover": "ð¶ï¸ Sous le pseudo de quel seigneur dÃ©clarer l'attaque ?",
        "announce_v1": (
            "âï¸ {attacker} du royaume {kingdom} a lancÃ© une attaque sur le royaume"
            " {defender} !\nð {date}\nBonne chance aux combattants ! ð"
        ),
        "announce_v1_traquenard": (
            "\nðª¤ Un Traquenard est actif â l'IA du royaume dÃ©fenseur est Facile (fenÃªtre 6h) !"
        ),
        "date_full": "{weekday} {day}/{month}/{year} Ã  {hour}h{minute}",
        "weekdays": "Lundi,Mardi,Mercredi,Jeudi,Vendredi,Samedi,Dimanche",
        "thread_title": "âï¸ {kingdom} ð {defender} â {map}",
        "thread_body": (
            "ð **DÃ©roulÃ© de l'attaque & droits du dÃ©fenseur**\n"
            "â¢ **SiÃ¨ge** â la dÃ©fense reste ouverte ; Ã  la fin du SiÃ¨ge (reveal), le bluff"
            " (s'il y en a un) tombe et sans dÃ©fenseur l'IA du royaume prend la dÃ©fense.\n"
            "â¢ **Draft** â le pick & ban part en MP : 3 civilisations classÃ©es de votre"
            " main, validation des deux cÃ´tÃ©s (les civs partent au salon de la bataille).\n"
            "â¢ **RDV** â un salon vocal de bataille s'ouvre Ã  l'heure du RDV : une absence"
            " au vocal est un forfait.\n"
            "â¢ **Score** â la game : dÃ©clarez le rÃ©sultat avec l'ID de la game.\n"
            "ð¡ï¸ N'importe quel seigneur du royaume dÃ©fenseur peut dÃ©fendre via le"
            " bouton du panel **DÃ©clarer une attaque**.\n"
            "ðª¤ Un royaume peut activer un Traquenard sur son adversaire : l'IA de"
            " dÃ©fense devient Facile pendant 6h et les mariages scellÃ©s prennent +2h"
            " de verrou."
        ),
        "reveal_cover": ("ð¶ï¸ Reveal â l'attaque sur **{map}** Ã©tait menÃ©e par **{lord}** (la couverture tombe)."),
        "reveal_ai": (
            "ð¤ Personne n'a dÃ©fendu **{map}** â l'IA du royaume **{kingdom}** prend la dÃ©fense"
            " (niveau **{level}**/5) !"
        ),
        "reveal_defense": "ð¡ï¸ La dÃ©fense de **{map}** est verrouillÃ©e â RDV **{time}** (Paris).",
        "pickban_dm": (
            "âï¸ **Pick & ban â {map}**\nClasse tes **{count}** pick de civilisation par ordre de prÃ©fÃ©rence "
            "(ton classement ordonne les replis).\n"
            "Si tu possÃ¨des un Sabotage ou un Faucon, tu pourras en faire usage dans le salon de la bataille.\n"
            "\n"
            "â ï¸ **RÃ¨gle anti-blocage** : si tu ne valides aucun choix avant le RDV, "
            "3 civilisations seront tirÃ©es **au hasard** de ta main â et aucun "
            "sabotage ni faucon ne s'appliquera. Le RDV a lieu quoi qu'il arrive.\n"
            "Ta main : {hand}\nMain adverse : {theirs}"
        ),
        "pickban_pick": "Choix {n}",
        "pickban_validate": "â Valider",
        "pickban_missing": "â Classe d'abord tes {count} civilisations.",
        "pickban_waiting": "â³ Picks enregistrÃ©s et validÃ©s â en attente de l'adversaire.",
        "pickban_ready_prompt": (
            "âï¸ **Draft rÃ©solu â {map}**\nLes civilisations partent au salon de la"
            " bataille Ã  l'heure du RDV.\nConfirme ta prÃ©sence avec le bouton"
            " ci-dessous â un joueur absent du salon vocal Ã  l'heure du RDV est"
            " dÃ©clarÃ© forfait !"
        ),
        "ready_button": "â PrÃªt â confirme ta prÃ©sence",
        "ready_done": "â PrÃ©sence confirmÃ©e â rejoins le salon de la bataille Ã  l'heure du RDV.",
        "room_category": "Champs de bataille",
        "room_title": "{map} â {attacker} ð {defender}",
        "room_role": "Bataille {map}",
        "room_body": (
            "ðï¸ **PrÃ©parez-vous Ã  la bataille â {map}**\n"
            "{lines}\n"
            "Sabotage ou usage du Faucon : via ce salon textuel.\n"
            "ð partagez l'URL de la partie Â· ð dÃ©clarez le rÃ©sultat Ã  la fin."
        ),
        "room_dm": (
            "ðï¸ **Le salon de la bataille est ouvert â {map}**\n"
            "Rejoins ton adversaire : {room} â et surveille le salon textuel du"
            " vocal (URL de la partie, rÃ©sultat, sabotages)."
        ),
        "result_button": "ð DÃ©clarer le rÃ©sultat",
        "result_select_title": "ð **Qui remporte la partie ?**",
        "result_select_placeholder": "Le vainqueur de la game",
        "score_url_posted": "ð **URL de la partie** : {url}",
        "score_pending": (
            "â³ RÃ©sultat enregistrÃ© (game {game}) â il sera appliquÃ© sauf"
            " contestation de l'autre joueur dans les {minutes} prochaines minutes."
        ),
        "forfeit_both": (
            "ð³ï¸ Forfait gÃ©nÃ©ral sur **{map}** â personne n'a validÃ© : l'attaquant, prÃ©sent, l'emporte (**{kingdom}**)."
        ),
        "forfeit_side": "ð³ï¸ Forfait sur **{map}** â **{side}** a manquÃ© la validation : victoire de **{kingdom}**.",
        "side_attacker": "l'attaquant",
        "side_defender": "le dÃ©fenseur",
        "score_url_button": "ð URL de la partie",
        "score_url_modal_title": "URL de la partie",
        "score_url_modal_label": "URL du lobby de jeu",
        "score_url_modal_placeholder": "aoe2de://â¦",
        "score_url_set": "â URL enregistrÃ©e â elle est visible dans le salon de la bataille.",
        "score_reminder_dm": (
            "â° **RDV â {map}** : aucun rÃ©sultat dÃ©clarÃ© depuis 3h â dÃ©clare le"
            " rÃ©sultat de la game avec le bouton ð du salon de la bataille !"
        ),
        "score_modal_title": "RÃ©sultat de la game",
        "score_modal_label": "ID de la game (requis)",
        "score_modal_placeholder": "ex. 123456",
        "score_resolved": ("ð RÃ©sultat enregistrÃ© â **{winner}** remporte **{map}** (game {game})."),
        "score_already": "â Ce rÃ©sultat Ã©tait dÃ©jÃ  enregistrÃ© â rien Ã  faire.",
        "score_disputed": (
            "âï¸ DÃ©claration contradictoire â les admins arbitrent (game {game})."
        ),
        "noshow_side": ("ð³ï¸ **{map}** â **{side}** a manquÃ© le RDV : victoire de **{kingdom}**."),
        "noshow_both": ("ð³ï¸ **{map}** â double forfait : aucun joueur au RDV, le territoire reste Ã  son propriÃ©taire."),
        "dispute_admin_dm": ("âï¸ **Litige â {map}**\n{declarations}\nTranche le rÃ©sultat (game {game})."),
        "auto_dispute": (
            "âï¸ **Litige auto-ouvert sur {map}** â aucun rÃ©sultat dÃ©clarÃ© 15 h aprÃ¨s"
            " l'ouverture de la partie : un admin tranche via la console (`resultat`)."
        ),
        "dispute_win_attacker": "âï¸ Tranche : attaquant",
        "dispute_win_defender": "âï¸ Tranche : dÃ©fenseur",
        "dispute_double": "ð³ï¸ Double forfait",
        "dispute_applied": "âï¸ Arbitrage appliquÃ© â **{kingdom}** remporte **{map}**.",
        "dispute_forfeit": "âï¸ Arbitrage appliquÃ© â double forfait sur **{map}**.",
        "dispute_not_admin": "â Seuls les bot-admins arbitrent un litige.",
    },
    "en": {
        "panel_title": "âï¸ **Battlefield â Attack**",
        "panel_help": (
            "Declare an attack on an adversary territory â the meeting is set at the displayed time.\n"
            "Flow: **Siege** (the defense opens at T-15, in the Attaques salon) â **Draft** "
            "(DM pick & ban) â **Meeting** (confirm your presence) â **Score** (the game, "
            "declare the result)."
        ),
        "panel_pointer": "â³ Ongoing attacks and defenses: see the **Attaques** salon.",
        "board_title": "ð¡ï¸ **Ongoing attacks**",
        "board_empty": "ðï¸ No ongoing attack.",
        "panel_empty": "ðï¸ No ongoing attack.",
        "attack_line": "âï¸ **{map}** â {attacker} ({kingdom}) ð {defender} â meets **{time}** (Paris) Â· {phase}",
        "gaia_line": "ð¦ **{map}** â GaÃ¯a battle Â· {count} fighter(s) â meets **{time}** (Paris)",
        "gaia_join_button": "ð¦ Join the battle",
        "gaia_join_note": (
            "ð¦ You joined the battle on **{map}** ({count} fighters) â meeting **{time}** (Paris time)."
        ),
        "gaia_join_announce": "ð¦ **{lord}** joins the battle on **{map}** ({count} fighters)!",
        "gaia_gone": "â This battle no longer accepts participants.",
        "announce_v1_gaia": (
            "\nð¦ Other lords may join the battle through the **Join** button of"
            " the Attack panel (up to {max} fighters)!"
        ),
        "phase_siege": "Siege",
        "phase_draft": "Draft",
        "phase_ready": "Meeting",
        "phase_score": "Score",
        "reveal_battle": "âï¸ Battle ahead (defender: {defender})",
        "reveal_battle_anon": "âï¸ Battle ahead",
        "defense_manager_button": "ð¡ï¸ Defense management",
        "defense_manager_title": "ð¡ï¸ Defense management â attacks against **{kingdom}**",
        "defense_manager_empty": "ðº No ongoing attack against your kingdom.",
        "defense_manager_row": "â¢ **{map}** â meeting **{time}** (Paris) Â· {state}",
        "defense_manager_defended": "defended by **{lord}** â",
        "defense_manager_waiting": "waiting for a defender â³",
        "defense_manager_not_defender": "â You are no active lord of a kingdom.",
        "defense_manager_no_service": "â The attack service is unavailable.",
        "reveal_no_defense": "ð³ï¸ Irresistible assault â no defense",
        "defender_pending": "waiting for a defender",
        "defender_anon": "defense in place ð",
        "defender_ai": "AI ð¤",
        "covered_tag": " ð¶ï¸",
        "declare_button": "âï¸ Declare an attack",
        "defend_button": "ð¡ï¸ Defend",
        "modal_title": "âï¸ Declare an attack",
        "modal_map": "Adversary territory (map key)",
        "modal_lobby": "Game lobby URL",
        "modal_cover": "Cover lord (mention, optional)",
        "modal_traquenard": "Ambush trap? (yes/no)",
        "declared_note": "âï¸ Attack declared on **{map}** â meeting at **{time}** (Paris time).",
        "declared_announce": (
            "âï¸ **{attacker}** ({kingdom}) attacks **{map}** ð {defender_kingdom} â meeting **{time}** (Paris time)!"
        ),
        "defense_done": "ð¡ï¸ **{map}** â you carry your kingdom's defense! Meeting **{time}** (Paris).",
        "failed": "â {message}",
        "not_a_lord": "â You are no active lord of a kingdom.",
        "no_service": "â The attack service is unavailable.",
        "no_target": "â No adversary kingdom is attackable right now.",
        "choose_kingdom": "ð¯ **Choose the kingdom to attack:**",
        "protected_line": "ð¡ï¸ Under patrol (cannot be attacked): {kingdoms}",
        "patrol_kingdom": "**{name}** until {end}:00",
        "ai_level_desc": "Defense AI: {level}/5 Â· {count} territor(y|ies)",
        "choose_territory": "ðºï¸ **Choose the territory of {kingdom} to attack:**",
        "traquenard_hint": (
            "ðª¤ Info: a kingdom may activate a Traquenard on its adversary â the defense"
            " AI turns Easy for 6h and sealed marriages gain +2h of lock."
        ),
        "cover_question": (
            "ð¶ï¸ Use a counter-espionage? (1 charge â the attack is announced under"
            " another lord's name, unmasked at the end of the Siege)"
        ),
        "cover_yes_button": "ð¶ï¸ YES â bluff",
        "cover_no_button": "â NO â open-faced",
        "choose_cover": "ð¶ï¸ Under which lord's name should the attack be declared?",
        "announce_v1": (
            "âï¸ {attacker} of kingdom {kingdom} has launched an attack on kingdom"
            " {defender}!\nð {date}\nGood luck to the fighters! ð"
        ),
        "announce_v1_traquenard": ("\nðª¤ A Traquenard is active â the defender kingdom's AI is Easy (6h window)!"),
        "date_full": "{weekday} {day}/{month}/{year} at {hour}:{minute}",
        "weekdays": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "thread_title": "âï¸ {kingdom} ð {defender} â {map}",
        "thread_body": (
            "ð **Attack rundown & defender rights**\n"
            "â¢ **Siege** â the defense stays open; at the end of the Siege (reveal), the"
            " bluff (if any) drops and with no defender the kingdom's AI takes the defense.\n"
            "â¢ **Draft** â the pick & ban lands in the DMs: 3 ranked civilizations,"
            " both sides validate (the civs land in the battle room).\n"
            "â¢ **Meeting** â a battle voice room opens at the meeting time: a miss"
            " from the voice room is a forfeit.\n"
            "â¢ **Score** â the game: declare the result with the game ID.\n"
            "ð¡ï¸ Any lord of the defending kingdom may defend via the button of the"
            " **Declare an attack** panel.\n"
            "ðª¤ A kingdom may activate a Traquenard on its adversary: the defense AI"
            " turns Easy for 6h and sealed marriages gain +2h of lock."
        ),
        "reveal_cover": ("ð¶ï¸ Reveal â the attack on **{map}** was led by **{lord}** (the cover drops)."),
        "reveal_ai": (
            "ð¤ Nobody defended **{map}** â the AI of kingdom **{kingdom}** takes the defense (level **{level}**/5)!"
        ),
        "reveal_defense": "ð¡ï¸ The defense of **{map}** is locked â meeting **{time}** (Paris).",
        "pickban_dm": (
            "âï¸ **Pick & ban â {map}**\nRank your **{count}** civilization picks by"
            " order of preference (your ranking orders the fallbacks).\n"
            "If you own a Sabotage or a Faucon, you will use it in the battle room.\n"
            "\n"
            "â ï¸ **Anti-blocking rule**: no choice validated before the meeting â "
            "3 **random** civilizations from your hand â and no sabotage or faucon "
            "applies. The meeting happens anyway.\n"
            "Your hand: {hand}\nAdversary hand: {theirs}"
        ),
        "pickban_pick": "Choice {n}",
        "pickban_validate": "â Validate",
        "pickban_missing": "â Rank your {count} civilizations first.",
        "pickban_waiting": "â³ Picks recorded and validated â waiting for the adversary.",
        "pickban_ready_prompt": (
            "âï¸ **Draft resolved â {map}**\nThe civilizations land in the battle"
            " room at the meeting time.\nConfirm your presence with the button"
            " below â a player missing from the voice room at the meeting time"
            " is a forfeit!"
        ),
        "ready_button": "â Ready â confirm your presence",
        "ready_done": "â Presence confirmed â join the battle room at the meeting time.",
        "room_category": "Battlefields",
        "room_title": "{map} â {attacker} ð {defender}",
        "room_role": "Battle {map}",
        "room_body": (
            "ðï¸ **Prepare for battle â {map}**\n"
            "{lines}\n"
            "Sabotage or Faucon use: through this text room.\n"
            "ð share the game URL Â· ð declare the result at the end."
        ),
        "room_dm": (
            "ðï¸ **The battle room is open â {map}**\n"
            "Join your adversary: {room} â and watch the voice room's text"
            " channel (game URL, result, sabotages)."
        ),
        "result_button": "ð Declare the result",
        "result_select_title": "ð **Who wins the game?**",
        "result_select_placeholder": "The game's winner",
        "score_url_posted": "ð **Game URL**: {url}",
        "score_pending": (
            "â³ Result recorded (game {game}) â it applies unless the other"
            " player contests it within the next {minutes} minutes."
        ),
        "forfeit_both": (
            "ð³ï¸ General forfeit on **{map}** â nobody validated: the attacker, present, wins (**{kingdom}**)."
        ),
        "forfeit_side": "ð³ï¸ Forfeit on **{map}** â **{side}** missed the validation: victory of **{kingdom}**.",
        "side_attacker": "the attacker",
        "side_defender": "the defender",
        "score_url_button": "ð Game URL",
        "score_url_modal_title": "Game URL",
        "score_url_modal_label": "Game lobby URL",
        "score_url_modal_placeholder": "aoe2de://â¦",
        "score_url_set": "â URL saved â visible in the battle room.",
        "score_reminder_dm": (
            "â° **Meeting â {map}**: no result declared for 3 hours â declare the"
            " game result with the ð button of the battle room!"
        ),
        "score_modal_title": "Game result",
        "score_modal_label": "Game ID (required)",
        "score_modal_placeholder": "e.g. 123456",
        "score_resolved": ("ð Result recorded â **{winner}** wins **{map}** (game {game})."),
        "score_already": "â This result was already recorded â nothing to do.",
        "score_disputed": ("âï¸ Contradictory declarations â the admins arbitrate (game {game})."),
        "noshow_side": ("ð³ï¸ **{map}** â **{side}** missed the meeting: victory of **{kingdom}**."),
        "noshow_both": ("ð³ï¸ **{map}** â double forfeit: no player at the meeting, the territory stays with its owner."),
        "dispute_admin_dm": ("âï¸ **Dispute â {map}**\n{declarations}\nCut the result (game {game})."),
        "auto_dispute": (
            "âï¸ **Dispute auto-opened on {map}** â no result declared 15h after the"
            " game opened: an admin arbitrates through the console (`resultat`)."
        ),
        "dispute_win_attacker": "âï¸ Rule: attacker",
        "dispute_win_defender": "âï¸ Rule: defender",
        "dispute_double": "ð³ï¸ Double forfeit",
        "dispute_applied": "âï¸ Arbitration applied â **{kingdom}** wins **{map}**.",
        "dispute_forfeit": "âï¸ Arbitration applied â double forfeit on **{map}**.",
        "dispute_not_admin": "â Only bot-admins arbitrate a dispute.",
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


DECLARE_CHANNEL_NAME = "DÃ©clarer une attaque"
ANNOUNCE_CHANNEL_NAME = "Attaques"


def _declare_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    """Return the guild's Â« DÃ©clarer une attaque Â» salon (None when absent).

    Patch restructuration-royaume: the legacy Â« Attaquer Â» salon is the
    declared fallback â the provisioning adopts it by key and
    ``deploy_attack_panel`` renames it in place.
    """
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if guild is None:
        return None
    wanted = _slug(DECLARE_CHANNEL_NAME)
    channels = [c for c in getattr(guild, "text_channels", []) if _slug(getattr(c, "name", "")) in (wanted, "attaquer")]
    return next((c for c in channels if _slug(c.name) == wanted), channels[0] if channels else None)


def _announce_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    """Return the guild's Â« Attaques Â» announce salon (None when absent)."""
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if guild is None:
        return None
    wanted = _slug(ANNOUNCE_CHANNEL_NAME)
    return next(
        (c for c in getattr(guild, "text_channels", []) if _slug(getattr(c, "name", "")) == wanted),
        None,
    )


async def _announce_safe(channel: discord.TextChannel | None, content: str) -> None:
    """Best-effort announcement in the Attaquer salon."""
    if channel is None:
        return
    try:
        await channel.send(content)
    except Exception:
        logger.warning("KINGDOM ATTACK: announcement failed", exc_info=True)


async def _lord_name(kingdoms_service: Any, lord_id: str | None) -> str:
    """Resolve a lord display name (the raw id as fallback)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    if not lord_id:
        return "â"
    lord = await _lord_of(kingdoms_service, lord_id)
    if lord is None:
        return lord_id
    return str(getattr(lord, "display_name", None) or getattr(lord, "name", None) or lord_id)


async def _kingdom_name(kingdoms_service: Any, kingdom_id: str | None) -> str:
    """Resolve a kingdom display name (the raw id as fallback)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _kingdom_by_id_safe

    kingdom = await _kingdom_by_id_safe(kingdoms_service, kingdom_id or "")
    if kingdom is None:
        return str(kingdom_id or "â")
    return str(getattr(kingdom, "name", None) or kingdom_id)


def _declare_view(strings: dict[str, str]) -> discord.ui.View:
    """Return the CLEAN declare panel view: one declare button, nothing else.

    Drasah 17/10: Â« DÃ©clarer une attaque Â» stays clean â the ongoing
    attacks (and their defend/join buttons) live in the Attaques salon.
    """
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomAttackDeclareButton(strings["declare_button"]))
    return view


def _board_view(strings: dict[str, str], attacks: list[Any]) -> discord.ui.View:
    """Return the Attaques salon board view: defend/join per open attack."""
    from kingdoms.mods.kingdoms.models import AttackKind

    view = discord.ui.View(timeout=None)
    for attack in attacks:
        state = str(getattr(getattr(attack, "state", None), "value", getattr(attack, "state", "")))
        if state != "declared":
            continue
        kind = getattr(attack, "kind", None)
        if kind is AttackKind.GAIA:
            view.add_item(KingdomAttackJoinButton(str(attack.id), strings["gaia_join_button"]))
        elif kind is None or kind is AttackKind.PLAYER:
            view.add_item(KingdomAttackDefendButton(str(attack.id), strings["defend_button"]))
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


def board_text(strings: dict[str, str], rows: list[str]) -> str:
    """Render the Attaques board message (title, live rows, marker)."""
    body = rows if rows else [strings["board_empty"]]
    return "\n".join((strings["board_title"], "", *body, ATTACK_BOARD_MARKER))


async def _ongoing_rows(
    attacks_service: Any, kingdoms_service: Any, strings: dict[str, str]
) -> tuple[list[str], list[Any]]:
    """Return the live rows of the ongoing attacks and the attacks themselves."""
    from kingdoms.mods.kingdoms.attacks import phase_label
    from kingdoms.mods.kingdoms.models import AttackKind, AttackState

    rows: list[str] = []
    attacks: list[Any] = []
    try:
        attacks = [a for a in await attacks_service.attacks() if not a.is_over]
    except Exception:
        logger.warning("KINGDOM ATTACK: attack list read failed", exc_info=True)
        return [], []
    for attack in attacks:
        if getattr(attack, "kind", None) is AttackKind.GAIA:
            # A GaÃ¯a battle is a free-for-all other lords may join.
            rows.append(
                strings["gaia_line"].format(
                    map=attack.map_key,
                    count=len(getattr(attack, "participants", None) or []),
                    time=_fmt_paris(attack.expires_at),
                )
            )
            continue
        if getattr(attack, "kind", "player") is not None and str(getattr(attack, "kind", "")) != "player":
            continue  # unknown kinds keep their own surface
        if not attack.revealed and attack.cover_lord_id:
            attacker = await _lord_name(kingdoms_service, attack.cover_lord_id)
            attacker += strings["covered_tag"]
        else:
            attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
        kingdom = await _kingdom_name(kingdoms_service, attack.attacker_kingdom_id)
        # Drasah 18/10 â effet de surprise : le nom du dÃ©fenseur humain
        # n'apparaÃ®t JAMAIS sur le board public ; le royaume dÃ©fenseur
        # le voit via la Gestion de la dÃ©fense (Remparts).
        if getattr(attack, "state", None) is AttackState.DEFENDED:
            defender = (
                strings["defender_ai"] if attack.ai_defender else strings["defender_anon"]
            )
        else:
            defender = strings["defender_pending"]
        phase = _phase_display(phase_label(attack), strings)
        # attaques-panel-spam (Drasah 15/10): at the T-15 reveal the line
        # shows the defense window's outcome instead of the bare phase â
        # Â« Bataille en vue Â» when a lord defends, Â« Assaut irrÃ©sistible Â»
        # when nobody did (AI fallback). Nothing new to compute: the
        # reveal (reveal_due) already resolved cover/AI/locked defense.
        # Drasah 18/10 â effet de surprise : le board public ne nomme
        # JAMAIS le dÃ©fenseur humain ; seul le royaume dÃ©fenseur le
        # voit (Gestion de la dÃ©fense, Remparts).
        if getattr(attack, "revealed", False) and not attack.is_over:
            if attack.ai_defender:
                phase = strings["reveal_no_defense"]
            elif getattr(attack, "state", None) is AttackState.DEFENDED:
                phase = strings["reveal_battle_anon"]
        rows.append(
            strings["attack_line"].format(
                map=attack.map_key,
                attacker=attacker,
                kingdom=kingdom,
                defender=defender,
                time=_fmt_paris(attack.expires_at),
                phase=phase,
            )
        )
    return rows, attacks


async def deploy_attack_panel(
    channel: discord.TextChannel | None,
    locale: str,
    kingdoms_service: Any = None,
    attacks_service: Any = None,
) -> None:
    """Pin (or refresh) the CLEAN declare panel in the DÃ©clarer une attaque salon.

    Drasah 17/10: this salon keeps ONLY the declare flow â the ongoing
    attacks (rows, defend/join buttons) moved to the Attaques salon
    (``deploy_attacks_board``). The service params stay for API
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
    # attaques-panel-spam (Drasah 15/10): the panel is EDITED in place â
    # delete+send on every call multiplied messages whenever the old
    # message delete failed (rate limit, out of the history scan depth).
    # The scan reads deep (200) so a rate-limited delete of the previous
    # cycle cannot let two panels coexist.
    panel_message = await _find_panel_message(channel)
    if panel_message is not None:
        if (panel_message.content or "") == content:
            return  # unchanged â nothing to touch (no API call at all)
        try:
            await panel_message.edit(content=content, view=view)
            return
        except Exception:
            logger.warning("KINGDOM ATTACK: panel edit failed", exc_info=True)
    try:
        await channel.send(content=content, view=view)
    except Exception:
        logger.warning("KINGDOM ATTACK: panel send failed", exc_info=True)


async def deploy_attacks_board(
    channel: discord.TextChannel | None,
    locale: str,
    kingdoms_service: Any = None,
    attacks_service: Any = None,
) -> None:
    """Pin (or refresh) the ongoing-attacks board in the Attaques salon.

    Drasah 17/10: the ongoing attacks and their defend/join buttons
    moved here â Â« DÃ©clarer une attaque Â» keeps only the declare flow.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    if channel is None:
        return
    wiring = _wiring()
    kingdoms_service = kingdoms_service or wiring.kingdoms_service
    attacks_service = attacks_service or wiring.attacks_service
    strings = _strings(locale)
    rows: list[str] = []
    attacks: list[Any] = []
    if attacks_service is not None:
        rows, attacks = await _ongoing_rows(attacks_service, kingdoms_service, strings)
    content = board_text(strings, rows)
    view = _board_view(strings, attacks)
    board_message = await _find_panel_message(channel, ATTACK_BOARD_MARKER)
    if board_message is not None:
        if (board_message.content or "") == content:
            return  # unchanged â nothing to touch (no API call at all)
        try:
            await board_message.edit(content=content, view=view)
            return
        except Exception:
            logger.warning("KINGDOM ATTACK: board edit failed", exc_info=True)
    try:
        await channel.send(content=content, view=view)
    except Exception:
        logger.warning("KINGDOM ATTACK: board send failed", exc_info=True)


async def _find_panel_message(
    channel: discord.TextChannel, marker: str = ATTACK_PANEL_MARKER
) -> Any | None:
    """Return the live panel message of the salon, pruning the duplicates.

    attaques-panel-spam: the scan reads deep (200) so a rate-limited
    delete of a previous cycle cannot let two panels coexist â the
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
    """Re-pin the guild's declare panel and attacks board after a mutation."""
    await deploy_attack_panel(_declare_channel(guild), locale)
    await deploy_attacks_board(_announce_channel(guild), locale)


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
        """Rebuild the button from the wire â THIS module's strings only."""
        return cls(_strings(_locale(interaction))["declare_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard the click, then open the GUIDED declaration flow.

        Drasah's recÃ¢blage (2026-10-14): the bare modal is replaced by
        a guided ephemeral flow â kingdom â territory â (cover?) â
        lobby â so the attacker SEES the defense AI level, the patrol
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
        content, view = await _kingdom_select_view(wiring, str(getattr(lord, "kingdom_id", "") or ""), strings)
        if view is None:
            await interaction.response.send_message(strings["no_target"], ephemeral=True)
            return
        await interaction.response.send_message(content, view=view, ephemeral=True)


def _fmt_paris_full(moment: Any, strings: dict[str, str]) -> str:
    """Format a moment as Â« Vendredi 29/08/2025 Ã  19h15 Â» (the players' clock)."""
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
    ("eau", "ð§"),
    ("hybride", "ð"),
    ("objectif", "ð¯"),
    ("nomade", "ð¾"),
    ("foret", "ð²"),
    ("montagne", "â°ï¸"),
    ("desert", "ðï¸"),
    ("marais", "ðª·"),
    ("ouverte", "ð"),
    ("chaotique", "ð"),
    ("mur_depart", "ð§±"),
    ("ferme", "ð"),
)


def _map_description(entry: Any, strings: dict[str, str]) -> str:
    """One select-option description: the map's tags + cadastre bonus."""
    types = getattr(entry, "types", None)
    tags = [icon for key, icon in _MAP_TAG_KEYS if bool(getattr(types, key, False))]
    bonus = str((getattr(entry, "cadastre", None) or {}).get("bonus", "") or "")
    parts = [" ".join(tags)] if tags else []
    if bonus:
        parts.append(bonus)
    return " Â· ".join(parts)[:100]


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
            # GaÃ¯a territories are attackable through the free-for-all
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
    wiring: Any, own_kingdom_id: str, strings: dict[str, str]
) -> tuple[str, discord.ui.View | None]:
    """Step 1 â the attackable kingdoms (patrol-shielded ones stay visible)."""
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
        content, view = await _territory_select_view(wiring, own_kingdom_id, chosen[0], strings)
        if view is None:
            await target.response.edit_message(content=strings["no_target"], view=None)
            return
        await target.response.edit_message(content=content, view=view)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    lines = [str(strings["choose_kingdom"])]
    if protected:
        lines.append(str(strings["protected_line"]).format(kingdoms=" Â· ".join(protected)))
    lines.append(str(strings["traquenard_hint"]))
    return "\n".join(lines), view


async def _territory_select_view(
    wiring: Any, own_kingdom_id: str, kingdom_id: str, strings: dict[str, str]
) -> tuple[str, discord.ui.View | None]:
    """Step 2 â the territories of the chosen kingdom (tags + cadastre)."""
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
    return str(strings["choose_territory"]).format(kingdom=f"**{kingdom.name}**"), view


async def _declare_player_attack(
    wiring: Any,
    target: discord.Interaction[Any],
    strings: dict[str, str],
    map_key: str,
    cover_lord_id: str | None,
) -> None:
    """Declare a player attack at step 3 â no modal anymore.

    Live beta 18/10: the lobby URL field was removed from the
    declaration (it moved to the RDV DM's ð button) and the modal
    was left with ZERO component â Discord refuses an empty modal
    (400 Bad Request), the callback died and the attacker saw
    Â« Kingdoms n'a pas rÃ©pondu Ã  temps Â». A player attack needs no
    extra input anymore: declare directly, then confirm.
    """
    attacks = wiring.attacks_service
    if attacks is None:
        await target.response.send_message(strings["no_service"], ephemeral=True)
        return
    await target.response.defer(ephemeral=True)
    try:
        attack = await attacks.declare_attack(
            str(target.user.id), map_key, "", cover_lord_id=cover_lord_id
        )
    except Exception as exc:
        message = getattr(exc, "message", None) or str(exc)
        await target.followup.send(strings["failed"].format(message=message), ephemeral=True)
        return
    await target.followup.send(
        strings["declared_note"].format(map=attack.map_key, time=_fmt_paris(attack.expires_at)),
        ephemeral=True,
    )
    await _announce_declaration_safe(target.guild, wiring, strings, attack)
    await refresh_attack_panel_safe(target.guild, _locale(target))


async def _open_cover_step(
    wiring: Any,
    interaction: discord.Interaction[Any],
    strings: dict[str, str],
    map_key: str,
    own_kingdom_id: str,
    *,
    is_gaia: bool = False,
) -> None:
    """Step 3 â the counter-espionage question (skipped without a charge).

    A GaÃ¯a target skips the cover entirely: the free-for-all has no
    defender to bluff (Drasah 14/10 â GaÃ¯a is attackable like any
    kingdom, the battle just accepts extra lords).
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
    if state.purchases.get("contre_espionnage", 0) <= 0:
        await _declare_player_attack(wiring, interaction, strings, map_key, None)
        return
    view = discord.ui.View(timeout=600)

    async def open_lobby(cover_lord_id: str | None, target: discord.Interaction[Any]) -> None:
        await _declare_player_attack(wiring, target, strings, map_key, cover_lord_id)

    async def on_yes(target: discord.Interaction[Any]) -> None:
        lords = [
            lord
            for lord in await wiring.kingdoms_service.lords()
            if lord.kingdom_id == own_kingdom_id
            and not getattr(lord, "left", False)
            and str(lord.id) != str(target.user.id)
        ]
        if not lords:
            await open_lobby(None, target)
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
    await interaction.response.edit_message(content=str(strings["cover_question"]), view=view)


async def _announce_declaration_safe(guild: Any, wiring: Any, strings: dict[str, str], attack: Any) -> None:
    """Announce the v1-style declaration and open its rights thread."""
    channel = _announce_channel(guild)
    if channel is None:
        return
    kingdoms_service = wiring.kingdoms_service
    shown_id = attack.cover_lord_id or attack.attacker_lord_id
    attacker = f"<@{shown_id}>"
    attacker_name = await _kingdom_name(kingdoms_service, attack.attacker_kingdom_id)
    defender_name = await _kingdom_name(kingdoms_service, attack.defender_kingdom_id)
    content = str(strings["announce_v1"]).format(
        attacker=attacker,
        kingdom=_kingdom_mention(guild, attacker_name),
        defender=_kingdom_mention(guild, defender_name),
        date=_fmt_paris_full(attack.expires_at, strings),
    )
    if getattr(attack, "traquenard", False):
        content += str(strings["announce_v1_traquenard"])
    if getattr(attack, "kind", None) is not None and str(getattr(attack, "kind", "")) == "gaia":
        gaia_max = int(getattr(wiring.attacks_service, "gaia_max_participants", 7) or 7)
        content += str(strings["announce_v1_gaia"]).format(max=gaia_max)
    try:
        message = await channel.send(content)
    except Exception:
        logger.warning("KINGDOM ATTACK: declaration announcement failed", exc_info=True)
        return
    try:
        thread = await message.create_thread(
            name=str(strings["thread_title"]).format(kingdom=attacker_name, defender=defender_name, map=attack.map_key)[
                :100
            ]
        )
        await thread.send(str(strings["thread_body"]))
    except Exception:
        logger.warning("KINGDOM ATTACK: declaration thread failed", exc_info=True)


class KingdomAttackLobbyModal(discord.ui.Modal):
    """Final step â the game lobby URL (the territory is already chosen)."""

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
        # Drasah 18/10: the modal is GAÃA-ONLY now (a player attack
        # declares directly â an empty modal is rejected by Discord
        # and killed the whole declare flow, live beta 18/10). The
        # GaÃ¯a free-for-all keeps the field: the first declarer's URL
        # is the meeting point of the battle.
        self.lobby_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            label=strings["modal_lobby"][:45], max_length=200, required=True
        )
        self.add_item(self.lobby_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Declare the attack (GaÃ¯a free-for-all or player attack), confirm, announce."""
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
        await interaction.followup.send(
            strings["declared_note"].format(map=attack.map_key, time=_fmt_paris(attack.expires_at)),
            ephemeral=True,
        )
        await _announce_declaration_safe(interaction.guild, wiring, strings, attack)
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
        """Rebuild the button from the wire â THIS module's strings only."""
        return cls(
            await _resolve_attack_id_by_key_async(match["key"]),
            _strings(_locale(interaction))["defend_button"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Take the defense before the T-15 cutoff."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
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
        # Drasah 18/10 â effet de surprise : plus d'annonce publique du
        # dÃ©fenseur. Seul le royaume dÃ©fenseur voit qui dÃ©fend (bouton
        # Â« Gestion de la dÃ©fense Â» des Remparts) ; le board public
        # reste anonyme jusqu'au reveal.
        await refresh_attack_panel_safe(interaction.guild, _locale(interaction))


class KingdomDefenseManagerButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:defense-manager",
):
    """The ð¡ï¸ Â« Gestion de la dÃ©fense Â» button of the Remparts panel.

    Drasah 18/10 â effet de surprise : la dÃ©fense n'est plus annoncÃ©e
    publiquement et le board Attaques ne nomme jamais le dÃ©fenseur.
    Ce bouton (visible du royaume dÃ©fenseur dans ses Remparts) montre
    l'Ã©tat de chaque attaque subie et QUI dÃ©fend â la rÃ©assurance
    privÃ©e du royaume.
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
        """Rebuild the button from the wire â THIS module's strings only."""
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
        await interaction.response.send_message(
            strings["defense_manager_title"].format(kingdom=kingdom_name) + "\n" + body,
            ephemeral=True,
        )


class KingdomAttackJoinButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:join:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof join button of one open GaÃ¯a battle.

    Drasah 14/10: a GaÃ¯a territory is attacked through the declare
    flow like any kingdom; other lords then JOIN the battle from the
    panel (1v1v1â¦, one lord per kingdom, up to the participant
    ceiling) â the lobby URL of the first declarer is the meeting
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
        """Rebuild the button from the wire â THIS module's strings only."""
        return cls(
            await _resolve_attack_id_by_key_async(match["key"]),
            _strings(_locale(interaction))["gaia_join_button"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Join the open GaÃ¯a battle of this button's attack."""
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
        channel = _announce_channel(interaction.guild)
        await _announce_safe(
            channel,
            strings["gaia_join_announce"].format(
                lord=interaction.user.display_name,
                map=attack.map_key,
                count=len(attack.participants),
            ),
        )
        await refresh_attack_panel_safe(interaction.guild, _locale(interaction))


# ----------------------------------------------------------------------
# The DM pick & ban view (T-5)
# ----------------------------------------------------------------------
class KingdomPickBanView(discord.ui.View):
    """The ephemeral DM view: ranked picks, one validate button.

    Drasah 18/10 v2: NO sabotage select anymore (the Sabotage/Faucon
    moves to the battle room) and a BLIND resolution â the validating
    player NEVER sees the adversary civilizations (the live beta
    asymmetry: the second validator knew everything first). When the
    draft resolves, BOTH DMs are edited (anti-spam) into the ready
    prompt carrying the Â« PrÃªt Â» button.
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

        The selects had no callback: Discord showed Â« the application
        did not respond Â» and no civilization could ever be picked. The
        acknowledged interaction refreshes the select values held by
        THIS view instance â `_on_validate` reads them back at submit
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
        # and the second validator â nobody sees the adversary civs.
        content = strings["pickban_waiting"]
        try:
            await interaction.response.edit_message(content=content, view=None)
        except Exception:
            logger.warning("KINGDOM ATTACK: pick & ban DM edit failed", exc_info=True)
        pickban: dict[str, Any] = dict(attack.pickban or {})
        if not pickban.get("resolved_pickban"):
            return
        # The draft resolved: edit BOTH DMs into the ready prompt
        # (anti-spam â no new DM), the PrÃªt button rides the message.
        await _edit_pickban_dms_ready(interaction.client, attack, strings)


def _ready_key(attack_id: str, player_id: str) -> str:
    """Return the 16-hex digest keying the PrÃªt button (custom ids stay short)."""
    return hashlib.sha256(f"{attack_id}:{player_id}".encode()).hexdigest()[:16]


def _room_key(attack_id: str) -> str:
    """Return the 16-hex digest keying a battle-room component."""
    return hashlib.sha256(str(attack_id).encode()).hexdigest()[:16]


def _ready_dm_view(attack_id: str, player_id: str, locale: str) -> discord.ui.View:
    """Build the persistent PrÃªt view appended to the pick & ban DM."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        KingdomReadyButton(attack_id, player_id, _strings(locale)["ready_button"])
    )
    return view


async def _edit_dm_message_safe(bot: Any, ref: Any, content: str, view: discord.ui.View | None) -> bool:
    """Edit a stored DM message (channel_id + message_id) â best effort."""
    try:
        channel = getattr(bot, "get_channel", lambda _id: None)(int(ref["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(ref["channel_id"]))
        message = channel.get_partial_message(int(ref["message_id"]))
        await message.edit(content=content, view=view)
        return True
    except Exception:
        logger.warning("KINGDOM ATTACK: DM edit failed â falling back to a new DM", exc_info=True)
        return False


async def _edit_pickban_dms_ready(bot: Any, attack: Any, strings: dict[str, str]) -> None:
    """Edit both pick & ban DMs into the ready prompt (Drasah 18/10 v2).

    The refs come from the pickban store (``record_pickban_dm``); a
    failed edit (restart gap, expired message) falls back to a NEW
    DM â the PrÃªt button must reach both players.
    """
    pickban: dict[str, Any] = dict(attack.pickban or {})
    for side, player_id in (
        ("attacker", str(attack.attacker_lord_id)),
        ("defender", str(attack.defender_lord_id or "")),
    ):
        if not player_id or (side == "defender" and attack.ai_defender):
            continue
        content = strings["pickban_ready_prompt"].format(map=attack.map_key)
        view = _ready_dm_view(str(attack.id), player_id, "fr")
        ref = pickban.get(f"{side}_dm") or {}
        edited = False
        if ref.get("channel_id") and ref.get("message_id"):
            edited = await _edit_dm_message_safe(bot, ref, content, view)
        if not edited:
            await _dm_user_safe(bot, player_id, content, view)


class KingdomReadyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:ready:(?P<key>[0-9a-f]{16})",
):
    """The restart-proof Â« PrÃªt Â» button of the pick & ban DM."""

    def __init__(self, attack_id: str, player_id: str, label: str) -> None:
        key = _ready_key(attack_id, player_id)
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:ready:{key}",
                style=discord.ButtonStyle.secondary,
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
    ) -> KingdomReadyButton:
        """Resolve the key back to a meeting player (both loops)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        label = _strings(_locale(interaction))["ready_button"]
        wiring = _wiring()
        key = match["key"]
        if wiring.attacks_service is not None:
            try:
                for attack in await wiring.attacks_service.attacks():
                    if str(getattr(attack, "state", "")) != "meeting":
                        continue
                    for player_id in _score_players(attack):
                        if _ready_key(str(attack.id), player_id) == key:
                            return cls(str(attack.id), player_id, label)
            except Exception:
                logger.warning("KINGDOM ATTACK: ready key resolution failed", exc_info=True)
        return cls("", "", label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Confirm the presence (UI record â the forfeit rides the voice)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            await wiring.attacks_service.mark_ready(self.attack_id, self.player_id)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(strings["failed"].format(message=message), ephemeral=True)
            return
        await interaction.response.send_message(strings["ready_done"], ephemeral=True)


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
    message = await _dm_user_safe(
        bot,
        str(attack.attacker_lord_id),
        strings["pickban_dm"].format(
            map=attack.map_key,
            count=min(3, len(hand)) or 1,
            hand=", ".join(hand) or "â",
            theirs=", ".join(adversary) or "â",
        ),
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
        message = await _dm_user_safe(
            bot,
            str(attack.defender_lord_id),
            strings["pickban_dm"].format(
                map=attack.map_key,
                count=min(3, len(adversary)) or 1,
                hand=", ".join(adversary) or "â",
                theirs=", ".join(hand) or "â",
            ),
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
    bot: Any, user_id: str, content: str, view: discord.ui.View | None = None
) -> Any:
    """Best-effort DM to one player â returns the message (or None)."""
    try:
        user = getattr(bot, "get_user", lambda _id: None)(user_id)
        if user is None:
            user = await bot.fetch_user(int(user_id))
        if user is not None:
            return await user.send(content, view=view)
    except Exception:
        # The message stays generic on purpose: _dm_user_safe carries
        # BOTH the pick & ban DMs and the RDV DMs (live beta 18/10: the
        # log said Â« pick & ban DM failed Â» for a dying RDV DM â the
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
    when a phase was actually played this tick â an idle tick touches
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
        announce_channel = _announce_channel(guild)
        locale = await _guild_locale(bot, str(guild.id))
        strings = _strings(locale)
        dirty = 0
        if announce_channel is not None:
            dirty += await _run_phases_announce_safe(
                bot, guild, attacks_service, kingdoms_service, announce_channel, strings, locale
            )
        # The resolved meetings free their battle room (channel +
        # role) â whatever the resolution (declared, forfeit, admin).
        dirty += await _cleanup_battle_rooms_safe(bot, attacks_service)
        if dirty:
            await deploy_attack_panel(_declare_channel(guild), locale, kingdoms_service, attacks_service)
            await deploy_attacks_board(announce_channel, locale, kingdoms_service, attacks_service)


async def _run_phases_announce_safe(
    bot: Any,
    guild: Any,
    attacks_service: Any,
    kingdoms_service: Any,
    announce_channel: Any,
    strings: dict[str, str],
    locale: str,
) -> int:
    """Play the announce-channel phases of one guild â never raise."""
    dirty = 0
    try:
        dirty += await _run_reveals_safe(attacks_service, kingdoms_service, announce_channel, strings)
        pickbans = await attacks_service.pick_ban_due()
        for attack in pickbans:
            await _send_pickban_dm(bot, attack, kingdoms_service, locale)
        dirty += len(pickbans)
        # Drasah 18/10 v2: at T the meeting opens a BATTLE ROOM
        # (dedicated role, voice channel under Â« Champs de bataille Â»,
        # the shuffled civs in its text channel) and DMs the link to
        # both players.
        scores = await attacks_service.score_due()
        for attack in scores:
            await _open_battle_room(bot, guild, attack, kingdoms_service, locale)
        dirty += len(scores)
        # A lone result declaration holds score_confirm_minutes, then
        # the declared result applies (fait foi) â announced like any
        # resolution.
        confirmed = await attacks_service.score_confirm_due()
        for attack in confirmed:
            winner = await _kingdom_name(kingdoms_service, attack.winner_kingdom_id)
            await _announce_safe(
                announce_channel,
                strings["score_resolved"].format(
                    winner=winner, map=attack.map_key, game=(attack.score or {}).get("game_id") or "â"
                ),
            )
        dirty += len(confirmed)
        # attaques-phases-v2: the 3h no-result reminder (DM only, no forfeit).
        reminders = await attacks_service.score_reminder_due()
        for attack in reminders:
            await _send_score_reminder_dm(bot, attack, locale)
        dirty += len(reminders)
        # bug nÂ°2 (Check 59, Drasah 16/10): 15h without a declared
        # result â the dispute auto-opens, the admins arbitrate; no
        # meeting hangs forever.
        disputes = await attacks_service.dispute_due()
        for attack in disputes:
            await _announce_safe(announce_channel, strings["auto_dispute"].format(map=attack.map_key))
        dirty += len(disputes)
        dirty += await _run_forfeits_safe(attacks_service, kingdoms_service, announce_channel, strings)
        dirty += await _run_noshows_safe(attacks_service, kingdoms_service, announce_channel, strings)
    except Exception:
        logger.warning("KINGDOM ATTACK: phase run failed", exc_info=True)
    return dirty


async def _run_reveals_safe(attacks_service: Any, kingdoms_service: Any, channel: Any, strings: dict[str, str]) -> int:
    """Announce the T-15 reveals (cover drop only).

    Drasah 17/10: the defense window is [T-15, T] â at the cutoff the
    cover drops but the defense stays open; the AI fallback announces
    at the meeting time (``_run_forfeits_safe``). Returns the number
    of reveals played (attaques-panel-spam: the tick redeploy is
    gated on a non-zero phase count).
    """
    played = 0
    for attack in await attacks_service.reveal_due():
        played += 1
        if attack.cover_lord_id:
            await _announce_safe(
                channel,
                strings["reveal_cover"].format(
                    map=attack.map_key,
                    lord=await _lord_name(kingdoms_service, attack.attacker_lord_id),
                ),
            )
    return played


async def _run_forfeits_safe(attacks_service: Any, kingdoms_service: Any, channel: Any, strings: dict[str, str]) -> int:
    """Announce the meeting-time forfeits of the expired attacks.

    Returns the number of attacks resolved (attaques-panel-spam: any
    resolution changes the panel content, announced or not).
    """
    played = 0
    for attack in await attacks_service.expire_stale():
        played += 1
        if attack.ai_defender:
            # The [T-15, T] window closed with no defender â the bot
            # AI takes the defense at the meeting time (Drasah 17/10).
            await _announce_safe(
                channel,
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
            await _announce_safe(channel, strings["forfeit_both"].format(map=attack.map_key, kingdom=kingdom))
        else:
            await _announce_safe(
                channel,
                strings["forfeit_side"].format(map=attack.map_key, side=strings[f"side_{side}"], kingdom=kingdom),
            )
    return played



# ----------------------------------------------------------------------
# The battle room (Drasah 18/10 v2 â the RDV lives in a voice room)
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
    """Shuffle one side's 3 picks â the ranking must stay hidden."""
    import random as _random

    pool = [str(civ) for civ in (picks or [])]
    _random.shuffle(pool)
    return pool


async def _open_battle_room(bot: Any, guild: Any, attack: Any, kingdoms_service: Any, locale: str) -> None:
    """Open the battle room of a meeting (Drasah 18/10 v2).

    A dedicated role (given to both players), a voice channel under
    the Â« Champs de bataille Â» category (user_limit 2, access locked
    to the role) and a text-in-voice message carrying the SHUFFLED
    civilizations of each player + the persistent URL/result buttons.
    Both players get a DM pointing at the room.
    """
    strings = _strings(locale)
    score = attack.score or {}
    if score.get("room_channel_id"):
        return  # already opened (tick replay after a restart)
    try:
        attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
        defender = await _lord_name(kingdoms_service, attack.defender_lord_id)
        role = await guild.create_role(name=strings["room_role"].format(map=attack.map_key)[:100])
        category = next(
            (item for item in getattr(guild, "categories", []) if str(item.name) == strings["room_category"]),
            None,
        )
        if category is None:
            category = await guild.create_category(strings["room_category"])
        everyone = getattr(guild, "default_role", None)
        overwrites: dict[Any, discord.PermissionOverwrite] = {
            role: discord.PermissionOverwrite(connect=True, view_channel=True),
        }
        if everyone is not None:
            overwrites[everyone] = discord.PermissionOverwrite(connect=False, view_channel=False)
        channel = await guild.create_voice_channel(
            strings["room_title"].format(map=attack.map_key, attacker=attacker, defender=defender)[:100],
            category=category,
            user_limit=2,
            overwrites=overwrites,
        )
        pickban: dict[str, Any] = dict(attack.pickban or {})
        lines = [
            f"**{attacker}** â {' '.join(_shuffled_civs(pickban.get('attacker_picks')))}",
            f"**{defender}** â {' '.join(_shuffled_civs(pickban.get('defender_picks')))}",
        ]
        room_view = discord.ui.View(timeout=None)
        room_view.add_item(
            KingdomRoomUrlButton(str(attack.id), strings["score_url_button"])
        )
        room_view.add_item(
            KingdomRoomResultButton(str(attack.id), strings["result_button"])
        )
        await channel.send(
            strings["room_body"].format(map=attack.map_key, lines="\n".join(lines)),
            view=room_view,
        )
        for player_id in _score_players(attack):
            member = None
            try:
                member = guild.get_member(int(player_id))
            except (TypeError, ValueError):
                member = None
            if member is not None:
                try:
                    await member.add_roles(role)
                except Exception:
                    logger.warning("KINGDOM ATTACK: battle room role grant failed", exc_info=True)
        await record_battle_room_safe(attack, channel.id, role.id)
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


async def _cleanup_battle_rooms_safe(bot: Any, attacks_service: Any) -> int:
    """Delete the battle room (channel + role) of every resolved meeting."""
    played = 0
    try:
        attacks = await attacks_service.attacks()
    except Exception:
        logger.warning("KINGDOM ATTACK: battle room scan failed", exc_info=True)
        return 0
    for attack in attacks:
        score = attack.score or {}
        if not attack.is_over or not score.get("room_channel_id") or score.get("room_cleaned"):
            continue
        try:
            for guild in list(getattr(bot, "guilds", [])):
                channel = getattr(guild, "get_channel", lambda _id: None)(int(score["room_channel_id"]))
                if channel is not None:
                    await channel.delete()
                role_id = score.get("room_role_id")
                if role_id is not None:
                    role = getattr(guild, "get_role", lambda _id: None)(int(role_id))
                    if role is not None and hasattr(role, "delete"):
                        await role.delete()
        except Exception:
            logger.warning("KINGDOM ATTACK: battle room cleanup failed", exc_info=True)
        try:
            await attacks_service.mark_room_cleaned(str(attack.id))
        except Exception:
            logger.warning("KINGDOM ATTACK: battle room cleanup flag failed", exc_info=True)
        played += 1
    return played


class KingdomRoomUrlButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:room:url:(?P<key>[0-9a-f]{16})",
):
    """The persistent ð button of the battle room (URL share)."""

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:room:url:{_room_key(attack_id)}",
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
    ) -> KingdomRoomUrlButton:
        """Resolve the room key back to an attack id (restart-safe)."""
        label = _strings(_locale(interaction))["score_url_button"]
        return cls(await _resolve_room_attack_id_async(match["key"]), label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the game URL modal of this battle room."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
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
            await wiring.attacks_service.set_lobby_url(self.attack_id, self.player_id, url)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(strings["failed"].format(message=message), ephemeral=True)
            return
        try:
            room_channel: Any = interaction.channel
            await room_channel.send(strings["score_url_posted"].format(url=url))
        except Exception:
            logger.warning("KINGDOM ATTACK: battle room URL post failed", exc_info=True)
        await interaction.response.send_message(strings["score_url_set"], ephemeral=True)


class KingdomRoomResultButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:room:result:(?P<key>[0-9a-f]{16})",
):
    """The persistent ð button of the battle room (result declaration)."""

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
        """Ephemeral winner select â game-id modal (Drasah 18/10 v2)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        try:
            attack = next(
                item
                for item in await wiring.attacks_service.attacks()
                if str(item.id) == self.attack_id
            )
        except Exception:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        if attack.is_over or str(getattr(attack, "state", "")) != "meeting":
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        attacker_name = await _kingdom_name(wiring.kingdoms_service, attack.attacker_kingdom_id)
        defender_name = await _kingdom_name(wiring.kingdoms_service, attack.defender_kingdom_id)
        view = discord.ui.View(timeout=300)
        select: discord.ui.Select[Any] = discord.ui.Select(
            placeholder=strings["result_select_placeholder"][:100],
            options=[
                discord.SelectOption(label=str(attacker_name)[:100], value=str(attack.attacker_kingdom_id)),
                discord.SelectOption(label=str(defender_name)[:100], value=str(attack.defender_kingdom_id)),
            ],
            min_values=1,
            max_values=1,
        )

        async def _on_winner(target: discord.Interaction) -> None:
            if str(getattr(target.user, "id", "")) != str(interaction.user.id):
                await target.response.defer()
                return
            await target.response.send_modal(
                KingdomRoomResultModal(self.attack_id, str(target.user.id), str(select.values[0]))
            )

        select.callback = _on_winner  # type: ignore[method-assign,assignment]
        view.add_item(select)
        await interaction.response.send_message(
            strings["result_select_title"], view=view, ephemeral=True
        )


class KingdomRoomResultModal(discord.ui.Modal):
    """The battle-room result modal: the game id rides the winner.

    Drasah 18/10 v2: the declaration lives in the room â ``resolved``
    announces on the spot, ``disputed`` DMs the admins the arbitration
    view and a lone declaration pends ``score_confirm_minutes``.
    """

    def __init__(self, attack_id: str, player_id: str, winner_kingdom_id: str) -> None:
        strings = _strings("fr")
        super().__init__(title=strings["score_modal_title"][:45], timeout=300)
        self.attack_id = attack_id
        self.player_id = player_id
        self.winner_kingdom_id = winner_kingdom_id
        self.strings = strings
        self.game_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=strings["score_modal_label"][:45],
            placeholder=strings["score_modal_placeholder"][:100],
            required=True,
            max_length=100,
        )
        self.add_item(self.game_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Declare the result from the battle room (resolved or pending)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = self.strings
        wiring = _wiring()
        if wiring.attacks_service is None or not self.attack_id:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        game_id = str(self.game_input.value or "").strip()
        try:
            attack, outcome = await wiring.attacks_service.declare_score(
                self.attack_id, self.player_id, self.winner_kingdom_id, game_id
            )
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(
                strings["failed"].format(message=message), ephemeral=True
            )
            return
        if outcome == "disputed":
            await interaction.response.send_message(
                strings["score_disputed"].format(game=game_id or "â")
            )
            await _dm_admins_dispute_safe(interaction.client, wiring, attack, strings)
            return
        if outcome == "already":
            await interaction.response.send_message(strings["score_already"], ephemeral=True)
            return
        if outcome == "resolved":
            winner = await _kingdom_name(wiring.kingdoms_service, self.winner_kingdom_id)
            await interaction.response.send_message(
                strings["score_resolved"].format(winner=winner, map=attack.map_key, game=game_id or "â")
            )
            return
        minutes = 10
        try:
            minutes = int(
                wiring.attacks_service._config.attacks.score_confirm_minutes
            )
        except Exception:
            minutes = 10
        await interaction.response.send_message(
            strings["score_pending"].format(game=game_id or "â", minutes=minutes)
        )


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
        f"â¢ <@{player}> â {decl.get('winner')} (game {decl.get('game_id')})" for player, decl in declarations.items()
    ]
    content = strings["dispute_admin_dm"].format(
        map=attack.map_key,
        declarations="\n".join(lines) or "â",
        game=(attack.score or {}).get("game_id") or "â",
    )
    view = KingdomDisputeView(str(attack.id), "fr")
    for admin_id in wiring.bot_admins or ():
        await _dm_user_safe(bot, str(admin_id), content, view)


async def _run_noshows_safe(attacks_service: Any, kingdoms_service: Any, channel: Any, strings: dict[str, str]) -> int:
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
            await _announce_safe(channel, strings["noshow_both"].format(map=attack.map_key))
            continue
        kingdom = await _kingdom_name(kingdoms_service, attack.winner_kingdom_id)
        await _announce_safe(
            channel,
            strings["noshow_side"].format(map=attack.map_key, side=strings[f"side_{noshow}"], kingdom=kingdom),
        )
    return played

