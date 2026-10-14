"""Kingdoms mod attack phases Discord surface (D77).

The pinned panel of the Attaquer salon (marker ``<!-- kingdoms-attack
-->``) lists the ongoing attacks and carries the restart-proof
``kingdoms:attack:declare`` / ``kingdoms:attack:defend:<id>`` buttons.
The scheduler tick (``run_attack_phases_safe``) drives the phases:
the T-15 reveal announcement, the T-5 pick & ban DMs and the
meeting-time forfeits — every step replays safely after a restart
through the service-side flags.

Lesson Check 30: the persistent buttons read ONLY this module's
strings (FR/EN mirrors below).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

logger = logging.getLogger(__name__)

ATTACK_PANEL_MARKER = "<!-- kingdoms-attack -->"
_PARIS = ZoneInfo("Europe/Paris")

_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "panel_title": "⚔️ **Champ de bataille — Attaquer**",
        "panel_help": (
            "Déclarez une attaque sur un territoire adverse — le RDV est fixé à T.\n"
            "La défense reste ouverte jusqu'à T-15 : après le reveal, plus personne "
            "ne répond (l'IA du royaume défend à votre place).\n"
            "À T-5, le pick & ban part en MP : 3 civilisations classées de votre "
            "main, jusqu'à 2 sabotages, validation des deux côtés avant T."
        ),
        "panel_empty": "🕊️ Aucune attaque en cours.",
        "attack_line": "⚔️ **{map}** — {attacker} ({kingdom}) 🆚 {defender} — RDV **{time}** (Paris)",
        "defender_pending": "en attente de défense",
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
            "⚔️ **{attacker}** ({kingdom}) attaque **{map}** 🆚 {defender_kingdom} "
            "— RDV **{time}** (heure de Paris) !"
        ),
        "defense_done": "🛡️ **{map}** — tu portes la défense de ton royaume ! RDV **{time}** (Paris).",
        "defense_announce": "🛡️ **{lord}** défend **{map}** !",
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
            " le pseudo d'un autre seigneur, démasqué au reveal T-15)"
        ),
        "cover_yes_button": "🕶️ OUI — bluffer",
        "cover_no_button": "❌ NON — à visage découvert",
        "choose_cover": "🕶️ Sous le pseudo de quel seigneur déclarer l'attaque ?",
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
            "• **T-15** — reveal : le bluff (s'il y en a un) tombe ; sans défenseur,"
            " l'IA du royaume prend la défense.\n"
            "• **T-5** — le défenseur reçoit ses DM pick & ban.\n"
            "• **T** — échéance : sans résultat, le territoire reste au défenseur"
            " (forfait de l'attaquant).\n"
            "🛡️ N'importe quel seigneur du royaume défenseur peut défendre via le"
            " bouton du panel **Déclarer une attaque**.\n"
            "🪤 Un royaume peut activer un Traquenard sur son adversaire : l'IA de"
            " défense devient Facile pendant 6h et les mariages scellés prennent +2h"
            " de verrou."
        ),
        "reveal_cover": (
            "🕶️ Reveal T-15 — l'attaque sur **{map}** était menée par **{lord}** "
            "(la couverture tombe)."
        ),
        "reveal_ai": (
            "🤖 Personne n'a défendu **{map}** — l'IA du royaume **{kingdom}** prend la défense"
            " (niveau **{level}**/5) !"
        ),
        "reveal_defense": "🛡️ La défense de **{map}** est verrouillée — RDV **{time}** (Paris).",
        "pickban_dm": (
            "⚔️ **Pick & ban — {map}**\nClasse tes **{count}** civilisations (ton "
            "classement ordonne les replis) et sabotes jusqu'à **2** civilisations "
            "de la main adverse. Valide avant T — un manqué est un forfait !\n"
            "Ta main : {hand}\nMain adverse : {theirs}"
        ),
        "pickban_pick": "Choix {n}",
        "pickban_sabotage": "Sabotages (max 2, optionnel)",
        "pickban_validate": "✅ Valider",
        "pickban_missing": "❌ Classe d'abord tes {count} civilisations.",
        "pickban_waiting": "⏳ Picks enregistrés et validés — en attente de l'adversaire.",
        "pickban_resolved": "⚔️ Civs en jeu — toi : **{mine}** · adversaire : **{theirs}**. Bonne partie !",
        "pickban_announce": "⚔️ **{map}** — la partie part sur **{mine}** 🆚 **{theirs}** !",
        "forfeit_both": (
            "🏳️ Forfait général sur **{map}** — personne n'a validé : l'attaquant, présent, l'emporte "
            "(**{kingdom}**)."
        ),
        "forfeit_side": "🏳️ Forfait sur **{map}** — **{side}** a manqué la validation : victoire de **{kingdom}**.",
        "side_attacker": "l'attaquant",
        "side_defender": "le défenseur",
    },
    "en": {
        "panel_title": "⚔️ **Battlefield — Attack**",
        "panel_help": (
            "Declare an attack on an adversary territory — the meeting is set at T.\n"
            "The defense stays open until T-15: after the reveal nobody answers "
            "anymore (the kingdom's AI defends instead).\n"
            "At T-5 the pick & ban lands in your DMs: 3 ranked civilizations "
            "from your hand, up to 2 sabotages, both sides validate before T."
        ),
        "panel_empty": "🕊️ No ongoing attack.",
        "attack_line": "⚔️ **{map}** — {attacker} ({kingdom}) 🆚 {defender} — meets **{time}** (Paris)",
        "defender_pending": "waiting for a defender",
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
            "⚔️ **{attacker}** ({kingdom}) attacks **{map}** 🆚 {defender_kingdom} "
            "— meeting **{time}** (Paris time)!"
        ),
        "defense_done": "🛡️ **{map}** — you carry your kingdom's defense! Meeting **{time}** (Paris).",
        "defense_announce": "🛡️ **{lord}** defends **{map}**!",
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
            " another lord's name, unmasked at the T-15 reveal)"
        ),
        "cover_yes_button": "🕶️ YES — bluff",
        "cover_no_button": "❌ NO — open-faced",
        "choose_cover": "🕶️ Under which lord's name should the attack be declared?",
        "announce_v1": (
            "⚔️ {attacker} of kingdom {kingdom} has launched an attack on kingdom"
            " {defender}!\n📅 {date}\nGood luck to the fighters! 🍀"
        ),
        "announce_v1_traquenard": (
            "\n🪤 A Traquenard is active — the defender kingdom's AI is Easy (6h window)!"
        ),
        "date_full": "{weekday} {day}/{month}/{year} at {hour}:{minute}",
        "weekdays": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "thread_title": "⚔️ {kingdom} 🆚 {defender} — {map}",
        "thread_body": (
            "📜 **Attack rundown & defender rights**\n"
            "• **T-15** — reveal: the bluff (if any) drops; with no defender, the"
            " kingdom's AI takes the defense.\n"
            "• **T-5** — the defender receives their pick & ban DMs.\n"
            "• **T** — deadline: with no result, the territory stays with the"
            " defender (attacker forfeit).\n"
            "🛡️ Any lord of the defending kingdom may defend via the button of the"
            " **Declare an attack** panel.\n"
            "🪤 A kingdom may activate a Traquenard on its adversary: the defense AI"
            " turns Easy for 6h and sealed marriages gain +2h of lock."
        ),
        "reveal_cover": (
            "🕶️ T-15 reveal — the attack on **{map}** was led by **{lord}** "
            "(the cover drops)."
        ),
        "reveal_ai": (
            "🤖 Nobody defended **{map}** — the AI of kingdom **{kingdom}** takes the defense"
            " (level **{level}**/5)!"
        ),
        "reveal_defense": "🛡️ The defense of **{map}** is locked — meeting **{time}** (Paris).",
        "pickban_dm": (
            "⚔️ **Pick & ban — {map}**\nRank your **{count}** civilizations (your "
            "ranking orders the fallbacks) and sabotage up to **2** civilizations "
            "of the adversary hand. Validate before T — a miss is a forfeit!\n"
            "Your hand: {hand}\nAdversary hand: {theirs}"
        ),
        "pickban_pick": "Choice {n}",
        "pickban_sabotage": "Sabotages (max 2, optional)",
        "pickban_validate": "✅ Validate",
        "pickban_missing": "❌ Rank your {count} civilizations first.",
        "pickban_waiting": "⏳ Picks recorded and validated — waiting for the adversary.",
        "pickban_resolved": "⚔️ Civs in play — you: **{mine}** · adversary: **{theirs}**. Good game!",
        "pickban_announce": "⚔️ **{map}** — the game starts on **{mine}** 🆚 **{theirs}**!",
        "forfeit_both": (
            "🏳️ General forfeit on **{map}** — nobody validated: the attacker, present, wins "
            "(**{kingdom}**)."
        ),
        "forfeit_side": "🏳️ Forfeit on **{map}** — **{side}** missed the validation: victory of **{kingdom}**.",
        "side_attacker": "the attacker",
        "side_defender": "the defender",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _STRINGS["fr"] if str(locale).lower().startswith("fr") else _STRINGS["en"]


def _locale(interaction: discord.Interaction) -> str:
    return str(interaction.locale) if interaction.locale else "fr"


def _fmt_paris(moment: Any) -> str:
    """Format a moment in Paris time (the players' clock, D77)."""
    when = moment if isinstance(moment, datetime) else datetime.fromtimestamp(moment, tz=_PARIS)
    return when.astimezone(_PARIS).strftime("%d/%m %H:%M")


def _attack_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    """Return the guild's Attaquer salon (None when absent)."""
    from kingdoms.mods.kingdoms.kingdom_setup import _slug

    if guild is None:
        return None
    return next(
        (c for c in getattr(guild, "text_channels", []) if _slug(getattr(c, "name", "")) == "attaquer"),
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


def _panel_view(strings: dict[str, str], attacks: list[Any]) -> discord.ui.View:
    """Build the pinned panel view (declare + one defend button per DECLARED)."""
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomAttackDeclareButton(strings["declare_button"]))
    for attack in attacks:
        if str(getattr(getattr(attack, "state", None), "value", getattr(attack, "state", ""))) == "declared":
            view.add_item(KingdomAttackDefendButton(str(attack.id), strings["defend_button"]))
    return view


def panel_text(
    strings: dict[str, str], rows: list[str]
) -> str:
    """Build the pinned panel message (title, help, live rows, marker)."""
    body = rows if rows else [strings["panel_empty"]]
    return "\n".join(
        (strings["panel_title"], "", strings["panel_help"], "", *body, ATTACK_PANEL_MARKER)
    )


async def _ongoing_rows(
    attacks_service: Any, kingdoms_service: Any, strings: dict[str, str]
) -> tuple[list[str], list[Any]]:
    """Return the live rows of the ongoing attacks and the attacks themselves."""
    from kingdoms.mods.kingdoms.models import AttackState

    rows: list[str] = []
    attacks: list[Any] = []
    try:
        attacks = [a for a in await attacks_service.attacks() if not a.is_over]
    except Exception:
        logger.warning("KINGDOM ATTACK: attack list read failed", exc_info=True)
        return [], []
    for attack in attacks:
        if getattr(attack, "kind", "player") is not None and str(getattr(attack, "kind", "")) != "player":
            continue  # the Gaïa free-for-all keeps its own surface
        if not attack.revealed and attack.cover_lord_id:
            attacker = await _lord_name(kingdoms_service, attack.cover_lord_id)
            attacker += strings["covered_tag"]
        else:
            attacker = await _lord_name(kingdoms_service, attack.attacker_lord_id)
        kingdom = await _kingdom_name(kingdoms_service, attack.attacker_kingdom_id)
        if getattr(attack, "state", None) is AttackState.DEFENDED:
            defender = (
                strings["defender_ai"]
                if attack.ai_defender
                else await _lord_name(kingdoms_service, attack.defender_lord_id)
            )
        else:
            defender = strings["defender_pending"]
        rows.append(
            strings["attack_line"].format(
                map=attack.map_key,
                attacker=attacker,
                kingdom=kingdom,
                defender=defender,
                time=_fmt_paris(attack.expires_at),
            )
        )
    return rows, attacks


async def deploy_attack_panel(
    channel: discord.TextChannel,
    locale: str,
    kingdoms_service: Any = None,
    attacks_service: Any = None,
) -> None:
    """Pin (or refresh) the attack panel message in the Attaquer salon."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    kingdoms_service = kingdoms_service or wiring.kingdoms_service
    attacks_service = attacks_service or wiring.attacks_service
    strings = _strings(locale)
    rows: list[str] = []
    attacks: list[Any] = []
    if attacks_service is not None:
        rows, attacks = await _ongoing_rows(attacks_service, kingdoms_service, strings)
    try:
        for message in [m async for m in channel.history(limit=50)]:
            if ATTACK_PANEL_MARKER in (message.content or ""):
                await message.delete()
    except Exception:
        logger.warning("KINGDOM ATTACK: old panel removal failed", exc_info=True)
    try:
        await channel.send(content=panel_text(strings, rows), view=_panel_view(strings, attacks))
    except Exception:
        logger.warning("KINGDOM ATTACK: panel send failed", exc_info=True)


async def refresh_attack_panel_safe(guild: discord.Guild | None, locale: str) -> None:
    """Re-pin one guild's attack panel after a mutation (best effort)."""
    channel = _attack_channel(guild)
    if channel is None:
        return
    await deploy_attack_panel(channel, locale)


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
            wiring, str(getattr(lord, "kingdom_id", "") or ""), strings
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
    ("water", "💧"),
    ("open", "🌞"),
    ("nomad", "🌾"),
    ("desert", "🏜️"),
    ("mountain", "⛰️"),
    ("marsh", "🪷"),
    ("lakes", "🏞️"),
    ("golden", "✨"),
    ("start_wall", "🧱"),
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


def _kingdom_option(
    kingdom: Any, level: int, count: int, strings: dict[str, str]
) -> discord.SelectOption:
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
    territories = (
        await territories_service.territories() if territories_service is not None else []
    )
    options: list[Any] = []
    protected: list[str] = []
    for kingdom in await kingdoms_service.kingdoms():
        if getattr(kingdom, "is_gaia", False) or str(kingdom.id) == own_kingdom_id:
            continue
        owned = [t for t in territories if t.owner_kingdom_id == kingdom.id]
        if not owned:
            continue
        state = await attacks_service.technology_state(str(kingdom.id))
        if patrouille_active_at(state, now, base_level=base):
            slots = patrouille_slots(state)
            span = 3 if patrouille_reinforced(state, base) else 2
            end = (slots[-1] + span) % 24 if slots else 0
            protected.append(
                strings["patrol_kingdom"].format(name=str(kingdom.name), end=f"{end:02d}")
            )
            continue
        level = await attacks_service.defense_ai_level(str(kingdom.id))
        options.append(_kingdom_option(kingdom, level, len(owned), strings))
    return options, protected


async def _kingdom_select_view(
    wiring: Any, own_kingdom_id: str, strings: dict[str, str]
) -> tuple[str, discord.ui.View | None]:
    """Step 1 — the attackable kingdoms (patrol-shielded ones stay visible)."""
    from datetime import UTC, datetime

    import discord

    kingdoms_service = wiring.kingdoms_service
    attacks_service = wiring.attacks_service
    if kingdoms_service is None or attacks_service is None:
        return "", None
    now = datetime.now(tz=UTC)
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
        lines.append(str(strings["protected_line"]).format(kingdoms=" · ".join(protected)))
    lines.append(str(strings["traquenard_hint"]))
    return "\n".join(lines), view


async def _territory_select_view(
    wiring: Any, own_kingdom_id: str, kingdom_id: str, strings: dict[str, str]
) -> tuple[str, discord.ui.View | None]:
    """Step 2 — the territories of the chosen kingdom (tags + cadastre)."""
    import discord

    kingdoms_service = wiring.kingdoms_service
    territories_service = wiring.territories_service
    if kingdoms_service is None or territories_service is None:
        return "", None
    kingdom = next(
        (k for k in await kingdoms_service.kingdoms() if str(k.id) == kingdom_id), None
    )
    if kingdom is None:
        return "", None
    catalog = {
        str(getattr(entry, "key", "")): entry
        for entry in getattr(getattr(kingdoms_service, "config", None), "maps", None) or ()
    }
    owned = [
        t for t in await territories_service.territories() if t.owner_kingdom_id == kingdom_id
    ]
    if not owned:
        return "", None
    options = [
        discord.SelectOption(
            label=str(
                getattr(catalog.get(str(t.map_key), None), "display_name", None)
                or t.map_key
            )[:100],
            value=str(t.map_key)[:100],
            description=_map_description(
                catalog.get(str(t.map_key), None), strings
            ),
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
        await _open_cover_step(wiring, target, strings, chosen[0], own_kingdom_id)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    return str(strings["choose_territory"]).format(kingdom=f"**{kingdom.name}**"), view


async def _open_cover_step(
    wiring: Any,
    interaction: discord.Interaction[Any],
    strings: dict[str, str],
    map_key: str,
    own_kingdom_id: str,
) -> None:
    """Step 3 — the counter-espionage question (skipped without a charge)."""
    import discord

    attacks_service = wiring.attacks_service
    if attacks_service is None:
        await interaction.response.send_modal(
            KingdomAttackLobbyModal(strings, map_key, None, "")
        )
        return
    state = await attacks_service.technology_state(own_kingdom_id)
    if state.purchases.get("contre_espionnage", 0) <= 0:
        await interaction.response.send_modal(
            KingdomAttackLobbyModal(strings, map_key, None, "")
        )
        return
    view = discord.ui.View(timeout=600)

    async def open_lobby(cover_lord_id: str | None, target: discord.Interaction[Any]) -> None:
        await target.response.send_modal(
            KingdomAttackLobbyModal(strings, map_key, cover_lord_id, "")
        )

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
        await target.response.edit_message(
            content=str(strings["choose_cover"]), view=cover_view
        )

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
    await interaction.response.edit_message(
        content=str(strings["cover_question"]), view=view
    )


async def _announce_declaration_safe(
    guild: Any, wiring: Any, strings: dict[str, str], attack: Any
) -> None:
    """Announce the v1-style declaration and open its rights thread."""
    channel = _attack_channel(guild)
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
    try:
        message = await channel.send(content)
    except Exception:
        logger.warning("KINGDOM ATTACK: declaration announcement failed", exc_info=True)
        return
    try:
        thread = await message.create_thread(
            name=str(strings["thread_title"]).format(
                kingdom=attacker_name, defender=defender_name, map=attack.map_key
            )[:100]
        )
        await thread.send(str(strings["thread_body"]))
    except Exception:
        logger.warning("KINGDOM ATTACK: declaration thread failed", exc_info=True)


class KingdomAttackLobbyModal(discord.ui.Modal):
    """Final step — the game lobby URL (the territory is already chosen)."""

    def __init__(
        self,
        strings: dict[str, str],
        map_key: str,
        cover_lord_id: str | None,
        kingdom_name: str,
    ) -> None:
        super().__init__(title=strings["modal_title"][:45])
        self.strings = strings
        self.map_key = map_key
        self.cover_lord_id = cover_lord_id
        self.kingdom_name = kingdom_name
        self.lobby_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            label=strings["modal_lobby"][:45], max_length=200, required=True
        )
        self.add_item(self.lobby_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Declare the attack, confirm, announce (under the cover if any)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = self.strings
        wiring = _wiring()
        attacks = wiring.attacks_service
        await interaction.response.defer(ephemeral=True)
        try:
            attack = await attacks.declare_attack(
                str(interaction.user.id),
                self.map_key,
                self.lobby_input.value.strip(),
                cover_lord_id=self.cover_lord_id,
            )
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(
                strings["failed"].format(message=message), ephemeral=True
            )
            return
        await interaction.followup.send(
            strings["declared_note"].format(
                map=attack.map_key, time=_fmt_paris(attack.expires_at)
            ),
            ephemeral=True,
        )
        await _announce_declaration_safe(interaction.guild, wiring, strings, attack)
        await refresh_attack_panel_safe(interaction.guild, _locale(interaction))


# ----------------------------------------------------------------------
# The persistent defend button (one per DECLARED attack)
# ----------------------------------------------------------------------
class KingdomAttackDefendButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:attack:defend:(?P<attack_id>[A-Za-z0-9_:\-]+)",
):
    """The restart-proof defend button of one waiting attack."""

    def __init__(self, attack_id: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:attack:defend:{attack_id}",
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
        return cls(match["attack_id"], _strings(_locale(interaction))["defend_button"])

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
            attack = await wiring.attacks_service.respond_defense(
                self.attack_id, str(interaction.user.id)
            )
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(strings["failed"].format(message=message), ephemeral=True)
            return
        await interaction.followup.send(
            strings["defense_done"].format(map=attack.map_key, time=_fmt_paris(attack.expires_at)),
            ephemeral=True,
        )
        channel = _attack_channel(interaction.guild)
        await _announce_safe(
            channel,
            strings["defense_announce"].format(
                lord=interaction.user.display_name, map=attack.map_key
            ),
        )
        await refresh_attack_panel_safe(interaction.guild, _locale(interaction))


# ----------------------------------------------------------------------
# The DM pick & ban view (T-5)
# ----------------------------------------------------------------------
class KingdomPickBanView(discord.ui.View):
    """The ephemeral DM view: ranked picks, sabotages, one validate button.

    The view lives the 5 minutes between T-5 and T — a restart inside
    that window loses the DM view (the meeting-time catch-up settles
    the attack as a forfeit), which keeps the component restart-simple.
    """

    def __init__(
        self,
        attack_id: str,
        player_id: str,
        side: str,
        hand: list[str],
        adversary_hand: list[str],
        locale: str,
        announce_channel: Any = None,
    ) -> None:
        super().__init__(timeout=600)
        self.attack_id = attack_id
        self.player_id = player_id
        self.side = side
        self.announce_channel = announce_channel
        strings = _strings(locale)
        self.strings = strings
        self.rank_selects: list[discord.ui.Select[Any]] = []
        for index in range(min(3, len(hand))):
            select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder=strings["pickban_pick"].format(n=index + 1)[:100],
                custom_id=f"kingdoms:attack:pickban:pick:{index}",
                options=[
                    discord.SelectOption(label=str(civ)[:100], value=str(civ))
                    for civ in hand
                ][:25],
                min_values=1 if index == 0 else 0,
                max_values=1,
            )
            self.rank_selects.append(select)
            self.add_item(select)
        self.sabotage_select: discord.ui.Select[Any] | None = None
        if adversary_hand:
            sabotage: discord.ui.Select[Any] = discord.ui.Select(
                placeholder=strings["pickban_sabotage"][:100],
                custom_id="kingdoms:attack:pickban:sabotage",
                options=[
                    discord.SelectOption(label=str(civ)[:100], value=str(civ))
                    for civ in adversary_hand
                ][:25],
                min_values=0,
                max_values=min(2, len(adversary_hand)),
            )
            self.sabotage_select = sabotage
            self.add_item(sabotage)
        button: discord.ui.Button[discord.ui.View] = discord.ui.Button(
            label=strings["pickban_validate"][:80],
            custom_id="kingdoms:attack:pickban:validate",
            style=discord.ButtonStyle.primary,
        )
        button.callback = self._on_validate  # type: ignore[method-assign]
        self.add_item(button)

    async def _on_validate(self, interaction: discord.Interaction) -> None:
        """Submit the ranked picks + sabotages, then validate (D77)."""
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
        sabotages = list(self.sabotage_select.values) if self.sabotage_select else []
        try:
            await wiring.attacks_service.set_picks(
                self.attack_id, self.player_id, picks, sabotages=sabotages
            )
            attack = await wiring.attacks_service.validate_pickban(self.attack_id, self.player_id)
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.response.send_message(
                strings["failed"].format(message=message), ephemeral=True
            )
            return
        pickban: dict[str, Any] = dict(attack.pickban or {})
        mine = pickban.get(f"{self.side}_picks") or picks
        theirs = pickban.get("attacker_picks" if self.side == "defender" else "defender_picks")
        if pickban.get("resolved_pickban"):
            content = strings["pickban_resolved"].format(
                mine=", ".join(mine) or "—", theirs=", ".join(theirs or []) or "—"
            )
            await _announce_safe(
                self.announce_channel,
                strings["pickban_announce"].format(
                    map=attack.map_key,
                    mine=", ".join(pickban.get("attacker_picks") or []),
                    theirs=", ".join(pickban.get("defender_picks") or []),
                ),
            )
        else:
            content = strings["pickban_waiting"]
        try:
            await interaction.response.edit_message(content=content, view=None)
        except Exception:
            logger.warning("KINGDOM ATTACK: pick & ban DM edit failed", exc_info=True)


async def _send_pickban_dm(
    bot: Any, channel: Any, attack: Any, kingdoms_service: Any, locale: str
) -> None:
    """Send the T-5 pick & ban DM to each human player of the meeting."""
    strings = _strings(locale)
    hand = await _hand_of(kingdoms_service, attack.attacker_kingdom_id)
    adversary = await _hand_of(kingdoms_service, attack.defender_kingdom_id)
    view = KingdomPickBanView(
        str(attack.id),
        str(attack.attacker_lord_id),
        "attacker",
        hand,
        adversary,
        locale,
        announce_channel=channel,
    )
    await _dm_user_safe(
        bot,
        str(attack.attacker_lord_id),
        strings["pickban_dm"].format(
            map=attack.map_key,
            count=min(3, len(hand)) or 1,
            hand=", ".join(hand) or "—",
            theirs=", ".join(adversary) or "—",
        ),
        view,
    )
    if not attack.ai_defender and attack.defender_lord_id:
        view = KingdomPickBanView(
            str(attack.id),
            str(attack.defender_lord_id),
            "defender",
            adversary,
            hand,
            locale,
            announce_channel=channel,
        )
        await _dm_user_safe(
            bot,
            str(attack.defender_lord_id),
            strings["pickban_dm"].format(
                map=attack.map_key,
                count=min(3, len(adversary)) or 1,
                hand=", ".join(adversary) or "—",
                theirs=", ".join(hand) or "—",
            ),
            view,
        )


async def _hand_of(kingdoms_service: Any, kingdom_id: str | None) -> list[str]:
    """Return the kingdom's civilizations (its Alliances hand, D77)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _kingdom_by_id_safe

    kingdom = await _kingdom_by_id_safe(kingdoms_service, kingdom_id or "")
    return list(getattr(kingdom, "civilizations", None) or []) if kingdom else []


async def _dm_user_safe(bot: Any, user_id: str, content: str, view: discord.ui.View) -> None:
    """Best-effort DM to one player."""
    try:
        user = getattr(bot, "get_user", lambda _id: None)(user_id)
        if user is None:
            user = await bot.fetch_user(int(user_id))
        if user is not None:
            await user.send(content, view=view)
    except Exception:
        logger.warning("KINGDOM ATTACK: pick & ban DM failed (%s)", user_id, exc_info=True)


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
    """Drive the D77 phases for every guild: reveal, DMs, forfeits."""
    attacks_service = getattr(bot, "kingdoms_attacks_service", None)
    kingdoms_service = getattr(bot, "kingdoms_service", None)
    if attacks_service is None:
        return
    for guild in list(getattr(bot, "guilds", [])):
        channel = _attack_channel(guild)
        if channel is None:
            continue
        locale = await _guild_locale(bot, str(guild.id))
        strings = _strings(locale)
        try:
            await _run_reveals_safe(attacks_service, kingdoms_service, channel, strings)
            for attack in await attacks_service.pick_ban_due():
                await _send_pickban_dm(bot, channel, attack, kingdoms_service, locale)
            await _run_forfeits_safe(attacks_service, kingdoms_service, channel, strings)
        except Exception:
            logger.warning("KINGDOM ATTACK: phase run failed", exc_info=True)
        await deploy_attack_panel(channel, locale, kingdoms_service, attacks_service)


async def _run_reveals_safe(
    attacks_service: Any, kingdoms_service: Any, channel: Any, strings: dict[str, str]
) -> None:
    """Announce the T-15 reveals (cover drop, AI fallback, locked defense)."""
    for attack in await attacks_service.reveal_due():
        if attack.cover_lord_id:
            await _announce_safe(
                channel,
                strings["reveal_cover"].format(
                    map=attack.map_key,
                    lord=await _lord_name(kingdoms_service, attack.attacker_lord_id),
                ),
            )
        elif attack.ai_defender:
            await _announce_safe(
                channel,
                strings["reveal_ai"].format(
                    map=attack.map_key,
                    kingdom=await _kingdom_name(kingdoms_service, attack.defender_kingdom_id),
                    level=attack.effects.get("ai_level", 1) if attack.effects else 1,
                ),
            )
        else:
            await _announce_safe(
                channel,
                strings["reveal_defense"].format(
                    map=attack.map_key, time=_fmt_paris(attack.expires_at)
                ),
            )


async def _run_forfeits_safe(
    attacks_service: Any, kingdoms_service: Any, channel: Any, strings: dict[str, str]
) -> None:
    """Announce the meeting-time forfeits of the expired attacks."""
    for attack in await attacks_service.expire_stale():
        side = (attack.pickban or {}).get("forfeit_side")
        if not side:
            continue
        kingdom = await _kingdom_name(kingdoms_service, attack.winner_kingdom_id)
        if side == "both":
            await _announce_safe(
                channel, strings["forfeit_both"].format(map=attack.map_key, kingdom=kingdom)
            )
        else:
            await _announce_safe(
                channel,
                strings["forfeit_side"].format(
                    map=attack.map_key, side=strings[f"side_{side}"], kingdom=kingdom
                ),
            )
