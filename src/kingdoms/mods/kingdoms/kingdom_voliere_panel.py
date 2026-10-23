"""Kingdoms mod — the 🪶 Volière panel (voliere-v2, Drasah 14/10/2026).

The volière opens from the Le-Royaume view (« Développer la volière »),
is free from the Âge Féodal and shared by every lord of the kingdom.
Voliere-v2 (Drasah's live-test rulings): one slot per age (Féodal 1,
Château 2, Impérial 3), each slot takes ONE bird, a bird already owned
can never be re-taken — and ONE action flies the WHOLE bird (no other
action of that bird until it lands). Cooldowns stay shared by the realm.

Every answer is a single ephemeral bubble (the volière is discreet by
nature) with the standard market TTL. The anonymity is player-facing
only — every action logs the acting lord for the admin (anti-abus).

The Pigeon's traité secret is PARKED until Drasah specifies the treaty
types (decision 14/10/2026).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import discord

from kingdoms.mods.kingdoms import chronicle
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.voliere_panel")

VOLIERE_ACTIONS: tuple[tuple[str, str], ...] = (
    ("corbeau_civ", "primary"),
    ("corbeau_verify", "secondary"),
    ("corbeau_track", "secondary"),
    ("faucon_territory", "primary"),
    ("faucon_sabotage", "secondary"),
    ("faucon_release", "danger"),
    ("pigeon_civ", "primary"),
    ("pigeon_question", "secondary"),
    ("pigeon_dm", "secondary"),
)

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "voliere_button": "🪶 Develop the volière",
        "voliere_title": "# 🪶 Volière of {kingdom}",
        "voliere_line": "Free from the Feudal Age — cooldowns shared by the whole kingdom.",
        "voliere_corbeau": "**Corbeau**",
        "voliere_faucon": "**Faucon**",
        "voliere_pigeon": "**Pigeon**",
        "ready_in": "ready in {hours}h",
        "ready": "ready",
        "slot_button": "🪶 Choose a bird",
        "slot_choose": "🪶 A slot is open — which bird lands in your volière?",
        "slot_open": "🕳️ Slot to fill — **choose a bird**.",
        "slot_next": "🔒 Next slot at the **{age}** age.",
        "bird_chosen": "🪶 The **{bird}** lands in your volière!",
        "bird_ready": "🪶 **{bird}** — on its perch, ready",
        "bird_flying": "🕊️ **{bird}** — flying, back <t:{ts}:R>",
        "bird_not_owned": "❌ The {bird} is not in your volière — choose it in a slot first.",
        "act_corbeau_civ": "🐦‍⬛ Steal tech points",
        "act_corbeau_verify": "🐦‍⬛ Verify an attack",
        "act_corbeau_track": "🐦‍⬛ Track a lord",
        "act_faucon_territory": "🦅 Draw a territory",
        "act_faucon_sabotage": "🦅 Sabotage a meeting",
        "act_faucon_release": "🦅 Release the faucon",
        "act_pigeon_civ": "🕊️ Draw a civilization",
        "act_pigeon_question": "🕊️ Anonymous question",
        "act_pigeon_dm": "🕊️ Anonymous MP",
        "civ_drawn": "🎲 The {bird} draws the civilization **{civ}**.",
        "territory_drawn": "🎲 The faucon draws the territory **{map}** — it joins your kingdom.",
        "verify_choose": "🐦‍⬛ Which attack should the Corbeau inspect?",
        "verify_result": (
            "🐦‍⬛ The real attacker of **{map}** is **{name}**.\n"
            "🗡️ His civilizations: **{civs}**.\n"
            "♟️ Edge: the defender sets his 3 civilizations AFTER."
        ),
        "verify_no_target": "❌ No ongoing attack targets your kingdom.",
        "track_choose": "🐦‍⬛ Which lord should the Corbeau track?",
        "track_done": "🐦‍⬛ **{name}** is tracked for 24h — every attack he declares leaves with a 24h delay.",
        "track_no_target": "❌ No trackable lord (another player kingdom).",
        "sabotage_choose": "🦅 Which meeting should the Faucon sabotage?",
        "sabotage_civ_choose": "🦅 Which civilization to sabotage (limit 2)?",
        "sabotage_done": "🦅 The Faucon sabotages **{civ}** on **{map}**.",
        "sabotage_no_target": "❌ No attack in its pick & ban phase involves your kingdom.",
        "sabotage_no_civ": "❌ The adversary holds no civilization to sabotage.",
        "release_done": "🦅 The faucon is released — for 24h your kingdom suffers NO adverse technology effect.",
        "question_title": "🕊️ Anonymous question",
        "question_input": "Your question (anonymous)",
        "question_sent": "🕊️ The question landed in the Question salon — nobody sees the sender.",
        "question_no_channel": "❌ The Question salon is absent.",
        "dm_choose": "🕊️ Which kingdom should receive the anonymous MP?",
        "dm_title": "🕊️ Anonymous MP to {kingdom}",
        "dm_input": "Your message (anonymous)",
        "dm_sent": "🕊️ The MP reached every lord of **{kingdom}** — nobody sees the sender.",
        "dm_no_target": "❌ No other player kingdom.",
        "dm_failed": "❌ The MP could not reach any lord.",
        "theft_choose": "🐦‍⬛ Which kingdom should the Corbeau raid?",
        "theft_sent": (
            "🐦‍⬛ The Corbeau flies to **{kingdom}** — the theft executes "
            "<t:{ts}:R> (alarm + 5-minute contest window there)."
        ),
        "theft_no_target": "❌ No other player kingdom.",
        "theft_contest_button": "🛡️ Contest",
        "theft_continue_button": "➡️ Continue (2nd ⚙️)",
        "theft_stop_button": "🛑 Stop (keep the 1st ⚙️)",
        "theft_stopped": "🐦‍⬛ The Corbeau flies home safe with **{points} ⚙️**.",
        "theft_continuing": (
            "🐦‍⬛ The Corbeau goes for the 2nd ⚙️ — nobody contests before "
            "<t:{ts}:R> and it leaves with **{points} ⚙️**."
        ),
        "theft_contested": (
            "🛡️ CONTEST WON — the Corbeau dies, **{points} ⚙️** refunded to the bank. "
            "The thief's Corbeau slot is lost (buyable again at the next Age)."
        ),
        "theft_not_yours": "❌ This theft does not involve your kingdom.",
        "failed": "❌ {message}",
        "no_service": "❌ The volière service is unavailable.",
        "not_a_lord": "❌ You belong to no kingdom of the current season.",
        "unknown": "unknown",
    },
    "fr": {
        "voliere_button": "🪶 Développer la volière",
        "voliere_title": "# 🪶 Volière de {kingdom}",
        "voliere_line": "Gratuite dès l'Âge Féodal — cooldowns partagés par tout le royaume.",
        "voliere_corbeau": "**Corbeau**",
        "voliere_faucon": "**Faucon**",
        "voliere_pigeon": "**Pigeon**",
        "ready_in": "prêt dans {hours}h",
        "ready": "prêt",
        "slot_button": "🪶 Choisir un oiseau",
        "slot_choose": "🪶 Un emplacement est ouvert — quel oiseau atterrit dans ta volière ?",
        "slot_open": "🕳️ Emplacement à remplir — **choisis un oiseau**.",
        "slot_next": "🔒 Prochain emplacement à l'âge **{age}**.",
        "bird_chosen": "🪶 Le **{bird}** atterrit dans ta volière !",
        "bird_ready": "🪶 **{bird}** — posé sur son perchoir, prêt",
        "bird_flying": "🕊️ **{bird}** — envolé, il revient <t:{ts}:R>",
        "bird_not_owned": "❌ Le {bird} n'est pas dans ta volière — choisis-le dans un emplacement d'abord.",
        "act_corbeau_civ": "🐦‍⬛ Voler des points technologiques",
        "act_corbeau_verify": "🐦‍⬛ Vérifier une attaque",
        "act_corbeau_track": "🐦‍⬛ Cibler un seigneur",
        "act_faucon_territory": "🦅 Piocher un territoire",
        "act_faucon_sabotage": "🦅 Sabotage en rencontre",
        "act_faucon_release": "🦅 Lâcher de faucon",
        "act_pigeon_civ": "🕊️ Piocher une civilisation",
        "act_pigeon_question": "🕊️ Question anonyme",
        "act_pigeon_dm": "🕊️ MP anonyme",
        "civ_drawn": "🎲 Le {bird} pioche la civilisation **{civ}**.",
        "territory_drawn": "🎲 Le faucon pioche le territoire **{map}** — il rejoint ton royaume.",
        "verify_choose": "🐦‍⬛ Quelle attaque le Corbeau doit-il inspecter ?",
        "verify_result": (
            "🐦‍⬛ Le véritable attaquant de **{map}** est **{name}**.\n"
            "🗡️ Ses civilisations : **{civs}**.\n"
            "♟️ Avantage : le défenseur définit ses 3 civilisations APRÈS."
        ),
        "verify_no_target": "❌ Aucune attaque en cours ne vise ton royaume.",
        "track_choose": "🐦‍⬛ Quel seigneur le Corbeau doit-il cibler ?",
        "track_done": "🐦‍⬛ **{name}** est suivi 24h — toutes ses déclarations d'attaque partent avec un délai de 24h.",
        "track_no_target": "❌ Aucun seigneur ciblable (d'un autre royaume).",
        "sabotage_choose": "🦅 Quelle rencontre le Faucon doit-il saboter ?",
        "sabotage_civ_choose": "🦅 Quelle civilisation saboter (limite 2) ?",
        "sabotage_done": "🦅 Le Faucon sabote **{civ}** sur **{map}**.",
        "sabotage_no_target": "❌ Aucune attaque en phase pick & ban n'implique ton royaume.",
        "sabotage_no_civ": "❌ L'adversaire ne détient aucune civilisation à saboter.",
        "release_done": "🦅 Le faucon est lâché — pendant 24h ton royaume ne subit AUCUN effet de technologie adverse.",
        "question_title": "🕊️ Question anonyme",
        "question_input": "Ta question (anonyme)",
        "question_sent": "🕊️ La question a atterri dans le salon Question — personne ne voit l'expéditeur.",
        "question_no_channel": "❌ Le salon Question est absent.",
        "dm_choose": "🕊️ Quel royaume doit recevoir le MP anonyme ?",
        "dm_title": "🕊️ MP anonyme à {kingdom}",
        "dm_input": "Ton message (anonyme)",
        "dm_sent": "🕊️ Le MP est parvenu à chaque seigneur de **{kingdom}** — personne ne voit l'expéditeur.",
        "dm_no_target": "❌ Aucun autre royaume.",
        "dm_failed": "❌ Le MP n'a pu atteindre aucun seigneur.",
        "theft_choose": "🐦‍⬛ Quel royaume le Corbeau doit-il piller ?",
        "theft_sent": (
            "🐦‍⬛ Le Corbeau s'envole vers **{kingdom}** — le vol s'exécute "
            "<t:{ts}:R> (alarme + fenêtre de contestation de 5 min là-bas)."
        ),
        "theft_no_target": "❌ Aucun autre royaume.",
        "theft_contest_button": "🛡️ Contester",
        "theft_continue_button": "➡️ Continuer (2e ⚙️)",
        "theft_stop_button": "🛑 STOP (garder le 1er ⚙️)",
        "theft_stopped": "🐦‍⬛ Le Corbeau repart sain et sauf avec **{points} ⚙️**.",
        "theft_continuing": (
            "🐦‍⬛ Le Corbeau tente le 2e ⚙️ — personne ne conteste avant "
            "<t:{ts}:R> et il repart avec **{points} ⚙️**."
        ),
        "theft_contested": (
            "🛡️ CONTESTATION RÉUSSIE — le Corbeau meurt, **{points} ⚙️** restitués "
            "à la banque. Le slot Corbeau du voleur est perdu (rachetable à l'Âge suivant)."
        ),
        "theft_not_yours": "❌ Ce vol n'implique pas ton royaume.",
        "failed": "❌ {message}",
        "no_service": "❌ Le service volière est indisponible.",
        "not_a_lord": "❌ Tu n'appartiens à aucun royaume de la saison en cours.",
        "unknown": "inconnu",
    },
}

BIRD_LABEL = {
    "corbeau_civ": "Corbeau",
    "pigeon_civ": "Pigeon",
}

BIRD_NAME: dict[str, str] = {"corbeau": "Corbeau", "faucon": "Faucon", "pigeon": "Pigeon"}


def _strings(locale: str) -> dict[str, str]:
    """Return the module strings for one locale (fr by default)."""
    return _STRINGS["fr" if str(locale).lower().startswith("fr") else "en"]


def _locale(interaction: discord.Interaction) -> str:
    """Extract the interaction locale (en fallback)."""
    value = str(getattr(interaction, "locale", "") or "")
    return value if value else "en"


def _now() -> datetime:
    """Return the wall clock used by the panel (hookable in tests)."""
    return game_now()


async def _voliere_state(wiring: Any, kingdom_id: str) -> dict[str, Any] | None:
    """Return the volière slot snapshot, or None when unavailable."""
    voliere = wiring.voliere_service
    if voliere is None:
        return None
    try:
        state: dict[str, Any] | None = await voliere.slot_state(kingdom_id)
        return state
    except Exception:
        logger.info("VOLIERE: slot_state failed", exc_info=True)
        return None


async def _voliere_content(
    wiring: Any, kingdom: Any, strings: dict[str, str], state: dict[str, Any] | None = None
) -> str:
    """Build the Volière sommaire — one line per bird, slots included (v2)."""
    if state is None:
        state = await _voliere_state(wiring, str(kingdom.id))
    lines = [
        strings["voliere_title"].format(kingdom=str(getattr(kingdom, "name", ""))),
        strings["voliere_line"],
        "",
    ]
    if state is None:
        lines.append(strings["no_service"])
        return "\n".join(lines)
    now_ts = int(_now().timestamp())
    cooldowns = {str(k): int(v) for k, v in (state.get("cooldowns") or {}).items()}
    for bird in [str(b) for b in (state.get("chosen") or [])]:
        ready_at = cooldowns.get(bird, 0)
        if ready_at > now_ts:
            lines.append(strings["bird_flying"].format(bird=BIRD_NAME.get(bird, bird), ts=ready_at))
        else:
            lines.append(strings["bird_ready"].format(bird=BIRD_NAME.get(bird, bird)))
    if int(state.get("open") or 0) > 0:
        lines.append(strings["slot_open"])
    next_age = str(state.get("next_age") or "")
    if next_age:
        lines.append(strings["slot_next"].format(age=next_age))
    return "\n".join(lines)


def _voliere_view(strings: dict[str, str], state: dict[str, Any] | None) -> discord.ui.View:
    """Build the ephemeral in-session action view of the volière (v2).

    Only the OWNED birds get buttons — a flying bird (cooldown) has its
    buttons disabled until it lands; an open slot adds the « choose a
    bird » button.
    """
    view = discord.ui.View(timeout=600)
    now_ts = int(_now().timestamp())
    chosen = [str(b) for b in ((state or {}).get("chosen") or [])]
    cooldowns = {str(k): int(v) for k, v in ((state or {}).get("cooldowns") or {}).items()}
    for action, style_name in VOLIERE_ACTIONS:
        bird = action.split("_", 1)[0]
        if bird not in chosen:
            continue
        style = getattr(discord.ButtonStyle, style_name, discord.ButtonStyle.secondary)
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=strings[f"act_{action}"][:80],
            custom_id=f"kingdoms:voliere:act:{action}",
            style=style,
            disabled=cooldowns.get(bird, 0) > now_ts,
        )
        button.callback = _action_callback(action)  # type: ignore[method-assign]
        view.add_item(button)
    if int((state or {}).get("open") or 0) > 0:
        choose_button: discord.ui.Button[Any] = discord.ui.Button(
            label=strings["slot_button"][:80],
            custom_id="kingdoms:voliere:slot:choose",
            style=discord.ButtonStyle.success,
        )
        choose_button.callback = _slot_button_callback()  # type: ignore[method-assign]
        view.add_item(choose_button)
    return view


def _slot_button_callback() -> Any:
    """Build the in-session « choose a bird » button callback."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    async def on_click(interaction: discord.Interaction) -> None:
        await _slot_flow(interaction, _wiring())

    return on_click


async def _slot_flow(interaction: discord.Interaction, wiring: Any, *, fresh: bool = False) -> None:
    """Open the bird-select for one empty volière slot (v2 slots).

    ``fresh`` marks a first response (the persistent realm-view button
    sends an ephemeral message; in-session buttons edit the bubble).
    """
    strings = _strings(_locale(interaction))
    if wiring.voliere_service is None or wiring.kingdoms_service is None:
        await interaction.response.send_message(strings["no_service"], ephemeral=True)
        return
    lord, kingdom = await _require_lord(interaction, strings, wiring)
    if lord is None or kingdom is None:
        return
    state = await _voliere_state(wiring, str(kingdom.id))
    available = [str(bird) for bird in ((state or {}).get("available") or [])]
    if int((state or {}).get("open") or 0) <= 0 or not available:
        if fresh:
            await interaction.response.send_message(strings["voliere_line"], ephemeral=True)
        else:
            await interaction.response.edit_message(
                content=strings["voliere_line"], view=None
            )
        return
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:voliere:slot:pick",
        placeholder=strings["slot_choose"][:100],
        options=[
            discord.SelectOption(label=BIRD_NAME.get(bird, bird)[:100], value=bird[:100])
            for bird in available[:25]
        ],
    )

    async def on_pick(target: discord.Interaction[Any]) -> None:
        values = [str(v) for v in (getattr(target, "data", None) or {}).get("values", [])]
        if not values:
            return
        try:
            await wiring.voliere_service.choose_bird(
                str(kingdom.id), values[0], now=_now()
            )
        except Exception as exc:
            _failed(target, strings, exc)
            return
        logger.info(
            "kingdoms volière: %s chooses the bird %s (admin trace)", lord.id, values[0]
        )
        try:
            await chronicle.record(
                lord,
                chronicle.KIND_BIRD_CHOSEN,
                now=_now(),
                store=wiring.kingdoms_service._store,
                bird=values[0],
            )
        except Exception:
            logger.info("VOLIERE: bird chronicle skipped", exc_info=True)
        fresh = await _voliere_state(wiring, str(kingdom.id))
        content = strings["bird_chosen"].format(bird=BIRD_NAME.get(values[0], values[0]))
        await target.response.edit_message(
            content=content, view=_voliere_view(strings, fresh)
        )

    select.callback = on_pick  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    if fresh:
        await interaction.response.send_message(
            content=strings["slot_choose"], view=view, ephemeral=True
        )
    else:
        await interaction.response.edit_message(content=strings["slot_choose"], view=view)


def _action_callback(action: str) -> Any:
    """Build one in-session ephemeral button callback."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    async def on_click(interaction: discord.Interaction) -> None:
        await _run_action(interaction, action, _wiring())

    return on_click


def _failed(target: Any, strings: dict[str, str], exc: Exception) -> None:
    """Edit the ephemeral bubble with the localized failure."""
    message = str(getattr(exc, "message", "") or exc)
    content = strings["failed"].format(message=message)
    try:
        target.response.edit_message(content=content, view=None)
    except Exception:
        logger.info("VOLIERE: failure edit skipped", exc_info=True)


async def _require_lord(interaction: discord.Interaction, strings: dict[str, str], wiring: Any) -> tuple[Any, Any]:
    """Resolve the acting lord + kingdom or answer the refusal."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
    if lord is None or not lord.kingdom_id or lord.left:
        await interaction.response.send_message(strings["not_a_lord"], ephemeral=True)
        return (None, None)
    kingdom = next(
        (k for k in await wiring.kingdoms_service.kingdoms() if k.id == lord.kingdom_id),
        None,
    )
    return (lord, kingdom)


async def _record_action(wiring: Any, lord: Any, action: str, **params: str) -> None:
    """Chronicle one CLICKED volière action on the acting lord (Seigneurie V2).

    Panel-level actions (the modals, the selects, the draws) never pass
    through a service that could record them — the panel knows the
    acting lord through ``_require_lord`` and records here, best-effort.
    """
    try:
        store = wiring.kingdoms_service._store
        await chronicle.record(
            lord,
            chronicle.KIND_VOLIERE_ACTION,
            now=_now(),
            store=store,
            action=action,
            **params,
        )
    except Exception:
        logger.info("VOLIERE: chronicle record skipped", exc_info=True)


async def _refresh_realm_safe(guild: Any, kingdom_id: str) -> None:
    """Best-effort realm view refresh after a kingdom mutation."""
    try:
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _refresh_realm_views_for_kingdom_safe,
        )

        await _refresh_realm_views_for_kingdom_safe(guild, kingdom_id)
    except Exception:
        logger.info("VOLIERE: realm refresh skipped", exc_info=True)


async def _draw_action(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    action: str,
    now: Any,
    lord: Any = None,
) -> None:
    """Run one draw-style action (civ, territory, lâcher) and edit the bubble."""
    voliere = wiring.voliere_service
    try:
        if action == "pigeon_civ":
            civ = await voliere.pick_civilization(str(kingdom.id), action, now=now)
            note = strings["civ_drawn"].format(bird=BIRD_LABEL[action], civ=civ)
            if lord is not None:
                await _record_action(wiring, lord, action, civ=civ)
        elif action == "faucon_territory":
            map_key = await voliere.pick_territory(str(kingdom.id), now=now)
            note = strings["territory_drawn"].format(map=map_key)
            if lord is not None:
                await _record_action(wiring, lord, action, map=map_key)
        else:
            await voliere.release_faucon(str(kingdom.id), now=now)
            note = strings["release_done"]
            if lord is not None:
                await _record_action(wiring, lord, action)
    except Exception as exc:
        _failed(interaction, strings, exc)
        return
    await interaction.response.edit_message(content=note, view=None)
    if action in ("corbeau_civ", "pigeon_civ"):
        # v2 anti-stale rule: a drawn civ lands in the Alliances salon
        # too (the draft-starter message edits itself in place).
        try:
            from kingdoms.mods.kingdoms.kingdom_realms import announce_draft_starter

            guild = interaction.guild
            if guild is not None:
                await announce_draft_starter(
                    guild, kingdom, wiring.kingdoms_service
                )
        except Exception:
            logger.info("VOLIERE: alliances refresh skipped", exc_info=True)
    await _refresh_realm_safe(interaction.guild, str(kingdom.id))


async def _run_action(
    interaction: discord.Interaction, action: str, wiring: Any
) -> None:
    """Execute one volière action (simple, select or modal flows)."""
    strings = _strings(_locale(interaction))
    if wiring.voliere_service is None or wiring.kingdoms_service is None:
        await interaction.response.send_message(strings["no_service"], ephemeral=True)
        return
    lord, kingdom = await _require_lord(interaction, strings, wiring)
    if lord is None or kingdom is None:
        return
    logger.info("kingdoms volière: %s runs %s (admin trace)", lord.id, action)
    now = _now()
    if action == "corbeau_civ":
        # corbeau-v3 (Drasah 15/10): the Corbeau no longer draws a
        # civilization — it STEALS ⚙️ from a target kingdom's bank.
        await _theft_flow(interaction, wiring, strings, kingdom, lord)
        return
    if action in ("pigeon_civ", "faucon_territory", "faucon_release"):
        await _draw_action(interaction, wiring, strings, kingdom, action, now, lord)
        return
    flows: dict[str, Any] = {
        "corbeau_verify": _verify_flow,
        "corbeau_track": _track_flow,
        "faucon_sabotage": _sabotage_flow,
    }
    flow = flows.get(action)
    if flow is not None:
        await flow(interaction, wiring, strings, kingdom, lord)
        return
    if action == "pigeon_question" and await _gate_bird(
        interaction, wiring, strings, kingdom, "pigeon_question"
    ):
        await _question_modal(interaction, wiring, strings, kingdom, lord)
        return
    if action == "pigeon_dm" and await _gate_bird(
        interaction, wiring, strings, kingdom, "pigeon_dm"
    ):
        await _dm_flow(interaction, wiring, strings, kingdom, lord)


async def _gate_bird(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    action: str,
) -> bool:
    """Peek the bird gates before a modal (no spend — the spend is on submit)."""
    bird = action.split("_", 1)[0]
    state = await _voliere_state(wiring, str(kingdom.id))
    if state is None:
        await interaction.response.send_message(strings["no_service"], ephemeral=True)
        return False
    if bird not in (state.get("chosen") or []):
        await interaction.response.edit_message(
            content=strings["bird_not_owned"].format(bird=BIRD_NAME.get(bird, bird)),
            view=None,
        )
        return False
    ready_at = int((state.get("cooldowns") or {}).get(bird, 0) or 0)
    if ready_at > int(_now().timestamp()):
        await interaction.response.edit_message(
            content=strings["bird_flying"].format(
                bird=BIRD_NAME.get(bird, bird), ts=ready_at
            ),
            view=None,
        )
        return False
    return True


# ----------------------------------------------------------------------
# The selection / modal flows
# ----------------------------------------------------------------------


async def _verify_flow(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    lord: Any = None,
) -> None:
    """Corbeau verify — select one ongoing attack against the kingdom."""
    voliere = wiring.voliere_service
    attacks = [
        a
        for a in await wiring.attacks_service.attacks()
        # planification-v1: a SCHEDULED attack is not declared yet —
        # the Corbeau cannot verify its civilizations before the
        # execution (there are none to see).
        if a.defender_kingdom_id == kingdom.id
        and not a.is_over
        and str(getattr(a, "state", "")) != "scheduled"
    ]
    if not attacks:
        await interaction.response.edit_message(
            content=strings["verify_no_target"], view=None
        )
        return
    options = [
        discord.SelectOption(
            label=str(a.map_key)[:100],
            value=str(a.id)[:100],
            description=str(a.id)[:100],
        )
        for a in attacks[:25]
    ]
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:voliere:verify:choose",
        placeholder=strings["verify_choose"][:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = [str(v) for v in (getattr(target, "data", None) or {}).get("values", [])]
        if not values:
            return
        try:
            inspection = await voliere.inspect_attack(
                str(kingdom.id), values[0], now=_now()
            )
        except Exception as exc:
            _failed(target, strings, exc)
            return
        attack = next((a for a in attacks if a.id == values[0]), None)
        map_key = str(attack.map_key) if attack is not None else str(inspection["map_key"])
        civs = ", ".join(inspection["attacker_civs"]) or strings["unknown"]
        await target.response.edit_message(
            content=strings["verify_result"].format(
                map=map_key,
                name=str(inspection["attacker_name"]),
                civs=civs,
            ),
            view=None,
        )
        await _record_action(
            wiring, lord, "corbeau_verify", map=map_key, target=str(inspection["attacker_name"])
        )

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    await interaction.response.edit_message(
        content=strings["verify_choose"], view=view
    )


async def _track_flow(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    lord: Any = None,
) -> None:
    """Corbeau track — select one lord of another player kingdom."""
    voliere = wiring.voliere_service
    lords = [
        lord
        for lord in await wiring.kingdoms_service.lords()
        if not lord.left
        and lord.kingdom_id is not None
        and lord.kingdom_id != kingdom.id
        and not getattr(
            next(
                (k for k in await wiring.kingdoms_service.kingdoms() if k.id == lord.kingdom_id),
                None,
            ),
            "is_gaia",
            False,
        )
    ]
    if not lords:
        await interaction.response.edit_message(
            content=strings["track_no_target"], view=None
        )
        return
    options = [
        discord.SelectOption(
            label=str(lord.display_name)[:100], value=str(lord.id)[:100]
        )
        for lord in lords[:25]
    ]
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:voliere:track:choose",
        placeholder=strings["track_choose"][:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = [str(v) for v in (getattr(target, "data", None) or {}).get("values", [])]
        if not values:
            return
        try:
            await voliere.track_lord(str(kingdom.id), values[0], now=_now())
        except Exception as exc:
            _failed(target, strings, exc)
            return
        target_name = next(
            (str(item.display_name) for item in lords if item.id == values[0]),
            values[0],
        )
        await target.response.edit_message(
            content=strings["track_done"].format(name=target_name), view=None
        )
        await _record_action(wiring, lord, "corbeau_track", target=target_name)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    await interaction.response.edit_message(
        content=strings["track_choose"], view=view
    )


async def _sabotage_flow(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    lord: Any = None,
) -> None:
    """Faucon sabotage — select the attack, then the adversary civilization."""
    voliere = wiring.voliere_service
    attacks = [
        a
        for a in await wiring.attacks_service.attacks()
        if not a.is_over
        # planification-v1: a SCHEDULED attack has no draft yet.
        and str(getattr(a, "state", "")) != "scheduled"
        and kingdom.id in (a.attacker_kingdom_id, a.defender_kingdom_id)
        and bool((a.pickban or {}).get("sent"))
        and not bool((a.pickban or {}).get("resolved_pickban"))
    ]
    if not attacks:
        await interaction.response.edit_message(
            content=strings["sabotage_no_target"], view=None
        )
        return
    options = [
        discord.SelectOption(
            label=str(a.map_key)[:100],
            value=str(a.id)[:100],
            description=str(a.id)[:100],
        )
        for a in attacks[:25]
    ]
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:voliere:sabotage:choose",
        placeholder=strings["sabotage_choose"][:100],
        options=options,
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = [str(v) for v in (getattr(target, "data", None) or {}).get("values", [])]
        if not values:
            return
        attack = next((a for a in attacks if a.id == values[0]), None)
        if attack is None:
            return
        adversary_id = (
            attack.defender_kingdom_id
            if attack.attacker_kingdom_id == kingdom.id
            else attack.attacker_kingdom_id
        )
        adversary = next(
            (k for k in await wiring.kingdoms_service.kingdoms() if k.id == adversary_id),
            None,
        )
        civs = [str(civ) for civ in (getattr(adversary, "civilizations", None) or [])]
        if not civs:
            await target.response.edit_message(
                content=strings["sabotage_no_civ"], view=None
            )
            return
        civ_select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id="kingdoms:voliere:sabotage:civ",
            placeholder=strings["sabotage_civ_choose"][:100],
            options=[
                discord.SelectOption(label=civ[:100], value=civ[:100]) for civ in civs[:25]
            ],
        )

        async def on_civ(civ_target: discord.Interaction[Any]) -> None:
            chosen = [
                str(v) for v in (getattr(civ_target, "data", None) or {}).get("values", [])
            ]
            if not chosen:
                return
            try:
                await voliere.faucon_sabotage(
                    str(kingdom.id), str(attack.id), chosen[0], now=_now()
                )
            except Exception as exc:
                _failed(civ_target, strings, exc)
                return
            await civ_target.response.edit_message(
                content=strings["sabotage_done"].format(
                    civ=chosen[0], map=str(attack.map_key)
                ),
                view=None,
            )
            await _record_action(
                wiring, lord, "faucon_sabotage", civ=chosen[0], map=str(attack.map_key)
            )

        civ_select.callback = on_civ  # type: ignore[method-assign, assignment]
        civ_view = discord.ui.View(timeout=600)
        civ_view.add_item(civ_select)
        await target.response.edit_message(
            content=strings["sabotage_civ_choose"], view=civ_view
        )

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    await interaction.response.edit_message(
        content=strings["sabotage_choose"], view=view
    )


async def _question_modal(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    lord: Any,
) -> None:
    """Pigeon question — one anonymous modal, posted in the Question salon."""
    question_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
        label=strings["question_input"][:45],
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    modal = discord.ui.Modal(title=strings["question_title"][:45])
    modal.add_item(question_input)

    async def on_submit(target: discord.Interaction[Any]) -> None:
        try:
            # v2: the Pigeon flies for the whole question cooldown.
            await wiring.voliere_service.spend_action(
                str(kingdom.id), "pigeon_question", now=_now()
            )
        except Exception as exc:
            _failed(target, strings, exc)
            return
        guild = target.guild
        channel = next(
            (c for c in getattr(guild, "channels", []) if _slug(getattr(c, "name", "")) == "question"),
            None,
        )
        if channel is None:
            await target.response.send_message(
                strings["question_no_channel"], ephemeral=True
            )
            return
        logger.info(
            "kingdoms volière: anonymous question by %s (admin trace)", lord.id
        )
        await _record_action(wiring, lord, "pigeon_question")
        try:
            await channel.create_thread(
                name=str(getattr(question_input, "value", "") or "")[:90],
                content=str(getattr(question_input, "value", "") or ""),
            )
        except Exception:
            try:
                await channel.send(str(getattr(question_input, "value", "") or ""))
            except Exception:
                logger.warning("VOLIERE: anonymous question post failed", exc_info=True)
                await target.response.send_message(
                    strings["question_no_channel"], ephemeral=True
                )
                return
        await target.response.send_message(strings["question_sent"], ephemeral=True)

    modal.on_submit = on_submit  # type: ignore[method-assign]
    await interaction.response.send_modal(modal)


async def _dm_kingdom_lords(
    wiring: Any, interaction: discord.Interaction, kingdom_id: str, text: str
) -> int:
    """Send one anonymous DM to every active lord of the kingdom."""
    bot = getattr(interaction, "client", None)
    sent = 0
    for recipient in await wiring.kingdoms_service.lords():
        if recipient.left or recipient.kingdom_id != kingdom_id:
            continue
        try:
            user = getattr(bot, "get_user", lambda _id: None)(recipient.id)
            if user is None and bot is not None:
                user = await bot.fetch_user(int(recipient.id))
            if user is not None:
                await user.send(text)
                sent += 1
        except Exception:
            logger.info("VOLIERE: DM failed (%s)", recipient.id, exc_info=True)
    return sent


async def _dm_flow(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    lord: Any,
) -> None:
    """Pigeon MP — select the kingdom, then one anonymous modal per lord."""
    kingdoms = [
        k
        for k in await wiring.kingdoms_service.kingdoms()
        if not getattr(k, "is_gaia", False) and k.id != kingdom.id
    ]
    if not kingdoms:
        await interaction.response.edit_message(
            content=strings["dm_no_target"], view=None
        )
        return
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:voliere:dm:choose",
        placeholder=strings["dm_choose"][:100],
        options=[
            discord.SelectOption(label=str(k.name)[:100], value=str(k.id)[:100])
            for k in kingdoms[:25]
        ],
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = [str(v) for v in (getattr(target, "data", None) or {}).get("values", [])]
        if not values:
            return
        target_kingdom = next((k for k in kingdoms if k.id == values[0]), None)
        if target_kingdom is None:
            return
        dm_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=strings["dm_input"][:45],
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        modal = discord.ui.Modal(
            title=strings["dm_title"].format(kingdom=str(target_kingdom.name))[:45]
        )
        modal.add_item(dm_input)

        async def on_submit(submit_target: discord.Interaction[Any]) -> None:
            text = str(getattr(dm_input, "value", "") or "")
            try:
                # v2: the Pigeon flies for the whole MP cooldown.
                await wiring.voliere_service.spend_action(
                    str(kingdom.id), "pigeon_dm", now=_now()
                )
            except Exception as exc:
                _failed(submit_target, strings, exc)
                return
            logger.info(
                "kingdoms volière: anonymous MP by %s to %s (admin trace)",
                lord.id,
                target_kingdom.id,
            )
            await _record_action(
                wiring, lord, "pigeon_dm", kingdom=str(target_kingdom.name)
            )
            sent = await _dm_kingdom_lords(
                wiring, submit_target, target_kingdom.id, text
            )
            if sent == 0:
                await submit_target.response.send_message(
                    strings["dm_failed"], ephemeral=True
                )
                return
            await submit_target.response.send_message(
                strings["dm_sent"].format(kingdom=str(target_kingdom.name)),
                ephemeral=True,
            )

        modal.on_submit = on_submit  # type: ignore[method-assign]
        await target.response.send_modal(modal)

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    await interaction.response.edit_message(content=strings["dm_choose"], view=view)


# ----------------------------------------------------------------------
# The corbeau theft flow + the restart-proof alarm buttons (corbeau-v3)
# ----------------------------------------------------------------------


async def _theft_flow(
    interaction: discord.Interaction,
    wiring: Any,
    strings: dict[str, str],
    kingdom: Any,
    lord: Any,
) -> None:
    """Corbeau theft — select the target kingdom, the Corbeau flies 1h (v3).

    Nothing is stolen at the click: the execution (alarm + 5-minute
    contest window on the robbed side, « Continuer ? » prompt on the
    thief side) happens ``corbeau_theft_delay_hours`` later, in the
    events loop.
    """
    voliere = wiring.voliere_service
    kingdoms = [
        k
        for k in await wiring.kingdoms_service.kingdoms()
        if not getattr(k, "is_gaia", False) and k.id != kingdom.id
    ]
    if not kingdoms:
        await interaction.response.edit_message(
            content=strings["theft_no_target"], view=None
        )
        return
    select: discord.ui.Select[Any] = discord.ui.Select(
        custom_id="kingdoms:voliere:theft:choose",
        placeholder=strings["theft_choose"][:100],
        options=[
            discord.SelectOption(
                label=str(k.name)[:100],
                value=str(k.id)[:100],
                description=f"⚙️ {int(getattr(k, 'tech_points_bank', 0) or 0)}"[:100],
            )
            for k in kingdoms[:25]
        ],
    )

    async def on_choose(target: discord.Interaction[Any]) -> None:
        values = [str(v) for v in (getattr(target, "data", None) or {}).get("values", [])]
        if not values:
            return
        target_kingdom = next((k for k in kingdoms if k.id == values[0]), None)
        if target_kingdom is None:
            return
        try:
            theft = await voliere.send_corbeau_theft(
                str(kingdom.id), values[0], str(lord.id), now=_now()
            )
        except Exception as exc:
            _failed(target, strings, exc)
            return
        await target.response.edit_message(
            content=strings["theft_sent"].format(
                kingdom=str(target_kingdom.name), ts=int(theft["execute_at"])
            ),
            view=None,
        )

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    await interaction.response.edit_message(
        content=strings["theft_choose"], view=view
    )


def theft_alarm_view(locale: str) -> discord.ui.View:
    """Build the RESTART-PROOF alarm button view (one locale, no closure).

    Used by the events loop: the alarm message sent to the robbed
    kingdom's Le-Royaume salon carries the persistent 🛡️ Contester
    button — a restart never loses the contest window.
    """
    strings = _strings(locale)
    view = discord.ui.View(timeout=None)
    view.add_item(
        KingdomTheftButton("contest", "", strings["theft_contest_button"])
    )
    return view


def theft_prompt_view(locale: str, theft_id: str) -> discord.ui.View:
    """Build the thief's persistent « Continuer ? » prompt view."""
    strings = _strings(locale)
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomTheftButton("continue", theft_id, strings["theft_continue_button"]))
    view.add_item(KingdomTheftButton("stop", theft_id, strings["theft_stop_button"]))
    return view


class KingdomTheftButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:corbeau:theft:(?P<action>contest|continue|stop):(?P<theft_id>[^:]*)",
):
    """The restart-proof theft button (corbeau-v3, Drasah 15/10).

    ``contest`` lives on the ROBBED side's alarm message; ``continue``
    and ``stop`` live on the thief's prompt message. The theft id rides
    the custom id for the thief-side buttons; the contest side finds
    the theft by target (theft_id stays in the custom id too, both are
    checked server-side).
    """

    def __init__(self, action: str, theft_id: str, label: str) -> None:
        style = (
            discord.ButtonStyle.danger
            if action == "contest"
            else (
                discord.ButtonStyle.primary
                if action == "continue"
                else discord.ButtonStyle.secondary
            )
        )
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:corbeau:theft:{action}:{theft_id}",
                style=style,
            )
        )
        self.action = action
        self.theft_id = theft_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomTheftButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        action = str(match.group("action"))
        strings = _strings(_locale(interaction))
        label = {
            "contest": strings["theft_contest_button"],
            "continue": strings["theft_continue_button"],
            "stop": strings["theft_stop_button"],
        }[action]
        return cls(action, str(match.group("theft_id")), label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the contest / continue / stop, then edit the bubble."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        voliere = wiring.voliere_service
        if voliere is None or wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        lord, kingdom = await _require_lord(interaction, strings, wiring)
        if lord is None or kingdom is None:
            return
        try:
            if self.action == "contest":
                theft_id = self.theft_id
                if not theft_id:
                    # alarm button built without the id: find the theft
                    # that targets THIS kingdom (the one to contest).
                    theft_id = next(
                        (
                            str(k.corbeau_theft.get("id"))
                            for k in await wiring.kingdoms_service.kingdoms()
                            if k.corbeau_theft is not None
                            and str(k.corbeau_theft.get("target_kingdom_id"))
                            == str(kingdom.id)
                            and str(k.corbeau_theft.get("state"))
                            in ("awaiting_continue", "continuing")
                        ),
                        "",
                    )
                if not theft_id:
                    raise VoliereTheftLookupError(strings["theft_not_yours"])
                result = await voliere.contest_theft(
                    theft_id, str(kingdom.id), now=_now()
                )
                try:
                    await chronicle.record(
                        lord,
                        chronicle.KIND_CORBEAU_CONTESTED,
                        now=_now(),
                        store=wiring.kingdoms_service._store,
                        thief=str(result.get("thief_kingdom_name", "")),
                    )
                except Exception:
                    logger.info("VOLIERE: contest chronicle skipped", exc_info=True)
                content = strings["theft_contested"].format(
                    points=int(result.get("refunded", 0) or 0)
                )
            else:
                result = await voliere.continue_theft(
                    self.theft_id,
                    str(kingdom.id),
                    self.action == "continue",
                    now=_now(),
                )
                if self.action == "continue":
                    content = strings["theft_continuing"].format(
                        ts=int(result.get("contest_until", 0) or 0),
                        points=int(result.get("stolen", 0) or 0),
                    )
                else:
                    content = strings["theft_stopped"].format(
                        points=int(result.get("stolen", 0) or 0)
                    )
        except Exception as exc:
            _failed(interaction, strings, exc)
            return
        await interaction.response.edit_message(content=content, view=None)


class VoliereTheftLookupError(Exception):
    """Raised when no active theft involves the clicking kingdom."""


# ----------------------------------------------------------------------
# The persistent Le-Royaume button
# ----------------------------------------------------------------------


class KingdomVoliereButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:voliere:open",
):
    """The restart-proof « Développer la volière » button of the realm view."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:voliere:open",
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
    ) -> KingdomVoliereButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["voliere_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral Volière view (any lord, from the Âge Féodal)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.voliere_service is None or wiring.kingdoms_service is None:
            await interaction.response.send_message(strings["no_service"], ephemeral=True)
            return
        lord, kingdom = await _require_lord(interaction, strings, wiring)
        if lord is None or kingdom is None:
            return
        state = await _voliere_state(wiring, str(kingdom.id))
        if state is not None and int(state.get("open") or 0) > 0 and state.get("available"):
            # v2: an empty slot asks for its bird BEFORE anything else.
            await _slot_flow(interaction, wiring, fresh=True)
            return
        content = await _voliere_content(wiring, kingdom, strings, state)
        await interaction.response.send_message(
            content=content, view=_voliere_view(strings, state), ephemeral=True
        )


def voliere_button(strings: dict[str, str]) -> KingdomVoliereButton:
    """Build the realm-view volière button (visible from the Feudal Age)."""
    return KingdomVoliereButton(strings["voliere_button"])
