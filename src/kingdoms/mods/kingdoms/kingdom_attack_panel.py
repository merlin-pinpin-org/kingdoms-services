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
        "reveal_cover": (
            "🕶️ Reveal T-15 — l'attaque sur **{map}** était menée par **{lord}** "
            "(la couverture tombe)."
        ),
        "reveal_ai": "🤖 Personne n'a défendu **{map}** — l'IA du royaume **{kingdom}** prend la défense !",
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
        "reveal_cover": (
            "🕶️ T-15 reveal — the attack on **{map}** was led by **{lord}** "
            "(the cover drops)."
        ),
        "reveal_ai": "🤖 Nobody defended **{map}** — the AI of kingdom **{kingdom}** takes the defense!",
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
        """Guard the click, then open the declaration modal."""
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
        await interaction.response.send_modal(KingdomAttackDeclareModal(strings))


class KingdomAttackDeclareModal(discord.ui.Modal):
    """The declaration form: territory, lobby URL, optional cover lord."""

    def __init__(self, strings: dict[str, str]) -> None:
        super().__init__(title=strings["modal_title"][:45])
        self.strings = strings
        self.map_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            label=strings["modal_map"][:45], max_length=100, required=True
        )
        self.lobby_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            label=strings["modal_lobby"][:45], max_length=200, required=True
        )
        self.cover_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            label=strings["modal_cover"][:45], max_length=60, required=False
        )
        for item in (self.map_input, self.lobby_input, self.cover_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Declare the attack, confirm, announce (under the cover if any)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = self.strings
        wiring = _wiring()
        attacks = wiring.attacks_service
        await interaction.response.defer(ephemeral=True)
        cover = None
        digits = re.search(r"\d{5,}", self.cover_input.value or "")
        if digits is not None:
            cover = digits.group()
        try:
            attack = await attacks.declare_attack(
                str(interaction.user.id),
                self.map_input.value.strip(),
                self.lobby_input.value.strip(),
                cover_lord_id=cover,
            )
        except Exception as exc:
            message = getattr(exc, "message", None) or str(exc)
            await interaction.followup.send(strings["failed"].format(message=message), ephemeral=True)
            return
        await interaction.followup.send(
            strings["declared_note"].format(
                map=attack.map_key, time=_fmt_paris(attack.expires_at)
            ),
            ephemeral=True,
        )
        channel = _attack_channel(interaction.guild)
        shown_id = attack.cover_lord_id or attack.attacker_lord_id
        await _announce_safe(
            channel,
            strings["declared_announce"].format(
                attacker=await _lord_name(wiring.kingdoms_service, shown_id)
                + (strings["covered_tag"] if attack.cover_lord_id else ""),
                kingdom=await _kingdom_name(wiring.kingdoms_service, attack.attacker_kingdom_id),
                map=attack.map_key,
                defender_kingdom=await _kingdom_name(
                    wiring.kingdoms_service, attack.defender_kingdom_id
                ),
                time=_fmt_paris(attack.expires_at),
            ),
        )
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
