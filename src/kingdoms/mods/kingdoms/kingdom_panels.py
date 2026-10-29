"""The Kingdoms salons-first panels (kingdoms#138): enrollment workflow.

Three panels on top of the bootstrapped structure:

- **Postuler** (open to all) — a pinned button opens an ephemeral,
  per-player enrollment flow with no free-text role: a select menu
  offers Seigneur or Roi; a Seigneur then picks a declared kingdom
  from a list (or joins the waiting queue, for an admin to assign
  later), a Roi types a kingdom name; both then give their AoE II
  Insight link + game ID and confirm with a rules-acceptance button
  (no typing);
- **Candidatures** (admin only) — each submitted application lands as
  a message with admin-only buttons ✅ validated / ⏳ pending / ❌
  refused; validation assigns the kingdoms_lord or kingdoms_king mod
  role to the player;
- **Paramètres** (admin only) — the mod's own season actions; language
  and timezone live in the platform core (bot-admin).

The mod declares its roles in ``config/mods/kingdoms.yaml``
(kingdoms_king, kingdoms_lord); the panels assign them through the
ModRolesService — never raw Discord role IDs. Panels degrade when the
stores are not wired (local runs): the buttons answer with a note.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.logs import LogService
from kingdoms.core.services.roles import ModRolesService
from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.ui import (
    GREEN,
)
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.panel_messages import (
    NO_MENTIONS,
    channel_messages,
    message_text,
    message_text_squashed,
    view_text,
)
from kingdoms.mods.kingdoms.snapshot import season_label

logger = logging.getLogger("kingdoms.kingdom_panels")

# Discord auto-hides ephemeral messages after 15 minutes, but they stay
# on the user's screen until dismissed — Drasah asked for a real
# auto-cleanup of the Postuler confirmations (2026-10-11). The
# interaction webhook token lives 15 minutes: a deletion scheduled
# before that boundary succeeds, later ones fail silently.
_EPHEMERAL_TTL_SECONDS = 840
# The enrollment flow lives in ONE ephemeral bubble: after the final
# confirmation the whole thing must vanish quickly (~30 s) instead of
# staying 14 minutes (Drasah 2026-10-13 postulation-v1 spec).
_CONFIRM_TTL_SECONDS = 30  # type: int
_EPHEMERAL_TASKS: set[asyncio.Task[None]] = set()


def _schedule_ephemeral_delete(
    interaction: discord.Interaction, message: Any, seconds: int = _EPHEMERAL_TTL_SECONDS
) -> None:
    """Best-effort auto-delete of one ephemeral followup (~14 minutes)."""

    async def _delete() -> None:
        await asyncio.sleep(seconds)
        try:
            message_id = int(getattr(message, "id", 0) or 0)
            if message_id:
                await interaction.followup.delete_message(message_id)
        except Exception:
            logger.info("KINGDOM PANELS: ephemeral confirmation already gone")

    try:
        task = asyncio.create_task(_delete())
        _EPHEMERAL_TASKS.add(task)
        task.add_done_callback(_EPHEMERAL_TASKS.discard)
    except Exception:
        logger.info("KINGDOM PANELS: ephemeral TTL task not scheduled")

KINGDOM_MOD = "kingdoms"
ROLE_LORD = "kingdoms_lord"
ROLE_KING = "kingdoms_king"

APPLY_BUTTON_ID = "kingdoms:apply:open"
ROLE_SELECT_ID = "kingdoms:apply:role"
KINGDOM_SELECT_ID = "kingdoms:apply:kingdom"
DECISION_APPROVE_ID = "kingdoms:candidature:approve"
DECISION_PENDING_ID = "kingdoms:candidature:pending"
DECISION_REFUSE_ID = "kingdoms:candidature:refuse"

QUEUE_VALUE = "__queue__"
PANEL_MARKER = "kingdoms:panel:postuler"
SETTINGS_PANEL_MARKER = "kingdoms:panel:parametres"
MARKET_PANEL_MARKER = "kingdoms:panel:marche"
MARKET_CHANNEL = "marche"
SEASON_STATUS_MARKER = "kingdoms:season:status"
SEASON_STATUS_CHANNEL = "saison"
UPDATE_CHANNEL = "update"
CHANGELOG_MARKER = "kingdoms:update:changelog"


async def refresh_changelog(guild: discord.Guild, locale: str) -> bool:
    """Post (or refresh) the kingdoms changelog message in the Update channel."""
    channel = next((c for c in guild.text_channels if c.name.lower() == UPDATE_CHANNEL), None)
    if channel is None:
        return False
    fr = str(locale).lower().startswith("fr")
    items = (
        "💰 Le panneau Marché est en ligne : technologies de combat et actions spéciales"
        " (Explorateur, Corruption, Garde Royale) achetables par le Roi du royaume."
        "\n🛠️ Panneau admin enrichi : déploiement des salons + panels, resynchronisation, "
        "statut de la saison, affectation des joueurs en attente, ajout manuel de royaume — "
        "les actions sensibles demandent une confirmation."
        if fr
        else "💰 The Market panel is live: combat technologies and special actions"
        " (Explorer, Corruption, Royal Guard) purchasable by the kingdom's King."
        "\n🛠️ Richer admin panel: deploy salons + panels, resync, season status, "
        "assign waiting players, add a kingdom manually — sensitive actions ask for confirmation."
    )
    content = f"{items}\n{CHANGELOG_MARKER}"
    for message in await channel_messages(channel):
        if CHANGELOG_MARKER in message_text(message):
            try:
                await message.edit(content=content)
            except Exception:
                logger.warning("KINGDOM PANELS: changelog refresh failed", exc_info=True)
            return True
    await channel.send(content)
    return True


async def post_update_note(guild: discord.Guild, locale: str, note: str) -> bool:
    """Post a dated changelog note in the Update channel."""
    from zoneinfo import ZoneInfo

    channel = next((c for c in guild.text_channels if c.name.lower() == UPDATE_CHANNEL), None)
    if channel is None:
        return False
    try:
        tz = ZoneInfo("Europe/Paris")
    except Exception:
        tz = None
    now = game_now().astimezone(tz) if tz else game_now()
    stamp = now.strftime("%d/%m/%Y %H:%M")
    await channel.send(f"**🕘 Update — {stamp}**\n{note}")
    return True

INSIGHT_URL = re.compile(r"^https?://.+", re.IGNORECASE)
GAME_ID = re.compile(r"^\d{6,20}$")

STRINGS: dict[str, dict[str, Any]] = {
    "en": {
        "apply_button": "📋 Enroll",
        "apply_title": "Kingdoms enrollment",
        "apply_role_king_line": "👑 King — Found your kingdom, run its treasury and lead diplomacy.",
        "apply_role_lord_line": "🛡️ Lord — Join a banner and bear arms for your crown.",
        "roleinfo_king_button": "📖 View the King role",
        "roleinfo_lord_button": "📖 View the Lord role",
        "apply_sending": "⏳ Sending your application…",
        "apply_already_pending": (
            "⛔ You already have a pending application — an admin must "
            "process it before you can send another one."
        ),
        "apply_already_enrolled": (
            "⛔ You are already enrolled in a kingdom this season — "
            "only one application per person."
        ),
        "enroll_already": "⚠️ Already enrolled in the current season.",
        "enroll_service_down": (
            "⚠️ The Kingdoms service is unavailable: the enrollment and the"
            " kingdom DM could NOT be sent — this is an incident, contact an"
            " admin (the role alone was granted)."
        ),
        "role_sheet_king": (
            "# 👑 THE KING — RIGHTS & OBLIGATIONS\n"
            "\n"
            "**Role** — one single King per kingdom.\n"
            "\n"
            "**⚙️ TREASURY — SOLE HOLDER**\n"
            "• Patrol (2 h daily slot, quota/season), combat technologies, Explorer, territory purchase.\n"
            "\n"
            "**⛪ PARISH — SOLE DECISION-MAKER**\n"
            "• Tier upgrade, arranged marriage (💰 token), choice of the lord.\n"
            "\n"
            "**👑💍 ROYAL MARRIAGE — SPECIFIC REGIME**\n"
            "• Cost: 2 marriage points or 2 tokens.\n"
            "• Doubled lock: 48 h / 24 h / 12 h.\n"
            "• +2 ⚙️ to the treasury; civilisation secured for the whole season (even after a defeat).\n"
            "• Announcement in Géopolitique is mandatory.\n"
            "\n"
            "**🐦 CARRIER PIGEON — SOLE RECIPIENT**\n"
            "\n"
            "🚫 A lord can NOT spend the treasury, upgrade the Parish, nor seal an arranged marriage."
        ),
        "role_sheet_lord": (
            "# 🛡️ THE LORD — RIGHTS & OBLIGATIONS\n"
            "\n"
            "**⚔️ COMBAT — YOUR JURISDICTION**\n"
            "• Lord's Day window: Sunday 23:30 → Monday 10:00.\n"
            "• No execution during the window; declaring is allowed if the execution happens after it closes.\n"
            "• Marriage lock = forbidden to attack AND to defend.\n"
            "\n"
            "**💍 MARRIAGE — YOUR DECISION ALONE**\n"
            "• Nobody can marry you off.\n"
            "• 1 marriage point, civilisation drawn from your kingdom's hand, "
            "24 h / 12 h / 6 h lock, exclusivity between kingdoms.\n"
            "\n"
            "**🚫 WHAT YOU CANNOT DO**\n"
            "• Spend the treasury, upgrade the Parish, buy tokens for someone else.\n"
            "\n"
            "**👀 OBSERVATION**\n"
            "• Your marriage is never announced: red username 🔒 then 💍, no bot reminder.\n"
            "\n"
            "📜 Territory and Alliances are readable by every lord — espionage is legal."
        ),
        "choose_role": "Choose your role",
        "choose_kingdom": "Join a kingdom",
        "queue_option": "⏳ Wait for a kingdom",
        "queue_description": "Wait for kingdoms to be created — an admin will assign you later.",
        "no_kingdoms_hint": "No kingdom exists yet — you can wait in the queue.",
        "role_king": "👑 King",
        "role_lord": "🎖️ Lord",
        "kingdom_after_validation": "to be chosen after your application is approved",
        "king_name_dm_title": "👑 Your kingdom's name",
        "king_name_dm_body": (
            "Your King application has been accepted! Click the button "
            "below to choose your kingdom's name (max 40 characters). It "
            "will then be submitted to the admins for validation."
        ),
        "king_name_dm_fallback": "Click the button below to name your kingdom.",
        "king_name_claim_hint": (
            "👑 Free thrones are open — you can also join an existing"
            " kingdom through the dropdown."
        ),
        "king_name_button": "🏷️ Name my kingdom",
        "king_name_modal_title": "Your kingdom's name",
        "king_name_field": "Kingdom name (max 40 characters)",
        "king_name_state_invalid": (
            "No pending King application found for you — apply again or "
            "ask an admin (a season relaunch may have reset enrollments)."
        ),
        "king_name_dm_received": (
            "Your kingdom **{0}** has been proposed — it now awaits admin validation."
        ),
        "king_name_dm_error": (
            "The kingdom could not be created: {0}.\nTry again with another name "
            "(one line, max 40 characters)."
        ),
        "enroll_king_awaiting_name": (
            "👑 King enrolled — they will receive a DM to name their kingdom."
        ),
        "enroll_king_awaiting_kingdom": (
            "👑 King validated — they will receive a DM to choose their kingdom."
        ),
        "king_claim_dm_title": "👑 Choose your kingdom",
        "king_claim_dm_body": (
            "Your King application has been accepted! The kingdoms are "
            "imposed this season — pick your throne below."
        ),
        "king_claim_placeholder": "Choose your kingdom",
        "king_claim_done": "You now reign over **{0}**!",
        "king_claim_failed": "The claim failed — {0}.",
        "king_awaiting_kingdom_title": "👑 King without a kingdom yet",
        "king_awaiting_kingdom_body": (
            "Your King application has been accepted! No kingdom is "
            "available yet — you will be able to claim a throne as soon "
            "as one opens."
        ),
        "insight_field": "AoE II Insight link",
        "insight_placeholder": "https://www.aoe2insight.com/…",
        "game_id_field": "Game ID",
        "game_id_placeholder": "12345678",
        "smurfs_field": "Other AoE II Insight accounts (smurfs)",
        "smurfs_placeholder": "https://www.aoe2insight.com/… (one per line, optional)",
        "summary_title": "📋 Review your application",
        "summary_role": "Role",
        "summary_kingdom": "Kingdom",
        "summary_queue": "Status",
        "queue_value": "waiting for a kingdom",
        "summary_insight": "AoE II Insight",
        "summary_game_id": "Game ID",
        "summary_smurfs": "Declared smurf accounts",
        "rules_label": "I have read and I accept the rules",
        "submit": "✅ I accept the rules — send my application",
        "cancel": "Cancel",
        "bad_insight": "The AoE II Insight link must be a valid URL.",
        "bad_game_id": "The game ID must be 6 to 20 digits.",
        "cancelled": "Application cancelled.",
        "sent": "Your application has been sent — an admin will review it.",
        "sent_ttl": "*This message disappears by itself in a few moments.*",
        "profile_created": "Your profile has been created in your profile salon.",
        "candidature_title": "📋 New enrollment application",
        "candidature_role": "Requested role",
        "candidature_kingdom": "Kingdom",
        "candidature_queue": "Status",
        "candidature_insight": "AoE II Insight",
        "candidature_game_id": "Game ID",
        "decided": "The application has been marked {}.",
        "approved": "✅ validated",
        "pending": "⏳ pending",
        "refused": "❌ refused",
        "role_assigned": "The {} role has been assigned.",
        "no_service": "The enrollment service is not available right now.",
        "enroll_failed": "Enrollment failed ({}).",
        "enroll_queued": "The player is now waiting in the queue.",
        "enrolled_kingdom": "Enrolled in kingdom {}.",
        "settings_title": "⚙️ Kingdoms — settings",
        "welcome_title": "🏰 Welcome to Kingdoms — Season II!",
        "welcome_body": (
            "Your enrollment has been validated. Read the rules, present yourself in Présentation,"
            " and good luck on the battlefield!"
        ),
        "welcome_fallback": "(Sent here because your private messages are closed.)",
        "season_status_title": "📊 Kingdoms — Season II status",
        "season_status_kingdoms": "👑 Kingdoms",
        "season_status_queue": "⏳ Waiting players",
        "season_status_empty": "none yet",
        "season_status_hint": "This message is refreshed automatically by the bot.",
        "market_title": "💰 Kingdom Market",
        "market_hint_panel": (
            "👑 Only the King of a kingdom can buy — every purchase debits "
            "the kingdom treasury immediately (cost shown per article)."
        ),
        "market_fiche_input": "Article sheet (read only)",
        # cartes-tcg-v2 (Drasah 26/10): the revealed card carries its
        # own buttons — Buy / Cancel.
        "market_buy_button": "✅ Buy",
        "market_cancel_button": "❌ Cancel",
        "market_cancelled": "Purchase cancelled — the card goes back under the merchant's stall.",
        "market_fiche_effect": "EFFECT — {0}",
        "market_fiche_cost": "COST — {0} ⚙️",
        "market_fiche_wallet": "CURRENT TREASURY — {0} ⚙️",
        "market_fiche_left": "LEFT THIS SEASON — {0}/{1}",
        "market_fiche_king_only": "👑 King only — debited from the treasury immediately.",
        "market_effect": {
            "embuscade": (
                "Declare an exceptional attack without spending an attack "
                "point. Defending an ambush also uses an exceptional defence."
            ),
            "traquenard": (
                "On use: the AI you face becomes Easy, and the target kingdom's "
                "active marriages gain a +2h lock."
            ),
            "contre_espionnage": (
                "When attacking, you may declare the attack in the name of "
                "any other lord, except a lord from the kingdom you attack. "
                "Your identity is revealed 15 minutes before the game."
            ),
            "sabotage": (
                "Removes one civilization choice from your opponent during a "
                "confrontation. You may sabotage at most two civilizations "
                "per encounter."
            ),
            "jeu_d_armes": (
                "+1 permanent AI level (until the season ends) on your defence "
                "bot — cap 5, reached: your Patrols become Reinforced (3h)."
            ),
            "explorateur": (
                "The chosen lord is locked for 48h (🔒 no attack, no defence) "
                "then the kingdom receives a brand-new map. Once per season."
            ),
            "corruption": (
                "Instantly transfers an enemy territory to your kingdom, no "
                "combat. Blocked if the map is under Royal Guard. 2 per season."
            ),
            "garde_royale": (
                "Buys ONE Royal Guard token (several may be held at once, "
                "no limit). The King then places it from the Remparts "
                "salon: 12h of immunity on ONE chosen map."
            ),
            "patrouille": (
                "Unlocks ONE patrol slot (slot 1, then slot 2). The King "
                "places the daily 2h window from the Remparts salon — "
                "fixed for the cycle, redefined on the Lord's Day "
                "(3h when Reinforced)."
            ),
            "mariage_arrange": (
                "A 💰 token to consume at the Parish: arranged marriage of a "
                "lord of your choice with an unmarried civilization (6h lock)."
            ),
        },
        "market_bought": "✅ Purchased: **{0}** — the treasury holds {1} ⚙️ left.",
        "market_bought_detail": "✅ Purchased: **{0}** — {1}\nThe treasury holds {2} ⚙️ left.",
        "market_bought_left": "\nLeft this season: {0}/{1}.",
        "market_err_missing": "❌ Insufficient treasury — you are {0} ⚙️ short.",
        "market_err_limit_reached": "❌ Season limit reached ({0}/{1}).",
        "market_ledger_button": "🏛️ Chambre des comptes",
        "market_ledger_title": "🏛️ Chambre des comptes — {0}",
        "market_ledger_input": "Season register (read only)",
        "market_ledger_summary": "Season balance — spent: {0} ⚙️ · treasury: {1} ⚙️",
        "market_ledger_limits": "Limits — {0}",
        "market_ledger_empty": "No purchase yet.",
        "market_ledger_purchases_title": "Purchases",
        "market_ledger_cards_title": "Cards in hand",
        "market_ledger_cards_empty": "No card in hand right now.",
        "market_ledger_card_line": "{0} **{1}** — ×{2}",
        "market_ledger_line": "{0} {1} — {2} · {3} ⚙️ · {4}",
        "market_card_effect": "Effect: {0}",
        "market_card_usage": "Usage: {0}",
        "market_usage": {
            "embuscade": (
                "In « Declare an attack », you choose between a classic "
                "attack or an ambush."
            ),
            "traquenard": (
                "Used in an attack against an AI — the AI you face becomes "
                "Easy and the target kingdom's active marriages gain a +2h "
                "lock."
            ),
            "contre_espionnage": (
                "When declaring the attack, you can use the "
                "Counter-espionage."
            ),
            "sabotage": (
                "Used after ordering your 3 civilizations against an "
                "opponent — the « Sabotage » button becomes available."
            ),
        },
        "market_ledger_emoji": {
            "embuscade": "🥷",
            "traquenard": "🪤",
            "contre_espionnage": "🕵️",
            "sabotage": "🗡️",
            "jeu_d_armes": "🧪",
            "explorateur": "🛡️",
            "corruption": "🧨",
            "garde_royale": "🛡️",
            "patrouille": "🛡️",
            "mariage_arrange": "💒",
        },
        # marche-crier-off-v1 (Drasah 29/10): one merchant stall, four
        # shelves (his taxonomy + order), all buttons same colour. The
        # crier line is gone (Drasah asked for its removal live).
        "market_section_ia": "🤖 AI defense / attack management",
        "market_section_defense": "🛡️ Kingdom defense",
        "market_section_attaque": "⚔️ Attack improvements",
        "market_section_speciales": "✨ Special actions",
        "market_hint": (
            "Only the King of a kingdom can buy. Every purchase debits the"
            " kingdom's tech-point treasury ({} ⚙️ per purchase)."
        ),
        "market_not_king": "Only Kings can buy on the market.",
        "market_no_service": "The market is not available right now.",
        "market_buy_ok": "✅ Purchased: **{}** — treasury left: {} ⚙️",
        "market_buy_ok_territory": "✅ Purchased: **{}** — {}.",
        "market_err_insufficient": "❌ Not enough tech points.",
        "market_err_limit": "❌ The per-season purchase limit is reached.",
        "market_err_protected": "❌ This territory is protected.",
        "market_err_guard_active": "❌ This map is already shielded by a Royal Guard.",
        "market_err_already_married": "❌ This lord is already married.",
        "market_err_civ_taken": "❌ This civilization is already claimed by an active marriage.",
        "market_err_civ_unknown": "❌ Marriage refused.",
        "market_choose_slot": "🛡️ Choose the daily patrol slot (2h, Paris time):",
        "market_slot_line": "{:02d}:00 → {:02d}:00",
        "market_patrouille_ok": (
            "✅ Purchased: **Patrol** — daily slot {:02d}:00 → {:02d}:00"
            " — treasury left: {} ⚙️"
        ),
        "market_choose_lord": "💍 Choose the lord to wed:",
        "market_choose_explorer": "🗺️ Choose the lord to send exploring (48h):",
        "market_choose_civ": "👰 Choose the civilization of the arranged marriage:",
        "market_mariage_ok": (
            "✅ Arranged marriage sealed: **{}** is exclusive to your kingdom"
            " — treasury left: {} ⚙️"
        ),
        "market_err_unknown": "❌ Purchase refused ({}).",
        "market_arranged_token_ok": (
            "💒 Arranged-marriage token bought — stock: {}. Consume it at the Parish."
        ),
        "market_garde_token_ok": (
            "🛡️ Royal Guard bought — in reserve: {}. Place it from the 🧱 Remparts salon."
        ),
        "market_patrouille_slot_ok": (
            "🛡️ Patrol slot {} unlocked — place the window from the 🧱 Remparts salon."
        ),
        "ffa_join_button": "🗺️ Aller explorer",
        "ffa_join_note": (
            "✅ **{lord}** holds an FFA Exploration slot — **{time}** (Paris time). "
            "{count}/{max} participant(s)."
        ),
        "ffa_left_note": "🚪 **{lord}** released their slot — {count}/{max} participant(s).",
        "ffa_not_open": "❌ No FFA Exploration is open for enrollment right now.",
        "ffa_full": "❌ The FFA Exploration is full ({max} participants).",
        "ffa_kingdom_full": (
            "❌ Your kingdom already holds a slot (mode: one lord per kingdom)."
        ),
        "ffa_locked": (
            "❌ You are locked (a fresh Explorateur 48h lock or a missed FFA) — "
            "you cannot enroll."
        ),
        "ffa_announce": (
            "🧭 **FFA Exploration — Saturday {time} (Paris time)**\n"
            "One FREE {duration}-minute game against the Gaïa AI and the other "
            "participants — {max} slots, mode: {mode}. Click "
            "**« Aller explorer »** to reserve yours (first come, first "
            "served; click again to release it)."
        ),
        "ffa_start": (
            "🧭 **The FFA Exploration starts now** — {count} participant(s), "
            "{duration} minutes. Good luck!"
        ),
        "ffa_mode_one": "one lord per kingdom",
        "ffa_mode_free": "free slots",
        # restructuration-serveur-v1 c (Drasah 19/10): the ephemeral FFA salon.
        "ffa_salon_title": "🗺️ Exploration FFA",
        "market_err_no_points": "❌ The kingdom holds no marriage point.",
        "market_err_no_token": (
            "❌ No arranged marriage in stock — buy a token at the Market."
        ),
        "church_marry_intro": "👰 Choose the civilization of your marriage:",
        "church_marry_ok": (
            "💍 Marriage sealed: **{0}** — lock {1}h, Offrande +{2} ⚙️."
        ),
        "mercenary_refund_dm": (
            "⚔️ The Mercenary Chief calls your contract back: **{0}** was "
            "just sealed in marriage — your mercenary copy is stripped and "
            "your purse is refunded **{1} écu(s)**."
        ),
        "church_marry_royal_ok": (
            "👑💍 Marriage sealed: **{0}** becomes your Queen — royal lock"
            " {1}h · +2 ⚙️ to the treasury · secured for the whole season."
        ),
        "church_marry_royal_arranged_ok": (
            "👑💍 Royal arranged marriage: **{0}** becomes the Queen — royal"
            " lock {1}h · +2 ⚙️ · secured for the whole season."
        ),
        "church_err_royal_points": (
            "❌ A royal marriage costs TWO marriage points — the kingdom"
            " does not hold enough."
        ),
        "church_err_royal_token": (
            "❌ A royal arranged marriage costs TWO tokens — the kingdom"
            " does not hold enough."
        ),
        "royal_wedding_announce": (
            "👑💍 **Grand royal wedding** — {0} has taken **{1}** as Queen"
            " of the Kingdom of {2}! 📑 The details live in the « Learn"
            " more » thread below."
        ),
        "royal_wedding_couple": "{0}, promised lady of the {1} civilization",
        "royal_wedding_more_title": "Learn more",
        "royal_wedding_more_body": (
            "📑 **Learn more — the royal wedding of the Kingdom of {1}**\n"
            "🎯 **Gains** —\n"
            "🏴 **{0} civilization** ⏳ **All season long**\n"
            "⚙️ **+2 in the technology treasury**.\n"
            "🔒 **Lock** — {2}h nuptials: no attacks, no defense"
            " until **{3}**."
        ),
        "church_marry_kingdom_none": "You belong to no kingdom of the season.",
        "church_arrange_king_only": "Only the King consumes an arranged marriage.",
        "church_upgrade_king_only": "Only the King upgrades the Parish.",
        "church_upgrade_ok": (
            "⛪ Parish upgraded: **{0}** (tier {1}/{2}) — treasury left: {3} ⚙️"
        ),
        "church_upgrade_max": "❌ The Parish already sits at the top tier.",
        "cathedral_consecrate_button": "⛪ Consecrate a Lieu Sacrée (3 ⚙️)",
        "cathedral_consecrate_choose": "⛪ Choose the territory to consecrate:",
        "cathedral_consecrate_king_only": "❌ Only the King consecrates a Lieu Sacrée.",
        "cathedral_consecrate_tier": "❌ The consecration requires the Cathédrale tier.",
        "cathedral_consecrate_none": "❌ No territory is open to a consecration right now.",
        "cathedral_consecrate_ok": (
            "⛪ The chantier runs on **{0}** — verdict on {1} (Paris). "
            "Until then the territory resists every technology; a classic attack can destroy the chantier."
        ),
        "cathedral_consecrated": (
            "⛪ **{0}** consecrates a Lieu Sacrée on **{1}** — "
            "72h of chantier: immune to technologies, attackable by classic attacks."
        ),
        "cathedral_sacred": (
            "⛪ **{0}**'s Lieu Sacrée on **{1}** survived its chantier — the territory is now "
            "**SACRED**: immune to everything, forever."
        ),
        "church_err_not_in_hand": (
            "❌ This civilization is not in your Alliances hand."
        ),
        "church_err_no_hand": (
            "❌ Your kingdom holds no free civilization — the hand is"
            " redrawn at the next Lord's Day."
        ),
        "market_choose_corrupt": "🎯 Choose the territory to corrupt:",
        "market_choose_guard": "🛡️ Choose the territory to shield:",
        "market_no_options": "No option is available right now.",
        "market_territory_line": "{} — owner {}",
        "market_tech": {
            "embuscade": "Ambush",
            "traquenard": "Trap",
            "contre_espionnage": "Counter-espionage",
            "sabotage": "Sabotage",
            "jeu_d_armes": "Arms mastery",
        },
        "market_action": {
            "explorateur": "Explorer",
            "corruption": "Corruption",
            "garde_royale": "Royal Guard",
            "patrouille": "Patrol",
            "mariage_arrange": "Arranged marriage",
        },
    },
    "fr": {
        "apply_button": "📋 S'inscrire",
        "apply_title": "Inscription Kingdoms",
        "apply_role_king_line": "👑 Roi — Fonde ton royaume, gère son trésor et mène la diplomatie.",
        "apply_role_lord_line": "🛡️ Seigneur — Rejoins une bannière et porte les armes pour ta couronne.",
        "roleinfo_king_button": "📖 Voir le rôle Roi",
        "roleinfo_lord_button": "📖 Voir le rôle Seigneur",
        "apply_sending": "⏳ Envoi de ta candidature…",
        "apply_already_pending": (
            "⛔ Tu as déjà une candidature en attente — un admin doit "
            "la traiter avant que tu puisses en renvoyer une."
        ),
        "apply_already_enrolled": (
            "⛔ Tu es déjà inscrit dans un royaume cette saison — "
            "une seule candidature par personne."
        ),
        "enroll_already": "⚠️ Déjà inscrit dans la saison courante.",
        "enroll_service_down": (
            "⚠️ Le service Kingdoms est indisponible : l'inscription et le MP"
            " du royaume n'ont PAS pu être envoyés — c'est un incident, préviens"
            " un admin (seul le rôle a été attribué)."
        ),
        "role_sheet_king": (
            "# 👑 LE ROI — DROITS & OBLIGATIONS\n"
            "\n"
            "**Rôle** — un seul Roi par royaume.\n"
            "\n"
            "**⚙️ TRÉSOR — SEUL DÉTENTEUR**\n"
            "• Patrouille (créneau quotidien 2 h, quota/saison), technologies "
            "de combat, Explorateur, achat de territoire.\n"
            "\n"
            "**⛪ PAROISSE — SEUL DÉCISIONNAIRE**\n"
            "• Amélioration de palier, mariage arrangé (jeton 💰), choix du seigneur.\n"
            "\n"
            "**👑💍 MARIAGE ROYAL — RÉGIME SPÉCIFIQUE**\n"
            "• Coût : 2 points de mariage ou 2 jetons.\n"
            "• Verrou doublé : 48 h / 24 h / 12 h.\n"
            "• +2 ⚙️ au trésor ; civilisation sécurisée toute la saison, même "
            "après une défaite.\n"
            "• Annonce en Géopolitique obligatoire.\n"
            "\n"
            "**🐦 PIGEON-VOYAGEUR — SEUL DESTINATAIRE**\n"
            "\n"
            "🚫 Un seigneur ne peut PAS dépenser le trésor, améliorer la Paroisse ni sceller un mariage arrangé."
        ),
        "role_sheet_lord": (
            "# 🛡️ LE SEIGNEUR — DROITS & OBLIGATIONS\n"
            "\n"
            "**⚔️ COMBAT — TA JURIDICTION**\n"
            "• Fenêtre du Jour du Seigneur : dimanche 23 h 30 → lundi 10 h.\n"
            "• Aucune exécution pendant la fenêtre ; la déclaration est permise "
            "si l'exécution a lieu après la fermeture.\n"
            "• Verrou mariage = interdit d'attaquer ET de défendre.\n"
            "\n"
            "**💍 MARIAGE — TA DÉCISION SEULE**\n"
            "• Personne ne peut te marier.\n"
            "• 1 point de mariage, civilisation issue de la main du royaume, "
            "verrou 24 h / 12 h / 6 h, exclusivité entre royaumes.\n"
            "\n"
            "🚫 **CE QUE TU NE PEUX PAS FAIRE**\n"
            "• Dépenser le trésor, améliorer la Paroisse, acheter des jetons pour un autre.\n"
            "\n"
            "👀 **OBSERVATION**\n"
            "• Ton mariage n'est jamais annoncé : pseudo rouge 🔒 puis 💍, aucun rappel du bot.\n"
            "\n"
            "📜 Territoire et Alliances lisibles par tous les seigneurs — l'espionnage est légal."
        ),
        "choose_role": "Choisissez votre rôle",
        "choose_kingdom": "Rejoindre un royaume",
        "queue_option": "⏳ En attente",
        "queue_description": "Attendre la création des royaumes — un admin vous affectera plus tard.",
        "no_kingdoms_hint": "Aucun royaume n'existe encore — vous pouvez vous mettre en attente.",
        "role_king": "👑 Roi",
        "role_lord": "🎖️ Seigneur",
        "kingdom_after_validation": "à choisir après la validation de ta candidature",
        "king_name_dm_title": "👑 Nom de ton royaume",
        "king_name_dm_body": (
            "Ta candidature de Roi a été acceptée ! Clique sur le bouton "
            "ci-dessous pour choisir le nom de ton royaume (max 40 "
            "caractères). Il sera ensuite soumis à la validation des admins."
        ),
        "king_name_dm_fallback": "Clique sur le bouton ci-dessous pour nommer ton royaume.",
        "king_name_claim_hint": (
            "👑 Des trônes sont libres — tu peux aussi rejoindre un royaume"
            " existant via le menu déroulant."
        ),
        "king_name_button": "🏷️ Nommer mon royaume",
        "king_name_modal_title": "Nom de ton royaume",
        "king_name_field": "Nom du royaume (max 40 caractères)",
        "king_name_state_invalid": (
            "Aucune candidature de Roi en attente pour toi — postule à nouveau "
            "ou demande à un admin (un relancement de saison a pu réinitialiser "
            "les inscriptions)."
        ),
        "king_name_dm_received": (
            "Ton royaume **{0}** a été proposé — il est en attente de validation "
            "par les admins."
        ),
        "king_name_dm_error": (
            "Impossible de créer le royaume : {0}.\nRéessaie avec un autre nom "
            "(une seule ligne, max 40 caractères)."
        ),
        "enroll_king_awaiting_name": (
            "👑 Roi inscrit — il va recevoir un MP pour nommer son royaume."
        ),
        "enroll_king_awaiting_kingdom": (
            "👑 Roi validé — il va recevoir un MP pour choisir son royaume."
        ),
        "king_claim_dm_title": "👑 Choisis ton royaume",
        "king_claim_dm_body": (
            "Ta candidature de Roi a été acceptée ! Les royaumes sont "
            "imposés cette saison — choisis ton trône ci-dessous."
        ),
        "king_claim_placeholder": "Choisis ton royaume",
        "king_claim_done": "Tu règnes désormais sur **{0}** !",
        "king_claim_failed": "La réclamation a échoué — {0}.",
        "king_awaiting_kingdom_title": "👑 Roi sans royaume pour l'instant",
        "king_awaiting_kingdom_body": (
            "Ta candidature de Roi a été acceptée ! Aucun royaume n'est "
            "encore disponible — tu pourras réclamer un trône dès qu'un "
            "royaume sera libre."
        ),
        "insight_field": "Lien AoE II Insight",
        "insight_placeholder": "https://www.aoe2insight.com/…",
        "game_id_field": "ID de jeu",
        "game_id_placeholder": "12345678",
        "smurfs_field": "Autres comptes AoE II Insight (smurfs)",
        "smurfs_placeholder": "https://www.aoe2insight.com/… (une par ligne, facultatif)",
        "summary_title": "📋 Vérifiez votre candidature",
        "summary_role": "Rôle",
        "summary_kingdom": "Royaume",
        "summary_queue": "Statut",
        "queue_value": "en attente d'un royaume",
        "summary_insight": "AoE II Insight",
        "summary_game_id": "ID de jeu",
        "summary_smurfs": "Comptes smurfs déclarés",
        "rules_label": "J'ai lu et j'accepte les règles",
        "submit": "✅ J'accepte les règles — envoyer ma candidature",
        "cancel": "Annuler",
        "bad_insight": "Le lien AoE II Insight doit être une URL valide.",
        "bad_game_id": "L'ID de jeu doit contenir 6 à 20 chiffres.",
        "cancelled": "Candidature annulée.",
        "sent": "Votre candidature a été envoyée — un admin l'examinera.",
        "sent_ttl": "*Ce message disparaît de lui-même dans quelques instants.*",
        "profile_created": "Votre profil a été créé dans votre salon de profil.",
        "candidature_title": "📋 Nouvelle candidature",
        "candidature_role": "Rôle demandé",
        "candidature_kingdom": "Royaume",
        "candidature_queue": "Statut",
        "candidature_insight": "AoE II Insight",
        "candidature_game_id": "ID de jeu",
        "decided": "La candidature a été marquée {}.",
        "approved": "✅ validée",
        "pending": "⏳ en attente",
        "refused": "❌ refusée",
        "role_assigned": "Le rôle {} a été attribué.",
        "no_service": "Le service d'inscription n'est pas disponible pour le moment.",
        "enroll_failed": "L'inscription a échoué ({}).",
        "enroll_queued": "Le joueur est maintenant en attente d'un royaume.",
        "enrolled_kingdom": "Inscrit dans le royaume {}.",
        "settings_title": "⚙️ Kingdoms — paramètres",
        "welcome_title": "🏰 Bienvenue dans Kingdoms — Saison II !",
        "welcome_body": (
            "Votre inscription a été validée. Lisez les règles, présentez-vous dans Présentation,"
            " et bonne chance sur le champ de bataille !"
        ),
        "welcome_fallback": "(Envoyé ici car vos messages privés sont fermés.)",
        "season_status_title": "📊 Kingdoms — Statut de la Saison II",
        "season_status_kingdoms": "👑 Royaumes",
        "season_status_queue": "⏳ Joueurs en attente",
        "season_status_empty": "aucun pour le moment",
        "season_status_hint": "Ce message est mis à jour automatiquement par le bot.",
        "market_title": "💰 Marché du Royaume",
        "market_hint_panel": (
            "👑 Seul le Roi d'un royaume peut acheter — chaque achat débite "
            "immédiatement le trésor du royaume (coût affiché par article)."
        ),
        "market_fiche_input": "Fiche article (lecture seule)",
        # cartes-tcg-v2 (Drasah 26/10): la carte révélée porte ses
        # propres boutons — Acheter / Annuler.
        "market_buy_button": "✅ Acheter",
        "market_cancel_button": "❌ Annuler",
        "market_cancelled": "Achat annulé — la carte retourne sous l'étal du marchand.",
        "market_fiche_effect": "EFFET — {0}",
        "market_fiche_cost": "COÛT — {0} ⚙️",
        "market_fiche_wallet": "TRÉSORERIE ACTUELLE — {0} ⚙️",
        "market_fiche_left": "RESTE CETTE SAISON — {0}/{1}",
        "market_fiche_king_only": "👑 Réservé au Roi — débité immédiatement du trésor.",
        "market_effect": {
            "embuscade": (
                "Déclare une attaque exceptionnelle sans point d'attaque. "
                "Défendre une embuscade utilise aussi une défense "
                "exceptionnelle."
            ),
            "traquenard": (
                "À l'usage : l'IA affrontée devient Facile, et les mariages "
                "actifs du royaume ciblé prennent +2h de verrou."
            ),
            "contre_espionnage": (
                "Lorsque vous attaquez, vous pouvez la déclarer au nom de "
                "n'importe quel autre seigneur, excepté un seigneur provenant "
                "du royaume que vous attaquez. Ton identité est révélée "
                "15 min avant la partie."
            ),
            "sabotage": (
                "Permet de retirer un choix de civilisation à votre adversaire "
                "lors d'une confrontation. Vous ne pouvez saboter que deux "
                "civilisations maximum par rencontre."
            ),
            "jeu_d_armes": (
                "+1 niveau d'IA permanent (jusqu'à la fin de saison) sur ton "
                "bot de défense — plafond 5, atteint : les Patrouilles "
                "deviennent Renforcées (3h)."
            ),
            "explorateur": (
                "Le seigneur choisi est verrouillé 48h (🔒 ni attaque ni "
                "défense) puis le royaume reçoit une map inédite. 1 par saison."
            ),
            "corruption": (
                "Transfert immédiat d'un territoire adverse vers ton royaume, "
                "sans combat. Bloquée si la map est sous Garde Royale. "
                "2 par saison."
            ),
            "garde_royale": (
                "Achète UNE Garde Royale en réserve (plusieurs gardes "
                "possibles, sans limite). Le Roi la pose ensuite depuis "
                "le salon Remparts : 12h d'immunité sur UNE map au choix."
            ),
            "patrouille": (
                "Débloque UN emplacement de patrouille (slot 1 puis slot 2). "
                "Le Roi pose le créneau quotidien de 2h depuis le salon "
                "Remparts — figé pour le cycle, redéfini le Jour du "
                "Seigneur (3h si Renforcée)."
            ),
            "mariage_arrange": (
                "Jeton 💰 à consommer à la Paroisse : mariage arrangé d'un "
                "seigneur de ton choix avec une civilisation non mariée "
                "(verrou 6h)."
            ),
        },
        "market_bought": "✅ Acheté : **{0}** — il reste {1} ⚙️ au trésor.",
        "market_bought_detail": "✅ Acheté : **{0}** — {1}\nIl reste {2} ⚙️ au trésor.",
        "market_bought_left": "\nReste cette saison : {0}/{1}.",
        "market_err_missing": "❌ Trésorerie insuffisante — il te manque {0} ⚙️.",
        "market_err_limit_reached": "❌ Limite de saison atteinte ({0}/{1}).",
        "market_ledger_button": "🏛️ Chambre des comptes",
        "market_ledger_title": "🏛️ Chambre des comptes — {0}",
        "market_ledger_input": "Registre de la saison (lecture seule)",
        "market_ledger_summary": "Bilan de saison — dépensé : {0} ⚙️ · trésor : {1} ⚙️",
        "market_ledger_limits": "Limites — {0}",
        "market_ledger_empty": "Aucun achat pour le moment.",
        "market_ledger_purchases_title": "Achats",
        "market_ledger_cards_title": "Cartes en main",
        "market_ledger_cards_empty": "Aucune carte en main pour le moment.",
        "market_ledger_card_line": "{0} **{1}** — ×{2}",
        "market_ledger_line": "{0} {1} — {2} · {3} ⚙️ · {4}",
        "market_card_effect": "Effet : {0}",
        "market_card_usage": "Usage : {0}",
        "market_usage": {
            "embuscade": (
                "Dans « Déclarer une attaque », vous avez le choix entre une "
                "attaque classique ou une embuscade."
            ),
            "traquenard": (
                "S'utilise lors d'une attaque contre une IA — l'IA affrontée "
                "devient Facile et les mariages actifs du royaume ciblé "
                "prennent +2h de verrou."
            ),
            "contre_espionnage": (
                "À la déclaration de l'attaque, vous pouvez utiliser le "
                "Contre-espionnage."
            ),
            "sabotage": (
                "S'utilise après avoir choisi l'ordre de vos 3 civilisations "
                "contre un adversaire — le bouton « Saboter » devient "
                "disponible."
            ),
        },
        "market_ledger_emoji": {
            "embuscade": "🥷",
            "traquenard": "🪤",
            "contre_espionnage": "🕵️",
            "sabotage": "🗡️",
            "jeu_d_armes": "🧪",
            "explorateur": "🛡️",
            "corruption": "🧨",
            "garde_royale": "🛡️",
            "patrouille": "🛡️",
            "mariage_arrange": "💒",
        },
        # marche-crier-off-v1 (Drasah 29/10): une seule table de marchand,
        # quatre rayons (sa taxonomie + son ordre), boutons uniformes.
        # La phrase du crieur est retirée (demande live de Drasah).
        "market_section_ia": "🤖 Gestion de la défense / attaque IA",
        "market_section_defense": "🛡️ Défense du royaume",
        "market_section_attaque": "⚔️ Amélioration de l'attaque",
        "market_section_speciales": "✨ Actions spéciales",
        "market_hint": (
            "Seul le Roi d'un royaume peut acheter. Chaque achat débite le"
            " trésor de points de tech du royaume ({} ⚙️ par achat)."
        ),
        "market_not_king": "Seuls les Rois peuvent acheter au marché.",
        "market_no_service": "Le marché n'est pas disponible pour le moment.",
        "market_buy_ok": "✅ Achat effectué : **{}** — trésor restant : {} ⚙️",
        "market_buy_ok_territory": "✅ Achat effectué : **{}** — {}.",
        "market_err_insufficient": "❌ Pas assez de points de tech.",
        "market_err_limit": "❌ La limite d'achats pour cette saison est atteinte.",
        "market_err_protected": "❌ Ce territoire est protégé.",
        "market_err_guard_active": "❌ Cette map est déjà protégée par une Garde Royale.",
        "market_err_already_married": "❌ Ce seigneur est déjà marié.",
        "market_err_civ_taken": "❌ Cette civilisation est déjà visée par un mariage actif.",
        "market_err_civ_unknown": "❌ Mariage refusé.",
        "market_choose_slot": "🛡️ Choisissez le créneau quotidien de patrouille (2h, heure de Paris) :",
        "market_slot_line": "{:02d}h00 → {:02d}h00",
        "market_patrouille_ok": (
            "✅ Achat effectué : **Patrouille** — créneau quotidien {:02d}h00 → {:02d}h00"
            " — trésor restant : {} ⚙️"
        ),
        "market_choose_lord": "💍 Choisissez le seigneur à marier :",
        "market_choose_explorer": "🗺️ Choisissez le seigneur à envoyer explorer (48h) :",
        "market_choose_civ": "👰 Choisissez la civilisation du mariage arrangé :",
        "market_mariage_ok": (
            "✅ Mariage arrangé scellé : **{}** est exclusive à votre royaume"
            " — trésor restant : {} ⚙️"
        ),
        "market_err_unknown": "❌ Achat refusé ({}).",
        "market_arranged_token_ok": (
            "💒 Mariage arrangé acheté — stock : {}. Consommez-le à la Paroisse."
        ),
        "market_garde_token_ok": (
            "🛡️ Garde Royale achetée — en réserve : {}. Pose-la depuis le salon 🧱 Remparts."
        ),
        "market_patrouille_slot_ok": (
            "🛡️ Emplacement de patrouille {} débloqué — pose le créneau depuis le salon 🧱 Remparts."
        ),
        "ffa_join_button": "🗺️ Aller explorer",
        "ffa_join_note": (
            "✅ **{lord}** tient un créneau du FFA Exploration — **{time}** "
            "(heure de Paris). {count}/{max} participant(s)."
        ),
        "ffa_left_note": "🚪 **{lord}** a libéré son créneau — {count}/{max} participant(s).",
        "ffa_not_open": "❌ Aucun FFA Exploration n'est ouvert aux inscriptions pour le moment.",
        "ffa_full": "❌ Le FFA Exploration est complet ({max} participants).",
        "ffa_kingdom_full": (
            "❌ Ton royaume tient déjà un créneau (mode : 1 seigneur par royaume)."
        ),
        "ffa_locked": (
            "❌ Tu es verrouillé (verrou Explorateur 48h en cours ou FFA manqué) — "
            "inscription impossible."
        ),
        "ffa_announce": (
            "🧭 **FFA Exploration — samedi {time} (heure de Paris)**\n"
            "Une partie GRATUITE de {duration} minutes contre l'IA Gaïa et les "
            "autres participants — {max} places, mode : {mode}. Clique sur "
            "**« Aller explorer »** pour réserver ton créneau (1er arrivé, "
            "1er servi ; re-clique pour le libérer)."
        ),
        "ffa_start": (
            "🧭 **Le FFA Exploration commence maintenant** — {count} participant(s), "
            "{duration} minutes. Bonne chance !"
        ),
        "ffa_mode_one": "1 seigneur par royaume",
        "ffa_mode_free": "places libres",
        # restructuration-serveur-v1 c (Drasah 19/10): le salon FFA éphémère.
        "ffa_salon_title": "🗺️ Exploration FFA",
        "market_err_no_points": "❌ Le royaume ne détient plus de point de mariage.",
        "market_err_no_token": (
            "❌ Aucun mariage arrangé en stock — achetez un jeton au Marché."
        ),
        "church_marry_intro": "👰 Choisissez la civilisation de votre mariage :",
        "church_marry_ok": (
            "💍 Mariage scellé : **{0}** — verrou {1}h, Offrande +{2} ⚙️."
        ),
        "mercenary_refund_dm": (
            "⚔️ Le Chef Mercenaire rappelle ton contrat : **{0}** vient "
            "d'être scellée en mariage — ta copie mercenaire est retirée "
            "et ta bourse est remboursée de **{1} écu(s)**."
        ),
        "church_marry_royal_ok": (
            "👑💍 Mariage scellé : **{0}** devient votre Reine — verrou royal"
            " {1}h · +2 ⚙️ au trésor · sécurisée toute la saison."
        ),
        "church_marry_royal_arranged_ok": (
            "👑💍 Mariage arrangé royal : **{0}** devient la Reine — verrou"
            " royal {1}h · +2 ⚙️ · sécurisée toute la saison."
        ),
        "church_err_royal_points": (
            "❌ Un mariage royal coûte DEUX points de mariage — le royaume"
            " n'en détient pas assez."
        ),
        "church_err_royal_token": (
            "❌ Un mariage arrangé royal coûte DEUX jetons — le royaume n'en"
            " détient pas assez."
        ),
        "royal_wedding_announce": (
            "👑💍 **Grand mariage royal** — {0} a pris **{1}** pour Reine"
            " du Royaume {2} ! 📑 Le détail est dans le fil « En savoir"
            " plus » ci-dessous."
        ),
        "royal_wedding_couple": "{0}, promise de la civilisation {1}",
        "royal_wedding_more_title": "En savoir plus",
        "royal_wedding_more_body": (
            "📑 **En savoir plus — le mariage royal du Royaume {1}**\n"
            "🎯 **Gains** —\n"
            "🏴 **Civilisation {0}** ⏳ **Toute la saison**\n"
            "⚙️ **+2 en trésorerie technologique**.\n"
            "🔒 **Verrou** — Durée des Noces {2} h : ne peut ni attaquer"
            " ni défendre jusqu'à **{3}**."
        ),
        "church_marry_kingdom_none": "Tu n'appartiens à aucun royaume de la saison.",
        "church_arrange_king_only": "Seul le Roi consomme un mariage arrangé.",
        "church_upgrade_king_only": "Seul le Roi améliore la Paroisse.",
        "church_upgrade_ok": (
            "⛪ Paroisse améliorée : **{0}** (palier {1}/{2}) — trésorerie restante : {3} ⚙️"
        ),
        "church_upgrade_max": "❌ La Paroisse est déjà au palier maximal.",
        "cathedral_consecrate_button": "⛪ Consacrer un Lieu Sacrée (3 ⚙️)",
        "cathedral_consecrate_choose": "⛪ Choisis le territoire à consacrer :",
        "cathedral_consecrate_king_only": "❌ Seul le Roi consacre un Lieu Sacrée.",
        "cathedral_consecrate_tier": "❌ La consécration exige le palier Cathédrale.",
        "cathedral_consecrate_none": "❌ Aucun territoire n'est disponible à la consécration pour le moment.",
        "cathedral_consecrate_ok": (
            "⛪ Le chantier est posé sur **{0}** — verdict le {1} (Paris). "
            "Jusque-là le territoire résiste à toute technologie ; une attaque classique peut détruire le chantier."
        ),
        "cathedral_consecrated": (
            "⛪ **{0}** consacre un Lieu Sacrée sur **{1}** — "
            "72h de chantier : immunisé contre les technologies, attaquable en classique."
        ),
        "cathedral_sacred": (
            "⛪ Le Lieu Sacrée de **{0}** sur **{1}** a survécu à son chantier — le territoire est désormais "
            "**SACRÉ** : immunisé contre tout, à vie."
        ),
        "church_err_not_in_hand": (
            "❌ Cette civilisation n'est pas dans votre main d'Alliances."
        ),
        "church_err_no_hand": (
            "❌ Votre royaume ne détient aucune civilisation libre — la main est"
            " renouvelée au prochain Jour du Seigneur."
        ),
        "market_choose_corrupt": "🎯 Choisissez le territoire à corrompre :",
        "market_choose_guard": "🛡️ Choisissez le territoire à protéger :",
        "market_no_options": "Aucune option disponible pour le moment.",
        "market_territory_line": "{} — propriétaire {}",
        "market_tech": {
            "embuscade": "Embuscade",
            "traquenard": "Traquenard",
            "contre_espionnage": "Contre-espionnage",
            "sabotage": "Sabotage",
            "jeu_d_armes": "Jeu d'armes",
        },
        "market_action": {
            "explorateur": "Explorateur",
            "corruption": "Corruption",
            "garde_royale": "Garde Royale",
            "patrouille": "Patrouille",
            "mariage_arrange": "Mariage arrangé",
        },
    },
}


def _strings(locale: str) -> dict[str, Any]:
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def _is_admin(interaction: discord.Interaction, bot_admins: tuple[str, ...]) -> bool:
    """Whether the invoking member is a BOT_ADMINS operator or guild admin."""
    user_id = getattr(interaction.user, "id", None)
    if user_id is not None and str(user_id) in bot_admins:
        return True
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


class _ApplicationContext:
    """Everything the ephemeral enrollment flow needs to carry along."""

    def __init__(
        self,
        locale: str = "en",
        candidatures_channel: discord.abc.Messageable | None = None,
        bot_admins: tuple[str, ...] = (),
        mod_roles_service: ModRolesService | None = None,
        guild_id: str = "",
        kingdoms_service: Any = None,
        anchor: Any = None,
    ) -> None:
        self.locale = "fr" if str(locale).lower().startswith("fr") else "en"
        self.candidatures_channel = candidatures_channel
        self.bot_admins = bot_admins
        self.mod_roles_service = mod_roles_service
        self.guild_id = guild_id
        self.kingdoms_service = kingdoms_service
        # The interaction that created the single ephemeral bubble: its
        # webhook token is the only one that can edit/delete that bubble
        # from the final submit step (a modal cannot edit it).
        self.anchor = anchor

    async def declared_kingdom_names(self) -> list[str]:
        """Return the player kingdom names of the current season (empty if none)."""
        if self.kingdoms_service is None:
            return []
        try:
            kingdoms = await self.kingdoms_service.kingdoms()
        except Exception:
            logger.warning("KINGDOM PANELS: kingdom list read failed", exc_info=True)
            return []
        return sorted(k.name for k in kingdoms if getattr(k, "type", "player") != "gaia")


class _KingApplicationModal(discord.ui.Modal):
    """The King form: Insight link + game ID (name comes after approval)."""

    insight_link: discord.ui.TextInput[_KingApplicationModal] = discord.ui.TextInput(
        label="AoE II Insight link",
        placeholder="https://www.aoe2insight.com/…",
        max_length=200,
        required=True,
    )
    game_id: discord.ui.TextInput[_KingApplicationModal] = discord.ui.TextInput(
        label="Game ID",
        placeholder="12345678",
        max_length=20,
        required=True,
    )
    smurfs: discord.ui.TextInput[_KingApplicationModal] = discord.ui.TextInput(
        label="Smurf accounts",
        style=discord.TextStyle.paragraph,
        placeholder="https://www.aoe2insight.com/… (one per line, optional)",
        max_length=400,
        required=False,
    )

    def __init__(self, context: _ApplicationContext) -> None:
        self.context = context
        strings = _strings(context.locale)
        self.insight_link.label = strings["insight_field"][:45]
        self.insight_link.placeholder = strings["insight_placeholder"][:100]
        self.game_id.label = strings["game_id_field"][:45]
        self.game_id.placeholder = strings["game_id_placeholder"][:100]
        self.smurfs.label = strings["smurfs_field"][:45]
        self.smurfs.placeholder = strings["smurfs_placeholder"][:100]
        super().__init__(title=strings["apply_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _send_summary(
            interaction,
            self.context,
            is_king=True,
            kingdom_name="",
            queued=False,
            insight=(self.insight_link.value or "").strip(),
            game_id=(self.game_id.value or "").strip(),
            smurfs=_parse_smurfs(self.smurfs.value or ""),
        )


class _LordApplicationModal(discord.ui.Modal):
    """The Lord form (kingdom chosen beforehand): Insight link + game ID."""

    insight_link: discord.ui.TextInput[_LordApplicationModal] = discord.ui.TextInput(
        label="AoE II Insight link",
        placeholder="https://www.aoe2insight.com/…",
        max_length=200,
        required=True,
    )
    game_id: discord.ui.TextInput[_LordApplicationModal] = discord.ui.TextInput(
        label="Game ID",
        placeholder="12345678",
        max_length=20,
        required=True,
    )
    smurfs: discord.ui.TextInput[_LordApplicationModal] = discord.ui.TextInput(
        label="Smurf accounts",
        style=discord.TextStyle.paragraph,
        placeholder="https://www.aoe2insight.com/… (one per line, optional)",
        max_length=400,
        required=False,
    )

    def __init__(self, context: _ApplicationContext, kingdom_choice: str = "") -> None:
        self.context = context
        self.kingdom_choice = kingdom_choice
        strings = _strings(context.locale)
        self.insight_link.label = strings["insight_field"][:45]
        self.insight_link.placeholder = strings["insight_placeholder"][:100]
        self.game_id.label = strings["game_id_field"][:45]
        self.game_id.placeholder = strings["game_id_placeholder"][:100]
        self.smurfs.label = strings["smurfs_field"][:45]
        self.smurfs.placeholder = strings["smurfs_placeholder"][:100]
        super().__init__(title=strings["apply_title"][:45], timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _send_summary(
            interaction,
            self.context,
            is_king=False,
            kingdom_name=self.kingdom_choice,
            queued=self.kingdom_choice == QUEUE_VALUE,
            insight=(self.insight_link.value or "").strip(),
            game_id=(self.game_id.value or "").strip(),
            smurfs=_parse_smurfs(self.smurfs.value or ""),
        )


def _parse_smurfs(raw: str) -> tuple[str, ...]:
    """Split the smurf textarea into clean, de-duplicated links."""
    seen: dict[str, None] = {}
    for line in raw.splitlines():
        value = line.strip()
        if value:
            seen.setdefault(value, None)
    return tuple(seen)


async def _send_summary(
    interaction: discord.Interaction,
    context: _ApplicationContext,
    *,
    is_king: bool,
    kingdom_name: str,
    queued: bool,
    insight: str,
    game_id: str,
    smurfs: tuple[str, ...] = (),
) -> None:
    """Validate the form, then show the review + rules-acceptance step.

    The modal was launched from a component of the ephemeral bubble, so
    edit_message rewrites THAT bubble — the flow stays a single message
    (validation errors are the exception: they open their own short
    bubble and leave the flow bubble intact).
    """
    strings = _strings(context.locale)
    if not INSIGHT_URL.match(insight):
        await interaction.response.send_message(strings["bad_insight"], ephemeral=True)
        return
    if not GAME_ID.match(game_id):
        await interaction.response.send_message(strings["bad_game_id"], ephemeral=True)
        return

    role_label = strings["role_king"] if is_king else strings["role_lord"]
    lines = [f"# {strings['summary_title']}", f"**{strings['summary_role']}** : {role_label}"]
    if is_king:
        lines.append(f"**{strings['summary_kingdom']}** : {strings['kingdom_after_validation']}")
    elif queued:
        lines.append(f"**{strings['summary_queue']}** : {strings['queue_value']}")
    else:
        lines.append(f"**{strings['summary_kingdom']}** : {kingdom_name}")
    lines.extend(
        [
            f"**{strings['summary_insight']}** : {insight}",
            f"**{strings['summary_game_id']}** : {game_id}",
        ]
    )
    if smurfs:
        lines.append(f"**{strings['summary_smurfs']}** : {', '.join(smurfs)}")
    lines.append(f"**{strings['rules_label']}**")
    view = _summary_view(
        interaction.user.id,
        interaction.user.name,
        is_king=is_king,
        kingdom_name=kingdom_name,
        queued=queued,
        insight=insight,
        game_id=game_id,
        smurfs=smurfs,
        context=context,
    )
    # The modal was launched from a component of the ephemeral flow
    # bubble: edit_message rewrites that same bubble (postulation-v1
    # single-bubble rule) instead of stacking a new ephemeral message.
    await interaction.response.edit_message(content="\n".join(lines), view=view)


async def _rewrite_flow_confirmation(
    context: _ApplicationContext,
    interaction: discord.Interaction,
    confirmation: str,
) -> None:
    """Rewrite the single ephemeral flow bubble into its final state.

    Only the anchor interaction's webhook token can edit the ephemeral
    bubble it created (postulation-v1 single-bubble rule); fall back to
    a fresh ephemeral followup when the anchor or bubble is gone, then
    schedule the short confirmation auto-delete (~30 s).
    """
    anchor = context.anchor
    flow_message = getattr(interaction, "message", None)
    flow_id = int(getattr(flow_message, "id", 0) or 0)
    edited = False
    if anchor is not None and flow_id:
        try:
            await anchor.followup.edit_message(flow_id, content=confirmation, view=None)
            edited = True
        except Exception:
            logger.warning("KINGDOM PANELS: flow bubble edit failed", exc_info=True)
    if not edited:
        await interaction.followup.send(confirmation, ephemeral=True)
    if anchor is not None and flow_id:
        _schedule_ephemeral_delete(anchor, flow_message, seconds=_CONFIRM_TTL_SECONDS)


async def _enrolled_blocker(
    strings: dict[str, Any],
    service: Any,
    applicant_id: int,
) -> str | None:
    """Return the refusal string when the player is already enrolled."""
    if service is None:
        return None
    try:
        lords = await service.lords()
    except Exception:
        lords = []
    for lord in lords:
        if str(lord.id) == str(applicant_id) and not lord.left:
            # 2026-10-24 recovery path (Drasah): a validated King still
            # awaiting a kingdom (queue, no kingdom) may re-apply — the
            # approval of the new candidature RESENDS the kingdom-request
            # DM (the approval flow already sends it even when the
            # enrollment itself answers "already enrolled"). This is the
            # self-service recovery for a King whose DM was lost.
            from kingdoms.mods.kingdoms.service import KING_ROLE

            if (
                lord.role is KING_ROLE
                and bool(getattr(lord, "in_queue", False))
                and getattr(lord, "kingdom_id", None) is None
            ):
                return None
            return str(strings["apply_already_enrolled"])
    return None


async def _pending_candidature_blocker(
    strings: dict[str, Any],
    channel: discord.abc.Messageable | None,
    applicant_id: int,
) -> str | None:
    """Return the refusal string when a candidature is still pending.

    A candidature counts as pending while its decision view is still
    attached (``components`` non-empty) — a decided message keeps its
    content but loses its buttons, so the player may re-apply after a
    refusal.
    """
    if channel is None:
        return None
    mention = f"<@{applicant_id}>"
    try:
        recent = await channel_messages(channel)
    except Exception:
        recent = []
    for message in recent:
        content = getattr(message, "content", "") or ""
        if mention not in content:
            continue
        if strings["candidature_title"] not in content:
            continue
        if getattr(message, "components", None):
            return str(strings["apply_already_pending"])
    return None


async def _apply_blocker(context: _ApplicationContext, applicant_id: int) -> str | None:
    """Return the refusal string when the player may not apply again.

    Drasah's rule (2026-10-18): ONE candidature per person — a player
    with a still-pending candidature or already enrolled in the
    current season must not stack applications.
    """
    strings = _strings(context.locale)
    enrolled = await _enrolled_blocker(strings, context.kingdoms_service, applicant_id)
    if enrolled is not None:
        return enrolled
    return await _pending_candidature_blocker(strings, context.candidatures_channel, applicant_id)


def _summary_view(
    applicant_id: int,
    applicant_name: str,
    *,
    is_king: bool,
    kingdom_name: str,
    queued: bool,
    insight: str,
    game_id: str,
    smurfs: tuple[str, ...],
    context: _ApplicationContext,
) -> discord.ui.View:
    """Build the review step: the rules checkbox (a button) + cancel."""
    strings = _strings(context.locale)

    async def submit(interaction: discord.Interaction) -> None:
        # Ack first (3-second rule): rewrite the single flow bubble into
        # its "sending" state while the slow work runs.
        await interaction.response.edit_message(content=strings["apply_sending"], view=None)
        blocker = await _apply_blocker(context, applicant_id)
        if blocker is not None:
            await interaction.followup.send(blocker, ephemeral=True)
            return
        role_label = strings["role_king"] if is_king else strings["role_lord"]
        lines = [
            f"# 📋 {applicant_name}",
            f"**{strings['candidature_role']}** : {role_label}",
        ]
        if is_king:
            lines.append(f"**{strings['candidature_kingdom']}** : {strings['kingdom_after_validation']}")
        elif not queued:
            lines.append(f"**{strings['candidature_kingdom']}** : {kingdom_name}")
        else:
            lines.append(f"**{strings['candidature_queue']}** : {strings['queue_value']}")
        lines.extend(
            [
                f"**{strings['candidature_insight']}** : {insight}",
                f"**{strings['candidature_game_id']}** : {game_id}",
            ]
        )
        if smurfs:
            lines.append(f"**{strings['summary_smurfs']}** : {', '.join(smurfs)}")
        lines.append(f"<@{applicant_id}>")
        view = _candidature_view(
            context.locale,
            bot_admins=context.bot_admins,
            mod_roles_service=context.mod_roles_service,
            guild_id=context.guild_id,
        )
        target = context.candidatures_channel
        if target is None:
            await interaction.followup.send(strings["no_service"], ephemeral=True)
            return
        await target.send("\n".join(lines), view=view)
        profile_note = await _provision_profile_note(
            context,
            interaction,
            strings,
            is_king=is_king,
            kingdom_name=kingdom_name,
            queued=queued,
            insight=insight,
            game_id=game_id,
            smurfs=smurfs,
        )
        confirmation = strings["sent"] if not profile_note else f"{strings['sent']}\n{profile_note}"
        confirmation = f"{confirmation}\n{strings['sent_ttl']}"
        await _rewrite_flow_confirmation(context, interaction, confirmation)

    async def cancel(interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=strings["cancelled"], view=None)

    submit_button: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["submit"][:80],
        style=discord.ButtonStyle.success,
        custom_id="kingdoms:apply:submit",
    )
    submit_button.callback = submit  # type: ignore[method-assign]
    cancel_button: discord.ui.Button[Any] = discord.ui.Button(
        label=strings["cancel"][:80],
        style=discord.ButtonStyle.secondary,
        custom_id="kingdoms:apply:cancel",
    )
    cancel_button.callback = cancel  # type: ignore[method-assign]
    view = discord.ui.View(timeout=600)
    view.add_item(submit_button)
    view.add_item(cancel_button)
    return view


def _role_select_view(context: _ApplicationContext) -> discord.ui.View:
    """Step 1: the role select (Seigneur / Roi — no free text)."""
    strings = _strings(context.locale)

    async def on_choose(interaction: discord.Interaction) -> None:
        values = getattr(interaction, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        if chosen[0] == "king":
            await interaction.response.send_modal(_KingApplicationModal(context))
            return
        await interaction.response.edit_message(
            content=f"**{strings['choose_kingdom']}**\n{strings['no_kingdoms_hint']}"
            if not await context.declared_kingdom_names()
            else f"**{strings['choose_kingdom']}**",
            view=await _kingdom_select_view(context),
        )

    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=ROLE_SELECT_ID,
        placeholder=strings["choose_role"],
        options=[
            discord.SelectOption(label=strings["role_lord"], value="lord"),
            discord.SelectOption(label=strings["role_king"], value="king"),
        ],
    )
    select.callback = on_choose  # type: ignore[method-assign]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


async def _kingdom_select_view(context: _ApplicationContext) -> discord.ui.View:
    """Step 2 (Seigneur): the declared kingdoms + the wait option."""
    strings = _strings(context.locale)
    names = await context.declared_kingdom_names()
    options = [discord.SelectOption(label=name[:100], value=name[:100]) for name in names[:24]]
    options.append(
        discord.SelectOption(
            label=strings["queue_option"],
            value=QUEUE_VALUE,
            description=strings["queue_description"][:100],
        )
    )

    async def on_choose(interaction: discord.Interaction) -> None:
        values = getattr(interaction, "data", None) or {}
        chosen = [str(v) for v in values.get("values", [])]
        if not chosen:
            return
        await interaction.response.send_modal(_LordApplicationModal(context, chosen[0]))

    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id=KINGDOM_SELECT_ID,
        placeholder=strings["choose_kingdom"],
        options=options,
    )
    select.callback = on_choose  # type: ignore[method-assign]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return view


def _candidature_view(
    locale: str,
    bot_admins: tuple[str, ...],
    mod_roles_service: ModRolesService | None = None,
    guild_id: str = "",
) -> discord.ui.View:
    """Build the admin-only decision row of one candidature message.

    Approving assigns the kingdoms_lord or kingdoms_king mod role to
    the applicant through the ModRolesService — never a raw Discord
    role id. The role key rides the application message: the decision
    row is rebuilt from the wire (restart-proof by re-deploy).
    """
    strings = _strings(locale)

    async def decide(interaction: discord.Interaction, decision: str) -> None:
        if not _is_admin(interaction, bot_admins):
            await interaction.response.send_message("Only admins can decide on applications.", ephemeral=True)
            return
        label = {
            "approve": strings["approved"],
            "pending": strings["pending"],
            "refuse": strings["refused"],
        }[decision]
        content = interaction.message.content if interaction.message is not None else ""
        await interaction.response.edit_message(content=content, view=None)
        note = strings["decided"].format(label)
        if decision == "approve":
            applicant = re.search(r"<@(\d+)>", content)
            is_king = strings["role_king"] in content
            role_key = ROLE_KING if is_king else ROLE_LORD
            if mod_roles_service is not None and applicant is not None:
                try:
                    await mod_roles_service.assign_mod_role(guild_id, applicant.group(1), KINGDOM_MOD, role_key)
                    note += " " + strings["role_assigned"].format(role_key)
                except Exception:
                    logger.warning("CANDIDATURES: role assignment failed", exc_info=True)
                    note += " " + strings["no_service"]
            # validation-immersion-v1 (Drasah 28/10): the approved
            # candidate's out-of-game opt-in (member allow over Beta/
            # Conscription) is cleared — « Postuler » hides.
            if interaction.guild is not None and applicant is not None:
                try:
                    from kingdoms.mods.kingdoms.kingdom_immersion import clear_outgame_allow_safe

                    await clear_outgame_allow_safe(interaction.guild, applicant.group(1))
                except Exception:
                    logger.warning("CANDIDATURES: outgame allow clear failed", exc_info=True)
        await interaction.followup.send(note, ephemeral=True)

    from kingdoms.mods.kingdoms.kingdom_persistent import KingdomCandidatureButton

    view = discord.ui.View(timeout=None)
    view.add_item(KingdomCandidatureButton("approve", "✅", discord.ButtonStyle.success))
    view.add_item(KingdomCandidatureButton("pending", "⏳", discord.ButtonStyle.secondary))
    view.add_item(KingdomCandidatureButton("refuse", "❌", discord.ButtonStyle.danger))
    return view


# Designer-authored Postuler header (kingdoms#138 group 1): golden
# accent (the King colour), artwork thumbnail when the asset is
# bundled, one tagline — everything else lives in the flow itself.
APPLY_TITLE = "⚔️ INSCRIPTION — KINGDOMS SAISON II"
APPLY_TAGLINE = "Pars à la conquête des territoires ! Choisis ton camp avant de postuler."
GOLD = discord.Colour(0xE6B800)


def _apply_artwork_path() -> Path:
    """Return the bundled Postuler artwork path (may not exist).

    Computed lazily instead of as a module constant: pydoc renders module
    data values verbatim, and an absolute path here would make the generated
    docs depend on the checkout directory (pydoc freshness gate).
    """
    return Path(__file__).resolve().parent.parent / "assets" / "kingdoms_artwork.png"


async def build_apply_panel(
    locale: str = "en",
    candidatures_channel: discord.abc.Messageable | None = None,
    bot_admins: tuple[str, ...] = (),
    mod_roles_service: ModRolesService | None = None,
    guild_id: str = "",
    kingdoms_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the Postuler pinned panel: role lines, Enroll and sheet buttons."""
    strings = _strings(locale)
    del (
        candidatures_channel,
        bot_admins,
        mod_roles_service,
        guild_id,
        kingdoms_service,
    )

    from kingdoms.mods.kingdoms.kingdom_persistent import KingdomApplyButton, KingdomRoleInfoButton

    apply_button = KingdomApplyButton(strings["apply_button"][:80], discord.ButtonStyle.primary)
    apply_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    apply_row.add_item(apply_button)
    roleinfo_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    roleinfo_row.add_item(KingdomRoleInfoButton("king", strings["roleinfo_king_button"]))
    roleinfo_row.add_item(KingdomRoleInfoButton("lord", strings["roleinfo_lord_button"]))
    view = discord.ui.LayoutView(timeout=None)
    items: list[discord.ui.Item[discord.ui.LayoutView]] = [
        discord.ui.TextDisplay(f"# {APPLY_TITLE}"),
        discord.ui.Separator(),
    ]
    if _apply_artwork_path().is_file():
        items.append(
            discord.ui.Section(
                discord.ui.TextDisplay(APPLY_TAGLINE),
                accessory=discord.ui.Thumbnail(media="attachment://kingdoms_artwork.png"),
            )
        )
    else:
        items.append(discord.ui.TextDisplay(APPLY_TAGLINE))
    # One short line per role (postulation-v1): the player picks a camp
    # before opening the enrollment flow, and each line has its own
    # “View the role” rights & obligations sheet button.
    items.extend(
        [
            discord.ui.TextDisplay(strings["apply_role_king_line"]),
            discord.ui.TextDisplay(strings["apply_role_lord_line"]),
            discord.ui.Separator(),
            apply_row,
            roleinfo_row,
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {PANEL_MARKER}"),
        ]
    )
    view.add_item(
        discord.ui.Container(
            *items,
            accent_colour=GOLD,
        )
    )
    return view


async def build_settings_panel(
    logs_service: LogService | None,
    guild_id: str,
    by: str,
    bot_admins: tuple[str, ...] = (),
    roles_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the Paramètres panel: the admin season actions.

    Language and timezone live in the platform core (bot-admin) — the mod
    only exposes its own season actions here.
    """
    locale = "en"
    if logs_service is not None:
        try:
            locale = await logs_service.get_locale(guild_id)
        except Exception:
            logger.warning("KINGDOM SETTINGS: locale read failed", exc_info=True)
    strings = _strings(locale)

    from kingdoms.mods.kingdoms.kingdom_persistent import KingdomAdminButton
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings as profile_strings

    admin_strings = profile_strings(locale)
    # The time seams moved to the Machine à temps salon (Drasah's
    # deduplication rule, 2026-10-11): age-next/age-prev, the forced
    # Lord's Day window, end-season and back-to-setup now live in the
    # kingdom_time_panel — Paramètres keeps only the season bootstrap
    # and the server maintenance.
    # « Démarrer la beta » sits next to « Démarrer la saison » (Drasah
    # 17/10): the same beta-weekend-v2 flow as the console's answer.
    buttons = (
        ("launch", "launch_button", discord.ButtonStyle.success),
        ("start-season", "start_season_button", discord.ButtonStyle.success),
        ("start-beta", "start_beta_button", discord.ButtonStyle.success),
        ("status", "status_button", discord.ButtonStyle.secondary),
        ("deploy", "deploy_button", discord.ButtonStyle.primary),
        ("sync", "sync_button", discord.ButtonStyle.secondary),
        ("reset", "reset_salons_button", discord.ButtonStyle.danger),
        ("assign", "assign_button", discord.ButtonStyle.primary),
        ("add-kingdom", "add_kingdom_button", discord.ButtonStyle.primary),
        ("season-mode", "season_mode_button", discord.ButtonStyle.secondary),
        ("remove", "remove_player_button", discord.ButtonStyle.danger),
    )
    # Rows pack in chunks of 5: an overlong ActionRow is rejected by
    # Discord and kills the whole panel send (the D68 lesson).
    rows: list[discord.ui.ActionRow[discord.ui.LayoutView]] = []
    for index, (action, key, style) in enumerate(buttons):
        if index % 5 == 0:
            rows.append(discord.ui.ActionRow())
        rows[index // 5].add_item(KingdomAdminButton(action, admin_strings[key][:80], style))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {strings['settings_title']}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"## 🛠️ {admin_strings['admin_section']}"),
            *rows,
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {SETTINGS_PANEL_MARKER}"),
            accent_colour=GREEN,
        )
    )
    return view


# marche-visuel-v2 (Drasah 28/10): la table unique du marchand —
# quatre rayons (sa taxonomie, son ordre) : (clé strings de l'en-tête,
# clés d'articles dans l'ordre d'étalage). Un rayon ≤ 5 boutons = une
# seule ActionRow (règle D68 : jamais de rangée vide sérialisée).
MARKET_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("market_section_ia", ("traquenard", "jeu_d_armes")),
    ("market_section_defense", ("patrouille", "garde_royale")),
    ("market_section_attaque", ("sabotage", "contre_espionnage", "embuscade")),
    ("market_section_speciales", ("explorateur", "mariage_arrange", "corruption")),
)


async def build_market_panel(
    locale: str,
    kingdoms_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the Marché panel: the King's technology & action shop (§20).

    Buttons only — every purchase is confirmed through an ephemeral
    answer at click time, so the panel itself carries no state and is
    restart-proof (custom_id rebuild, §3b).
    """
    strings = _strings(locale)
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomMarketActionButton,
        KingdomMarketTechButton,
    )

    technologies: Any = None
    if kingdoms_service is not None:
        technologies = getattr(kingdoms_service, "config", None)
        technologies = getattr(technologies, "technologies", None)
    if technologies is None:
        from kingdoms.mods.kingdoms.config import TechnologyCosts

        technologies = TechnologyCosts()

    # marche-visuel-v2 (Drasah 28/10): one merchant stall, four shelves
    # in HIS order — every purchase button wears the same colour; the
    # tech/action button CLASSES keep their distinct callbacks.
    tech_class_keys = {
        "embuscade",
        "traquenard",
        "contre_espionnage",
        "sabotage",
        "jeu_d_armes",
    }
    shelf_items: list[Any] = []
    for header_key, keys in MARKET_SECTIONS:
        shelf_items.append(discord.ui.TextDisplay(f"## {strings[header_key]}"))
        row = discord.ui.ActionRow()
        for key in keys:
            cost = int(getattr(technologies, key))
            label = (
                strings["market_tech"][key]
                if key in tech_class_keys
                else strings["market_action"][key]
            )
            button_cls = (
                KingdomMarketTechButton
                if key in tech_class_keys
                else KingdomMarketActionButton
            )
            row.add_item(
                button_cls(
                    key,
                    f"{label} — {cost} ⚙️",
                    discord.ButtonStyle.primary,
                )
            )
        shelf_items.append(row)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {strings['market_title']}"),
            discord.ui.Separator(),
            # marche-crier-off-v1 (Drasah 29/10): no crier line anymore.
            # marche-v2: the King-only rule is finally visible on the
            # pinned panel (Drasah's decision, 2026-10-13).
            discord.ui.TextDisplay(strings["market_hint_panel"]),
            *shelf_items,
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {MARKET_PANEL_MARKER}"),
            accent_colour=GOLD,
        )
    )
    return view


async def _deploy_market_panel(
    channel: discord.TextChannel,
    locale: str,
    kingdoms_service: Any,
) -> None:
    """Edit the market panel in place, or pin a fresh one (vues-silencieuses-v1)."""
    from kingdoms.mods.kingdoms.marche_art import banner_file

    view = await build_market_panel(locale, kingdoms_service)
    # marche-visuel-v2: the banner (generated crier scene) rides as an
    # attachment on the pinned panel message — attached on the first
    # edit after upload, kept afterwards (never re-uploaded in steady
    # state, and no drop when the asset is absent).
    banner = banner_file()
    marked: list[Any] = [
        message
        for message in await channel_messages(channel)
        if MARKET_PANEL_MARKER in message_text(message)
    ]
    if marked:
        for duplicate in marked[1:]:
            try:
                await duplicate.delete()
            except Exception:
                logger.warning("KINGDOM PANELS: market duplicate purge failed", exc_info=True)
        existing = marked[0]
        text_converged = message_text_squashed(existing) == view_text(view)
        banner_missing = banner is not None and not existing.attachments
        if text_converged and not banner_missing:
            return
        try:
            if banner is not None:
                await existing.edit(
                    view=view,
                    files=[discord.File(str(banner), filename=f"marche_banner{banner.suffix.lower()}")],
                )
            else:
                await existing.edit(view=view)
            return
        except Exception:
            logger.warning("KINGDOM PANELS: market panel edit failed, resending", exc_info=True)
    if banner is not None:
        await channel.send(
            view=view,
            files=[discord.File(str(banner), filename=f"marche_banner{banner.suffix.lower()}")],
            allowed_mentions=NO_MENTIONS,
        )
    else:
        await channel.send(view=view, allowed_mentions=NO_MENTIONS)


async def refresh_season_status(
    guild: discord.Guild,
    locale: str,
    kingdoms: tuple[str, ...] | list[str] = (),
    queued: int = 0,
    progress: str = "",
) -> bool:
    """Post (or refresh) the season status message in the dedicated channel."""
    strings = _strings(locale)
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    channel = next(
        (c for c in guild.text_channels if _slug(c.name) == SEASON_STATUS_CHANNEL),
        None,
    )
    if channel is None:
        return False
    names = list(kingdoms)
    lines = [
        f"# {strings['season_status_title']}",
    ]
    if progress:
        lines.append(f"**{progress}**")
    names_line = ", ".join(names) if names else strings["season_status_empty"]
    lines.extend(
        [
            f"**{strings['season_status_kingdoms']}** : {names_line}",
            f"**{strings['season_status_queue']}** : {queued if queued else strings['season_status_empty']}",
            f"*{strings['season_status_hint']}*",
            SEASON_STATUS_MARKER,
        ]
    )
    content = "\n".join(lines)
    for message in await channel_messages(channel):
        if SEASON_STATUS_MARKER in message_text(message):
            try:
                await message.edit(content=content)
            except Exception:
                logger.warning("KINGDOM PANELS: season status refresh failed", exc_info=True)
            return True
    await channel.send(content)
    return True



async def _deploy_settings_panel(
    channel: discord.TextChannel,
    logs_service: LogService | None,
    guild_id: str,
    bot_admins: tuple[str, ...],
) -> None:
    """Edit the settings panel in place, or pin a fresh one (vues-silencieuses-v1).

    Stale legacy settings messages (old locale/timezone panel, componentless
    leftovers) are still purged, but a properly marked panel is now edited
    in place with an identity guard instead of delete+send.
    """
    view = await build_settings_panel(logs_service, guild_id, "system", bot_admins)
    marked: list[Any] = []
    for message in await channel_messages(channel):
        text = message_text(message)
        if SETTINGS_PANEL_MARKER in text:
            marked.append(message)
            continue
        stale = (
            "kingdoms:settings:" in text
            or (
                getattr(message, "author", None) is not None
                and bool(getattr(message.author, "bot", False))
                and (
                    getattr(message, "view", None) is not None
                    or bool(getattr(message, "components", None))
                )
            )
        )
        if stale:
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM PANELS: old settings panel removal failed", exc_info=True)
    if marked:
        for duplicate in marked[1:]:
            try:
                await duplicate.delete()
            except Exception:
                logger.warning("KINGDOM PANELS: settings duplicate purge failed", exc_info=True)
        existing = marked[0]
        if message_text_squashed(existing) == view_text(view):
            return
        try:
            await existing.edit(view=view)
            return
        except Exception:
            logger.warning("KINGDOM PANELS: settings panel edit failed, resending", exc_info=True)
    await channel.send(view=view, allowed_mentions=NO_MENTIONS)


async def _deploy_apply_panel(
    channel: discord.TextChannel,
    candidatures: discord.TextChannel | None,
    locale: str,
    bot_admins: tuple[str, ...],
    mod_roles_service: ModRolesService | None,
    guild_id: str,
    kingdoms_service: Any,
) -> None:
    """Remove the old apply panel then pin a fresh one."""
    for message in await channel_messages(channel):
        if PANEL_MARKER in message_text(message):
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM PANELS: old apply panel removal failed", exc_info=True)
    kwargs: dict[str, Any] = {}
    if _apply_artwork_path().is_file():
        kwargs["file"] = discord.File(_apply_artwork_path(), filename="kingdoms_artwork.png")
    await channel.send(
        view=await build_apply_panel(
            locale,
            candidatures,
            bot_admins,
            mod_roles_service,
            guild_id,
            kingdoms_service,
        ),
        allowed_mentions=NO_MENTIONS,
        **kwargs,
    )


async def _deploy_channel_panel(
    channel: discord.TextChannel,
    *,
    candidatures: discord.TextChannel | None,
    locale: str,
    logs_service: LogService | None,
    guild_id: str,
    bot_admins: tuple[str, ...],
    mod_roles_service: ModRolesService | None,
    kingdoms_service: Any,
) -> str | None:
    """Pin the panel matching one channel (its report key, None otherwise)."""
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    slug = _slug(channel.name)
    if slug == "postuler":
        await _deploy_apply_panel(
            channel, candidatures, locale, bot_admins, mod_roles_service, guild_id, kingdoms_service
        )
        return "postuler"
    if slug == "parametres":
        await _deploy_settings_panel(channel, logs_service, guild_id, bot_admins)
        return "paramètres"
    if slug == MARKET_CHANNEL or slug == "bourse":
        # marche-emoji-v1 (Drasah 26/10): le salon redevient « 💰 marché »
        # (seul l'emoji caddie gênait) — le slug « bourse » reste accepté
        # pour rattraper un salon déjà renommé par la passe précédente.
        await _deploy_market_panel(channel, locale, kingdoms_service)
        return "marché"
    if slug == "royaumes":
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring
        from kingdoms.mods.kingdoms.kingdom_realm_content import deploy_gaia_content
        from kingdoms.mods.kingdoms.kingdom_realms import (
            deploy_realms_panel,
            ensure_all_realm_structures,
        )

        await ensure_all_realm_structures(channel.guild, kingdoms_service)
        await deploy_realms_panel(channel.guild, locale, kingdoms_service)
        # restructuration-serveur-v1 c (Drasah 19/10): the Gaïa category
        # drops to ONE salon — its Territoire view (AI level live)
        # deploys with the same pass.
        try:
            await deploy_gaia_content(channel.guild, _wiring(), locale)
        except Exception:
            logger.warning("KINGDOM PANELS: gaia content deploy failed", exc_info=True)
        return "royaumes"
    if slug == "taverne":
        # taverne-immersion-v1 (Drasah 26/10): the 🍺 taverne salon keeps
        # ONLY the facade — outside art + door-in button (own marker).
        # taverne-panels-migration-v1 (Drasah 26/10): the drinks panel
        # and the Chief's auction MIGRATED inside the Comptoir salon of
        # the private Taverne category (deployed by the provisioning
        # pass of tavern_immersion, swept from here on every redeploy).
        from kingdoms.mods.kingdoms.tavern_immersion import deploy_tavern_facade

        try:
            await deploy_tavern_facade(channel, locale)
        except Exception:
            logger.warning(
                "KINGDOM PANELS: taverne facade deploy failed", exc_info=True
            )
        return "taverne"
    if slug == "machine-a-temps":
        from kingdoms.mods.kingdoms.kingdom_time_panel import deploy_time_panel

        await deploy_time_panel(channel, locale)
        return "machine à temps"
    if slug == "distributeur":
        from kingdoms.mods.kingdoms.kingdom_distributor_panel import (
            deploy_distributor_panel,
        )

        await deploy_distributor_panel(channel, locale)
        return "distributeur"
    if slug in ("declarer-une-attaque", "attaquer"):
        from kingdoms.mods.kingdoms.kingdom_attack_panel import deploy_attack_panel

        await deploy_attack_panel(channel, locale, kingdoms_service)
        return "déclarer une attaque"
    if slug in ("scriptorium", "📜-scriptorium"):
        from kingdoms.mods.kingdoms.kingdom_scriptorium import deploy_scriptorium_panel

        await deploy_scriptorium_panel(channel, locale, kingdoms_service)
        return "scriptorium"
    return None


async def deploy_panels(
    guild: discord.Guild,
    logs_service: LogService | None,
    bot_admins: tuple[str, ...] = (),
    mod_roles_service: ModRolesService | None = None,
    kingdoms_service: Any = None,
) -> dict[str, str]:
    """Deploy the pinned panels into Postuler, Paramètres and Marché.

    Returns a report dict {panel: status}. Idempotent in v1: each
    deployment pins a fresh panel message (existing pins from previous
    versions are not removed — a later slice will adopt them).
    """
    report: dict[str, str] = {}
    locale = "en"
    guild_id = str(guild.id)
    if logs_service is not None:
        try:
            locale = await logs_service.get_locale(guild_id)
        except Exception:
            logger.warning("KINGDOM PANELS: locale read failed", exc_info=True)
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    candidatures = next((c for c in guild.text_channels if _slug(c.name) == "candidatures"), None)
    for channel in guild.text_channels:
        key = await _deploy_channel_panel(
            channel,
            candidatures=candidatures,
            locale=locale,
            logs_service=logs_service,
            guild_id=guild_id,
            bot_admins=bot_admins,
            mod_roles_service=mod_roles_service,
            kingdoms_service=kingdoms_service,
        )
        if key is not None:
            report[key] = "deployed"
    context = _ApplicationContext(
        locale=locale,
        bot_admins=bot_admins,
        mod_roles_service=mod_roles_service,
        guild_id=guild_id,
        kingdoms_service=kingdoms_service,
    )
    try:
        if await refresh_season_status(
            guild,
            locale,
            kingdoms=await context.declared_kingdom_names(),
            progress=await _season_progress(kingdoms_service, locale),
        ):
            report["statut saison"] = "deployed"
    except Exception:
        logger.warning("KINGDOM PANELS: season status deployment failed", exc_info=True)
    try:
        if await refresh_changelog(guild, locale):
            report["update"] = "deployed"
    except Exception:
        logger.warning("KINGDOM PANELS: changelog deployment failed", exc_info=True)
    from kingdoms.mods.kingdoms.kingdom_content import refresh_salons_content

    report.update(await refresh_salons_content(guild, locale, kingdoms_service))
    return report


async def _season_progress(kingdoms_service: Any, locale: str) -> str:
    """Compute the cycle/age progress line (empty when no season runs)."""
    if kingdoms_service is None:
        return ""
    try:
        season = await kingdoms_service.current_season()
        if season is None:
            return ""
        return season_label(season, kingdoms_service.config, locale=locale)
    except Exception:
        logger.warning("KINGDOM PANELS: season progress read failed", exc_info=True)
        return ""


def register_kingdom_panels_command(
    tree: app_commands.CommandTree[discord.Client],
    logs_service: LogService | None = None,
    bot_admins: tuple[str, ...] = (),
    mod_roles_service: ModRolesService | None = None,
    kingdoms_service: Any = None,
) -> None:
    """Register the /kingdom command: bootstrap + panels, admin only."""

    @tree.command(
        name=localized("commands.kingdom_name", "kingdom"),
        description=localized(
            "commands.kingdom_description",
            "Bootstrap the Kingdoms salons and panels (admin only)",
        ),
    )
    @app_commands.default_permissions(administrator=True)
    async def kingdom_command(interaction: discord.Interaction) -> None:
        """Provision the structure, deploy the panels, report both."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("The /kingdom command must run inside a server.", ephemeral=True)
            return
        if not _is_admin(interaction, bot_admins):
            await interaction.response.send_message(
                "You are not allowed to bootstrap Kingdoms (admins only).",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from kingdoms.mods.kingdoms.kingdom_setup import provision_structure

            created, adopted = await provision_structure(guild)
            panels = await deploy_panels(guild, logs_service, bot_admins, mod_roles_service, kingdoms_service)
        except Exception as exc:
            logger.exception("KINGDOM SETUP: provisioning failed for guild %s", guild.id)
            await interaction.followup.send(f"❌ Setup failed: `{type(exc).__name__}: {exc}`"[:2000], ephemeral=True)
            return
        from kingdoms.mods.kingdoms.kingdom_setup import build_setup_report_view

        locale = str(interaction.locale) if interaction.locale else "en"
        view = build_setup_report_view(created, adopted, locale)
        extra = "\n".join(f"{name}: {status}" for name, status in panels.items())
        if extra:
            await interaction.followup.send(content=extra, ephemeral=True)
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.followup.send(view=view, ephemeral=True)


async def _provision_profile_note(
    context: _ApplicationContext,
    interaction: discord.Interaction,
    strings: dict[str, Any],
    *,
    is_king: bool,
    kingdom_name: str,
    queued: bool,
    insight: str,
    game_id: str,
    smurfs: tuple[str, ...],
) -> str:
    """Provision the applicant's profile message; return the note (or "")."""
    guild = interaction.guild
    if guild is None:
        return ""
    try:
        from kingdoms.mods.kingdoms.kingdom_profiles import (
            build_profile_message,
            ensure_profile_channel,
        )

        profile_channel = await ensure_profile_channel(guild, interaction.user)  # type: ignore[arg-type]
        if profile_channel is not None:
            role_label = strings["role_king"] if is_king else strings["role_lord"]
            content, profile_view = build_profile_message(
                context.locale,
                member_name=interaction.user.name or "joueur",
                state=strings["pending"],
                role_label=role_label,
                kingdom=kingdom_name,
                queued=queued,
                insight=insight,
                game_id=game_id,
                smurfs=smurfs,
            )
            await profile_channel.send(content, view=profile_view)
            return str(strings["profile_created"])
    except Exception:
        logger.warning("KINGDOM PANELS: profile provisioning failed", exc_info=True)
    return ""
