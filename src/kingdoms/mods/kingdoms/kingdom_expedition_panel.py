"""Kingdoms mod — the 🧭 Expédition salon (exploration-expedition-v1,
Drasah 30/10/2026).

The Explorateur token opens a JOURNEY now: the 🧭 button (Chambre des
comptes « Utiliser » + Le-Royaume view) creates the kingdom's 🧭
exploration salon. A lord enrolls as CHEF (prerequisite: one AVAILABLE
defence point 🛡️ — never consumed, going exploring is a real risk),
every other lord embarks himself as CREW (each −4h of travel, the chef
never cuts it, plancher 24h). ⛵ Lâcher les amarres shows ONLY with a
chef on board and is the single step that consumes the token.

Anti-pollution (hygiene rules): the panel edits in place, the refusals
are 90-second ephemeral bubbles, the salon is gated like the realm
(everyone deny + members allow) and dies at the return. The Géopolitique
departure message stays deliberately VAGUE (no lord, no duration) — it
feeds the future rumour/raven system (Drasah, in reflection).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time as _time
from typing import Any

import discord

from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.expedition_panel")

EXPEDITION_SALON_NAME = "🧭 exploration"
_ERROR_NOTE_SECONDS = 90.0
"""hygiene-ephemere-v1: every refusal bubble dies after 90 seconds."""

_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "open_button": "🧭 Exploration",
        "panel_title": "# 🧭 Expédition de {kingdom}",
        "chef_line": "🧭 **Chef d'expédition :** {name}",
        "crew_line": "⚓ **Équipage :** {names}",
        "eta_line": "⏳ **Trajet au départ :** **{hours} h** (48 h − 4 h par matelot, plancher 24 h)",
        "chef_rule": "🧭 Le chef doit disposer d'un point de défense 🛡️ disponible (non consommé).",
        "crew_rule": (
            "⚓ Chaque seigneur embarque de lui-même — le jeton 🧭 Explorateur"
            " n'est consommé qu'au Lâcher les amarres."
        ),
        "chef_button": "🧭 S'enroller comme chef d'expédition",
        "crew_button": "⚓ Participer au voyage",
        "launch_button": "⛵ Lâcher les amarres",
        "sailing_title": "# 🧭 Expédition de {kingdom}",
        "sailing_line": "⛵ **En mer** — retour estimé <t:{ends}:R>.",
        "sailing_note": (
            "⛵ Les amarres sont lâchées — le jeton 🧭 Explorateur est consommé."
            " Bon voyage, l'équipage revient <t:{ends}:R>."
        ),
        "depart_announce": (
            "🧭 Le royaume {kingdom} envoie une expédition explorer de"
            " nouveaux horizons..."
        ),
        "return_announce_map": (
            "🧭 Le royaume {kingdom} revient d'un long périple et découvre"
            " le territoire **{map}** !"
        ),
        "return_announce_civ": (
            "🧭 Le royaume {kingdom} revient d'un long périple — l'horizon"
            " ne révèle aucune terre nouvelle, mais l'équipage recrute la"
            " civilisation **{civ}** (+2 ⚙️) !"
        ),
        "return_announce_tech": (
            "🧭 Le royaume {kingdom} revient d'un long périple — aucune"
            " terre nouvelle : le royaume empoche **2 ⚙️** de compensation."
        ),
        "open_note": "🧭 Le salon d'expédition est ouvert : {channel}",
        "not_a_lord": "❌ Tu n'es pas un seigneur actif d'un royaume.",
        "no_service": "❌ Le service de l'économie est indisponible.",
        "no_token": "❌ Aucun jeton Explorateur en réserve — achètes-en un au Marché.",
        "already_sailing": "⛔ Une expédition est déjà en mer.",
        "locked": "⛔ Tu es dans un état verrouillé (mariage, exploration ou FFA).",
        "no_defense": "🛡️ Il te faut un point de défense disponible pour mener l'expédition.",
        "chef_not_crew": "⛔ Le chef d'expédition n'embarque pas comme matelot.",
        "failed": "❌ {message}",
    },
    "en": {
        "open_button": "🧭 Exploration",
        "panel_title": "# 🧭 Expedition of {kingdom}",
        "chef_line": "🧭 **Expedition chief:** {name}",
        "crew_line": "⚓ **Crew:** {names}",
        "eta_line": "⏳ **Travel at departure:** **{hours} h** (48 h − 4 h per mate, floor 24 h)",
        "chef_rule": "🧭 The chief needs one AVAILABLE defence point 🛡️ (never consumed).",
        "crew_rule": (
            "⚓ Every lord embarks himself — the 🧭 Explorateur token is"
            " only consumed at Cast off."
        ),
        "chef_button": "🧭 Enroll as expedition chief",
        "crew_button": "⚓ Join the voyage",
        "launch_button": "⛵ Cast off",
        "sailing_title": "# 🧭 Expedition of {kingdom}",
        "sailing_line": "⛵ **At sea** — back <t:{ends}:R>.",
        "sailing_note": (
            "⛵ The lines are cast — the 🧭 Explorateur token is consumed."
            " Safe travels, the crew returns <t:{ends}:R>."
        ),
        "depart_announce": (
            "🧭 The kingdom {kingdom} sends an expedition to explore new"
            " horizons..."
        ),
        "return_announce_map": (
            "🧭 The kingdom {kingdom} returns from a long journey and"
            " discovers the territory **{map}**!"
        ),
        "return_announce_civ": (
            "🧭 The kingdom {kingdom} returns from a long journey — the"
            " horizon reveals no new land, but the crew recruits the"
            " civilization **{civ}** (+2 ⚙️)!"
        ),
        "return_announce_tech": (
            "🧭 The kingdom {kingdom} returns from a long journey — no new"
            " land: the kingdom pockets **2 ⚙️** of compensation."
        ),
        "open_note": "🧭 The expedition salon is open: {channel}",
        "not_a_lord": "❌ You are not an active lord of a kingdom.",
        "no_service": "❌ The economy service is unavailable.",
        "no_token": "❌ No Explorateur token in reserve — buy one at the Market.",
        "already_sailing": "⛔ An expedition is already at sea.",
        "locked": "⛔ You are in a locked state (marriage, exploration or FFA).",
        "no_defense": "🛡️ You need one available defence point to lead the expedition.",
        "chef_not_crew": "⛔ The expedition chief does not embark as a mate.",
        "failed": "❌ {message}",
    },
}


def _strings(locale: Any) -> dict[str, str]:
    """Return the panel's strings for a Discord locale."""
    lang = str(locale or "en")
    return _STRINGS.get(lang[:2], _STRINGS["en"])


def _locale(interaction: discord.Interaction) -> str:
    """Extract the interaction locale (``fr``/``en``)."""
    raw = str(getattr(interaction, "locale", "") or "en")
    return "fr" if raw.lower().startswith("fr") else "en"


async def _error_note(interaction: discord.Interaction, text: str) -> None:
    """One 90-second ephemeral refusal bubble (hygiene-ephemere-v1)."""
    try:
        await interaction.response.send_message(text, ephemeral=True)
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: refusal bubble failed", exc_info=True)
        return

    async def _delete_later() -> None:
        await asyncio.sleep(_ERROR_NOTE_SECONDS)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass  # the bubble already died on its own

    try:
        asyncio.create_task(_delete_later())
    except Exception:
        pass


def _map_error(strings: dict[str, str], exc: Exception) -> str:
    """Translate one economy refusal into the panel's own bubbles."""
    from kingdoms.mods.kingdoms.economy import EconomyError

    text = str(exc)
    if "no explorateur token" in text:
        return strings["no_token"]
    if "already at sea" in text:
        return strings["already_sailing"]
    if "locked" in text:
        return strings["locked"]
    if "defence point" in text:
        return strings["no_defense"]
    if "not embark as crew" in text or "does not embark" in text:
        return strings["chef_not_crew"]
    if isinstance(exc, EconomyError):
        return str(strings["failed"]).format(message=text)
    return str(strings["failed"]).format(message=text)


async def _require_lord(
    interaction: discord.Interaction, strings: dict[str, str], wiring: Any
) -> tuple[Any, Any]:
    """Resolve the acting lord + kingdom or answer the refusal."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _lord_of

    lord = await _lord_of(wiring.kingdoms_service, str(interaction.user.id))
    if lord is None or not lord.kingdom_id or lord.left:
        await _error_note(interaction, strings["not_a_lord"])
        return (None, None)
    kingdom = next(
        (k for k in await wiring.kingdoms_service.kingdoms() if k.id == lord.kingdom_id),
        None,
    )
    if kingdom is None:
        await _error_note(interaction, strings["not_a_lord"])
        return (None, None)
    return (lord, kingdom)


def _expedition_hours(doc: dict[str, Any]) -> int:
    """The travel time at departure (48h − 4h per mate, floor 24h)."""
    from kingdoms.mods.kingdoms.economy import (
        EXPEDITION_BASE_HOURS,
        EXPEDITION_HOURS_PER_LORD,
        EXPEDITION_MIN_HOURS,
    )

    crew = len([item for item in doc.get("crew", []) if item])
    return max(
        EXPEDITION_MIN_HOURS,
        EXPEDITION_BASE_HOURS - EXPEDITION_HOURS_PER_LORD * crew,
    )


def _panel_text(
    strings: dict[str, str], kingdom: Any, doc: dict[str, Any], lord_names: dict[str, str]
) -> str:
    """The salon's pinned panel body (boarding or sailing)."""
    title = str(strings["panel_title"]).format(kingdom=getattr(kingdom, "name", ""))
    if str(doc.get("state", "")) == "sailing":
        ends = int(doc.get("ends_at", 0) or 0)
        return "\n".join([title, "", str(strings["sailing_line"]).format(ends=ends)])
    chef_id = str(doc.get("chef_lord_id", "") or "")
    crew = [str(item) for item in doc.get("crew", []) if item]
    chef_name = lord_names.get(chef_id, "—")
    crew_names = ", ".join(f"<@{item}>" for item in crew) if crew else "—"
    lines = [
        title,
        "",
        str(strings["chef_line"]).format(name=chef_name),
        str(strings["crew_line"]).format(names=crew_names),
        str(strings["eta_line"]).format(hours=_expedition_hours(doc)),
        "",
        strings["chef_rule"],
        strings["crew_rule"],
    ]
    return "\n".join(lines)


def _panel_view(strings: dict[str, str], doc: dict[str, Any]) -> discord.ui.View:
    """The panel's persistent buttons — ⛵ only with a chef on board."""
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomExpeditionChefButton(strings))
    view.add_item(KingdomExpeditionCrewButton(strings))
    if doc.get("chef_lord_id"):
        view.add_item(KingdomExpeditionLaunchButton(strings))
    return view


async def _lord_names(wiring: Any, kingdom_id: str) -> dict[str, str]:
    """Map the kingdom's lord ids to display names."""
    names: dict[str, str] = {}
    try:
        for lord in await wiring.kingdoms_service.lords():
            if str(lord.kingdom_id or "") == str(kingdom_id) and not lord.left:
                names[str(lord.id)] = str(getattr(lord, "display_name", lord.id))
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: lord names read failed", exc_info=True)
    return names


async def refresh_expedition_panel_safe(guild: Any, wiring: Any, kingdom_id: str) -> None:
    """Re-render the pinned panel in place (zero bubble, anti-pollution)."""
    economy = wiring.economy_service
    if economy is None or wiring.kingdoms_service is None:
        return
    try:
        doc = await economy.expedition(kingdom_id)
        if not doc or not doc.get("panel_message_id") or not doc.get("salon_id"):
            return
        channel = getattr(guild, "get_channel", lambda _id: None)(int(doc["salon_id"]))
        if channel is None:
            return
        message = await channel.fetch_message(int(doc["panel_message_id"]))
        kingdom = next(
            (k for k in await wiring.kingdoms_service.kingdoms() if k.id == kingdom_id),
            None,
        )
        if kingdom is None:
            return
        locale = "fr"
        strings = _strings(locale)
        names = await _lord_names(wiring, kingdom_id)
        if str(doc.get("state", "")) == "sailing":
            await message.edit(
                content=_panel_text(strings, kingdom, doc, names), view=None
            )
        else:
            await message.edit(
                content=_panel_text(strings, kingdom, doc, names),
                view=_panel_view(strings, doc),
            )
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: panel refresh failed", exc_info=True)


async def open_expedition_salon_safe(
    guild: Any, wiring: Any, kingdom: Any
) -> Any | None:
    """Open (or re-open) the kingdom's 🧭 exploration salon.

    The salon lives in the kingdom's category, gated like the realm
    (everyone deny + members allow — the INVARIANT a fresh arrival
    must never miss). A leftover empty salon is purged by slug first.
    Returns the channel (None on failure — best effort, never raises).
    """
    from kingdoms.mods.kingdoms.kingdom_realms import (
        _find_realm_category,
        _realm_overwrites,
    )

    economy = wiring.economy_service
    if economy is None or wiring.kingdoms_service is None:
        return None
    kingdom_id = str(kingdom.id)
    try:
        doc = await economy.begin_expedition(kingdom_id)
    except Exception as exc:
        logger.warning(
            "KINGDOMS EXPEDITION: boarding refused for %s (%s)", kingdom_id, exc
        )
        return None
    if doc.get("salon_id"):
        channel = getattr(guild, "get_channel", lambda _id: None)(int(doc["salon_id"]))
        if channel is not None:
            await refresh_expedition_panel_safe(guild, wiring, kingdom_id)
            return channel
    category = _find_realm_category(guild, str(kingdom.name))
    if category is None:
        logger.warning(
            "KINGDOMS EXPEDITION: no realm category for %s", kingdom_id
        )
        return None
    # A leftover salon from a wiped boarding is purged first — the name
    # is fixed, at most one lives per realm.
    wanted = _slug(EXPEDITION_SALON_NAME)
    for channel in list(getattr(category, "channels", []) or []):
        if _slug(getattr(channel, "name", "")) == wanted:
            try:
                await channel.delete()
            except Exception:
                logger.warning(
                    "KINGDOMS EXPEDITION: leftover salon purge failed", exc_info=True
                )
    member_ids: list[int] = []
    for lord in await wiring.kingdoms_service.lords():
        if str(lord.kingdom_id or "") != kingdom_id or lord.left:
            continue
        try:
            member_ids.append(int(lord.id))
        except (TypeError, ValueError):
            logger.warning(
                "KINGDOMS EXPEDITION: non-numeric lord id %r skipped", lord.id
            )
    overwrites = await _realm_overwrites(guild, member_ids)
    try:
        channel = await guild.create_text_channel(
            EXPEDITION_SALON_NAME,
            category=category,
            overwrites=overwrites,
        )
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: salon creation failed", exc_info=True)
        return None
    strings = _strings("fr")
    names = await _lord_names(wiring, kingdom_id)
    try:
        message = await channel.send(
            _panel_text(strings, kingdom, doc, names), view=_panel_view(strings, doc)
        )
        await economy.record_expedition_salon(
            kingdom_id, int(channel.id), int(message.id)
        )
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: panel message failed", exc_info=True)
        await economy.record_expedition_salon(kingdom_id, int(channel.id))
    logger.info("KINGDOMS EXPEDITION: salon opened for %s", kingdom_id)
    return channel


async def announce_expedition_depart_safe(guild: Any, kingdom_name: str) -> None:
    """The deliberately VAGUE Géopolitique departure (no lord, no duration)."""
    from kingdoms.mods.kingdoms.kingdom_content import announce_geopolitics

    strings = _strings("fr")
    text = str(strings["depart_announce"]).format(kingdom=kingdom_name)
    try:
        await announce_geopolitics(guild, "fr", text)
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: departure announcement failed", exc_info=True)


def expedition_return_text(
    locale: str, kingdom_name: str, kind: str, value: str
) -> str:
    """The Géopolitique return message (map, compensation civ, or points)."""
    strings = _strings(locale)
    if kind == "map":
        return str(strings["return_announce_map"]).format(
            kingdom=kingdom_name, map=value
        )
    if kind == "civ":
        return str(strings["return_announce_civ"]).format(
            kingdom=kingdom_name, civ=value
        )
    return str(strings["return_announce_tech"]).format(kingdom=kingdom_name)


async def sweep_expedition_salon_safe(guild: Any, salon_id: int) -> None:
    """Delete the expedition salon at the return (it never outlives the trip)."""
    if not salon_id:
        return
    channel = getattr(guild, "get_channel", lambda _id: None)(int(salon_id))
    if channel is None:
        return
    try:
        await channel.delete()
        logger.info("KINGDOMS EXPEDITION: salon swept (expedition over)")
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: salon deletion failed", exc_info=True)


async def open_expedition_from_market(
    interaction: discord.Interaction, wiring: Any, strings: dict[str, Any], lord: Any
) -> None:
    """The 🚀 Utiliser rewiring: the explorateur card OPENS the salon.

    The old lord-select flow is retired — the token is no longer spent
    on a lord pick: it funds a whole EXPEDITION. The window closes the
    player's previous ephemeral (fenetre-unique) and the salon link
    rides a small followup bubble.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        _close_previous_ephemeral,
        _EPHEMERAL_WINDOWS,
    )

    expe_strings = _strings(_locale(interaction))
    economy = wiring.economy_service
    if economy is None or wiring.kingdoms_service is None:
        await _error_note(interaction, expe_strings["no_service"])
        return
    kingdom = next(
        (
            k
            for k in await wiring.kingdoms_service.kingdoms()
            if k.id == str(lord.kingdom_id or "")
        ),
        None,
    )
    if kingdom is None:
        await _error_note(interaction, expe_strings["not_a_lord"])
        return
    user_id = str(interaction.user.id)
    await _close_previous_ephemeral(user_id)
    await interaction.response.defer(ephemeral=True)
    channel = await open_expedition_salon_safe(interaction.guild, wiring, kingdom)
    if channel is None:
        try:
            doc = await economy.expedition(str(kingdom.id))
            if str(doc.get("state", "")) == "sailing":
                note = expe_strings["already_sailing"]
            else:
                note = expe_strings["no_token"]
        except Exception:
            note = expe_strings["no_token"]
        try:
            await interaction.followup.send(note, ephemeral=True)
        except Exception:
            logger.warning("KINGDOMS EXPEDITION: refusal followup failed", exc_info=True)
        return
    note = str(expe_strings["open_note"]).format(channel=f"<#{int(channel.id)}>")
    try:
        message = await interaction.followup.send(note, ephemeral=True)
        _EPHEMERAL_WINDOWS[user_id] = (interaction, _time.monotonic(), message)
    except Exception:
        logger.warning("KINGDOMS EXPEDITION: salon link followup failed", exc_info=True)


class KingdomExpeditionOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:expedition:open",
):
    """The restart-proof 🧭 Exploration button (Le-Royaume view)."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:expedition:open",
                style=discord.ButtonStyle.success,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomExpeditionOpenButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["open_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open (or re-open) the kingdom's expedition salon."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.economy_service is None or wiring.kingdoms_service is None:
            await _error_note(interaction, strings["no_service"])
            return
        lord, kingdom = await _require_lord(interaction, strings, wiring)
        if lord is None or kingdom is None:
            return
        await open_expedition_from_market(interaction, wiring, strings, lord)


async def _handle_toggle(
    interaction: discord.Interaction, toggle: Any
) -> None:
    """Shared plumbing of the two boarding toggles (chef/crew).

    The refusal rides a 90-second bubble, the success edits the panel
    in place — a toggle never leaves a message behind (anti-pollution).
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    strings = _strings(_locale(interaction))
    wiring = _wiring()
    if wiring.economy_service is None or wiring.kingdoms_service is None:
        await _error_note(interaction, strings["no_service"])
        return
    lord, kingdom = await _require_lord(interaction, strings, wiring)
    if lord is None or kingdom is None:
        return
    try:
        await toggle(str(kingdom.id), str(lord.id))
    except Exception as exc:
        await _error_note(interaction, _map_error(strings, exc))
        return
    try:
        await interaction.response.defer()
    except Exception:
        pass
    await refresh_expedition_panel_safe(interaction.guild, wiring, str(kingdom.id))
    try:
        await interaction.delete_original_response()
    except Exception:
        pass


class KingdomExpeditionChefButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:expedition:chef",
):
    """« S'enroller comme chef d'expédition » — one available defence point."""

    def __init__(self, strings: dict[str, str]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["chef_button"])[:80],
                custom_id="kingdoms:expedition:chef",
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
    ) -> KingdomExpeditionChefButton:
        """Rebuild the button from the wire."""
        return cls(_strings(_locale(interaction)))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle the chef (never consumes the defence point)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
        if wiring.economy_service is None:
            await _error_note(interaction, _strings(_locale(interaction))["no_service"])
            return
        await _handle_toggle(interaction, wiring.economy_service.toggle_expedition_chef)


class KingdomExpeditionCrewButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:expedition:crew",
):
    """« Participer au voyage » — each lord clicks himself, −4h of travel."""

    def __init__(self, strings: dict[str, str]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["crew_button"])[:80],
                custom_id="kingdoms:expedition:crew",
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
    ) -> KingdomExpeditionCrewButton:
        """Rebuild the button from the wire."""
        return cls(_strings(_locale(interaction)))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Toggle one crew member (free, unlocked active lords only)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

        wiring = _wiring()
        if wiring.economy_service is None:
            await _error_note(interaction, _strings(_locale(interaction))["no_service"])
            return
        await _handle_toggle(interaction, wiring.economy_service.toggle_expedition_crew)


class KingdomExpeditionLaunchButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:expedition:launch",
):
    """« ⛵ Lâcher les amarres » — visible ONLY with a chef on board.

    The single step that consumes the Explorateur token; the locks fall
    on the chef + crew, the vague Géopolitique announcement goes out and
    the panel flips to its sailing face (no buttons left).
    """

    def __init__(self, strings: dict[str, str]) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(strings["launch_button"])[:80],
                custom_id="kingdoms:expedition:launch",
                style=discord.ButtonStyle.success,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomExpeditionLaunchButton:
        """Rebuild the button from the wire."""
        return cls(_strings(_locale(interaction)))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Consume the token, lock the crew, announce and flip the panel."""
        from kingdoms.mods.kingdoms.kingdom_persistent import (
            _refresh_realm_views_for_kingdom_safe,
            _wiring,
        )

        strings = _strings(_locale(interaction))
        wiring = _wiring()
        if wiring.economy_service is None or wiring.kingdoms_service is None:
            await _error_note(interaction, strings["no_service"])
            return
        lord, kingdom = await _require_lord(interaction, strings, wiring)
        if lord is None or kingdom is None:
            return
        try:
            doc = await wiring.economy_service.launch_expedition(str(kingdom.id))
        except Exception as exc:
            await _error_note(interaction, _map_error(strings, exc))
            return
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
            pass
        await refresh_expedition_panel_safe(interaction.guild, wiring, str(kingdom.id))
        ends = int(doc.get("ends_at", 0) or 0)
        salon = getattr(
            interaction.guild, "get_channel", lambda _id: None
        )(int(doc.get("salon_id", 0) or 0))
        if salon is not None:
            try:
                await salon.send(str(strings["sailing_note"]).format(ends=ends))
            except Exception:
                logger.warning(
                    "KINGDOMS EXPEDITION: sailing note failed", exc_info=True
                )
        await announce_expedition_depart_safe(interaction.guild, str(kingdom.name))
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
        # The realm view drops the 🧭 button (the token is gone).
        try:
            await _refresh_realm_views_for_kingdom_safe(
                interaction.guild, str(kingdom.id)
            )
        except Exception:
            logger.warning("KINGDOMS EXPEDITION: realm refresh failed", exc_info=True)
