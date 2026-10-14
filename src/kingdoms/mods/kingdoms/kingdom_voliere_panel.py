"""Kingdoms mod — the 🪶 Volière panel (voliere-v1, Drasah 14/10/2026).

The volière opens from the Le-Royaume view (« Développer la volière »),
is free from the Âge Féodal and shared by every lord of the kingdom:
three birds (Corbeau, Faucon, Pigeon), per-action cooldowns carried by
the kingdom itself.

Every answer is a single ephemeral bubble (the volière is discreet by
nature) with the standard market TTL. The anonymity is player-facing
only — every action logs the acting lord for the admin (anti-abus).

The Pigeon's traité secret is PARKED until Drasah specifies the treaty
types (decision 14/10/2026).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import discord

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
        "act_corbeau_civ": "🐦‍⬛ Draw a civilization",
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
        "verify_result": "🐦‍⬛ The real attacker of **{map}** is **{name}**.",
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
        "act_corbeau_civ": "🐦‍⬛ Piocher une civilisation",
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
        "verify_result": "🐦‍⬛ Le véritable attaquant de **{map}** est **{name}**.",
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


def _strings(locale: str) -> dict[str, str]:
    """Return the module strings for one locale (fr by default)."""
    return _STRINGS["fr" if str(locale).lower().startswith("fr") else "en"]


def _locale(interaction: discord.Interaction) -> str:
    """Extract the interaction locale (en fallback)."""
    value = str(getattr(interaction, "locale", "") or "")
    return value if value else "en"


def _now() -> datetime:
    """Return the wall clock used by the panel (hookable in tests)."""
    return datetime.now(tz=UTC)


async def _cooldown_line(
    wiring: Any, kingdom_id: str, action: str, strings: dict[str, str]
) -> str:
    """One cooldown status line — « prêt dans Xh » (Oracle's suggestion)."""
    voliere = wiring.voliere_service
    label = strings[f"act_{action}"]
    try:
        ready_at = int(await voliere.cooldown_ready_at(kingdom_id, action))
    except Exception:
        ready_at = 0
    now_ts = int(_now().timestamp())
    if ready_at > now_ts:
        hours = max(1, (ready_at - now_ts + 3599) // 3600)
        return f"- {label} — {strings['ready_in'].format(hours=hours)}"
    return f"- {label} — {strings['ready']}"


async def _voliere_content(
    wiring: Any, kingdom: Any, strings: dict[str, str]
) -> str:
    """Build the Volière sommaire with every cooldown status."""
    kingdom_id = str(kingdom.id)
    lines = [
        strings["voliere_title"].format(kingdom=str(getattr(kingdom, "name", ""))),
        strings["voliere_line"],
        "",
        strings["voliere_corbeau"],
    ]
    for action in ("corbeau_civ", "corbeau_verify", "corbeau_track"):
        lines.append(await _cooldown_line(wiring, kingdom_id, action, strings))
    lines.append(strings["voliere_faucon"])
    for action in ("faucon_territory", "faucon_sabotage", "faucon_release"):
        lines.append(await _cooldown_line(wiring, kingdom_id, action, strings))
    lines.append(strings["voliere_pigeon"])
    for action in ("pigeon_civ", "pigeon_question", "pigeon_dm"):
        lines.append(await _cooldown_line(wiring, kingdom_id, action, strings))
    return "\n".join(lines)


def _voliere_view(strings: dict[str, str]) -> discord.ui.View:
    """Build the ephemeral in-session action view of the volière."""
    view = discord.ui.View(timeout=600)
    for action, style_name in VOLIERE_ACTIONS:
        style = getattr(discord.ButtonStyle, style_name, discord.ButtonStyle.secondary)
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=strings[f"act_{action}"][:80],
            custom_id=f"kingdoms:voliere:act:{action}",
            style=style,
        )
        button.callback = _action_callback(action)  # type: ignore[method-assign]
        view.add_item(button)
    return view


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
) -> None:
    """Run one draw-style action (civ, territory, lâcher) and edit the bubble."""
    voliere = wiring.voliere_service
    try:
        if action in ("corbeau_civ", "pigeon_civ"):
            civ = await voliere.pick_civilization(str(kingdom.id), action, now=now)
            note = strings["civ_drawn"].format(bird=BIRD_LABEL[action], civ=civ)
        elif action == "faucon_territory":
            map_key = await voliere.pick_territory(str(kingdom.id), now=now)
            note = strings["territory_drawn"].format(map=map_key)
        else:
            await voliere.release_faucon(str(kingdom.id), now=now)
            note = strings["release_done"]
    except Exception as exc:
        _failed(interaction, strings, exc)
        return
    await interaction.response.edit_message(content=note, view=None)
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
    if action in ("corbeau_civ", "pigeon_civ", "faucon_territory", "faucon_release"):
        await _draw_action(interaction, wiring, strings, kingdom, action, now)
        return
    if action == "corbeau_verify":
        await _verify_flow(interaction, wiring, strings, kingdom)
        return
    if action == "corbeau_track":
        await _track_flow(interaction, wiring, strings, kingdom)
        return
    if action == "faucon_sabotage":
        await _sabotage_flow(interaction, wiring, strings, kingdom)
        return
    if action == "pigeon_question":
        await _question_modal(interaction, wiring, strings, lord)
        return
    if action == "pigeon_dm":
        await _dm_flow(interaction, wiring, strings, kingdom, lord)
        return


# ----------------------------------------------------------------------
# The selection / modal flows
# ----------------------------------------------------------------------


async def _verify_flow(
    interaction: discord.Interaction, wiring: Any, strings: dict[str, str], kingdom: Any
) -> None:
    """Corbeau verify — select one ongoing attack against the kingdom."""
    voliere = wiring.voliere_service
    attacks = [
        a
        for a in await wiring.attacks_service.attacks()
        if a.defender_kingdom_id == kingdom.id and not a.is_over
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
            name = await voliere.verify_attack(str(kingdom.id), values[0], now=_now())
        except Exception as exc:
            _failed(target, strings, exc)
            return
        attack = next((a for a in attacks if a.id == values[0]), None)
        map_key = str(getattr(attack, "map_key", values[0]))
        await target.response.edit_message(
            content=strings["verify_result"].format(map=map_key, name=name), view=None
        )

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    await interaction.response.edit_message(
        content=strings["verify_choose"], view=view
    )


async def _track_flow(
    interaction: discord.Interaction, wiring: Any, strings: dict[str, str], kingdom: Any
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

    select.callback = on_choose  # type: ignore[method-assign, assignment]
    view = discord.ui.View(timeout=600)
    view.add_item(select)
    await interaction.response.edit_message(
        content=strings["track_choose"], view=view
    )


async def _sabotage_flow(
    interaction: discord.Interaction, wiring: Any, strings: dict[str, str], kingdom: Any
) -> None:
    """Faucon sabotage — select the attack, then the adversary civilization."""
    voliere = wiring.voliere_service
    attacks = [
        a
        for a in await wiring.attacks_service.attacks()
        if not a.is_over
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
    interaction: discord.Interaction, wiring: Any, strings: dict[str, str], lord: Any
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
            logger.info(
                "kingdoms volière: anonymous MP by %s to %s (admin trace)",
                lord.id,
                target_kingdom.id,
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
        content = await _voliere_content(wiring, kingdom, strings)
        await interaction.response.send_message(
            content=content, view=_voliere_view(strings), ephemeral=True
        )


def voliere_button(strings: dict[str, str]) -> KingdomVoliereButton:
    """Build the realm-view volière button (visible from the Feudal Age)."""
    return KingdomVoliereButton(strings["voliere_button"])
