"""Kingdoms Alliances panel — the read-only clarity buttons (alliances-lisibilite-v1, Drasah 25/10).

Tranche 1 (commit bfd719e) gave every kingdom an append-only
acquisition ledger (``KingdomModel.civilization_acquisitions`` — 🎲
starter draft, 🗺️ cadastre condition, 💍 standard marriage, 👑 royal
marriage, 📜 Chef Mercenaire) and the read-only ``alliance_projection``
of the next cycle. This tranche surfaces them: the 🤝 Alliances salon
of every realm wears a panel that reads the hand with ONE emoji per
acquisition origin, and five restart-proof EPHEMERAL buttons answer
the questions Drasah's players actually ask —

- 🔭 Prochain cycle — the projection: kept / arriving / dropping, the
  🎯 « à 1 map près » hints, the exclusivity of the married elsewhere;
- 🚫 Verrouillées — every locked civilization and WHY (its cadastre
  condition, or another kingdom's active marriage — exclusivité D74);
- 📖 Historique — the acquisition ledger: every civilization EVER
  acquired, with its origin emoji and its date (the first origin is
  the historical truth and is never rewritten);
- ⏳ Durées — how long each alliance has been held (game clock);
- 🔤 Noms — display name ↔ techtree (provider) name of each held civ.

Every button is a ``DynamicItem``: the custom_id carries the button
kind, the kingdom resolves from the salon's realm category at click
time (the salon is readable by every lord — the answer belongs to the
kingdom OF THE SALON, never of the clicker), and the services rebuild
from the live bot. The answers are ephemeral and strictly read-only:
the cycle recalculation alone decides the truth.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kingdoms.mods.kingdoms.game_clock import game_now

logger = logging.getLogger("kingdoms.kingdom_alliances_panel")

__all__ = [
    "KingdomAlliancesDurationsButton",
    "KingdomAlliancesHistoryButton",
    "KingdomAlliancesLockedButton",
    "KingdomAlliancesNamesButton",
    "KingdomAlliancesNextButton",
    "alliance_origin_emoji",
    "kingdom_of_interaction",
]

ORIGIN_EMOJI: dict[str, str] = {
    "starter": "\U0001f3b2",
    "condition": "\U0001f5fa",
    "marriage": "\U0001f48d",
    "royal": "\U0001f451",
    "mercenary": "\U0001f4dc",
}
"""The emoji of each acquisition origin (alliances-lisibilite-v1)."""

DEFAULT_ORIGIN_EMOJI = "\U0001f91d"
"""A pre-ledger hand (a season started before the patch) reads 🤝."""

_PARIS_TZ = "Europe/Paris"


def alliance_origin_emoji(origin: object) -> str:
    """Return the emoji of one acquisition origin (🤝 when unknown)."""
    return ORIGIN_EMOJI.get(str(origin or ""), DEFAULT_ORIGIN_EMOJI)


def _strings_of(locale: object) -> dict[str, str]:
    from kingdoms.mods.kingdoms.kingdom_realm_content import _strings

    return _strings(str(locale) if locale else "en")


def _catalog(wiring: Any) -> tuple[Any, ...]:
    service = getattr(wiring, "kingdoms_service", None)
    return tuple(getattr(getattr(service, "config", None), "civilizations", ()) or ())


def _civ_names(wiring: Any) -> dict[str, str]:
    return {
        str(getattr(civ, "key", civ)): str(getattr(civ, "display_name", civ))
        for civ in _catalog(wiring)
    }


def _map_names(wiring: Any) -> dict[str, str]:
    service = getattr(wiring, "kingdoms_service", None)
    maps = getattr(getattr(service, "config", None), "maps", ()) or ()
    return {
        str(getattr(item, "key", item)): str(getattr(item, "display_name", item))
        for item in maps
    }


async def kingdom_of_interaction(wiring: Any, interaction: discord.Interaction) -> Any | None:
    """Resolve the kingdom whose realm salon hosts the interaction.

    The 🤝 Alliances salon is readable by every kingdoms_lord
    (PUBLIC_REALM_SALONS): the buttons answer for the kingdom OF THE
    SALON — a foreign lord browsing Avalon's panel sees Avalon, never
    his own kingdom.
    """
    guild = getattr(interaction, "guild", None)
    channel = getattr(interaction, "channel", None)
    category = getattr(channel, "category", None)
    service = getattr(wiring, "kingdoms_service", None)
    if guild is None or category is None or service is None:
        return None
    try:
        kingdoms = await service.kingdoms()
    except Exception:
        logger.warning("ALLIANCES PANEL: kingdom list read failed", exc_info=True)
        return None
    from kingdoms.mods.kingdoms.kingdom_realms import _find_realm_category

    category_id = getattr(category, "id", None)
    for kingdom in kingdoms:
        if getattr(kingdom, "is_gaia", False):
            continue
        try:
            found = _find_realm_category(guild, str(getattr(kingdom, "name", "")))
        except Exception:
            continue
        if found is not None and getattr(found, "id", None) == category_id:
            return kingdom
    return None


async def _projection(wiring: Any, kingdom: Any) -> dict[str, Any] | None:
    """The read-only projection of the next cycle (None = unavailable)."""
    diplomacy = getattr(wiring, "diplomacy_service", None)
    if diplomacy is None or not hasattr(diplomacy, "alliance_projection"):
        return None
    try:
        return await diplomacy.alliance_projection(str(getattr(kingdom, "id", "")))
    except Exception:
        logger.warning("ALLIANCES PANEL: projection read failed", exc_info=True)
        return None


def _label(names: dict[str, str], civs: Any) -> str:
    values = [str(civ) for civ in (civs or [])]
    if not values:
        return "—"
    return ", ".join(names.get(civ, civ) for civ in values)


def _format_age(seconds: int) -> str:
    """Render an age as Drasah reads it: « 3j 4h », « 2h 15m », « 40m »."""
    value = max(0, int(seconds))
    days, rest = divmod(value, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}j")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) or "0m"


async def next_cycle_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """🔭 The next-cycle preview: kept / arriving / dropping + hints."""
    projection = await _projection(wiring, kingdom)
    if projection is None:
        return strings["alliances_no_data"]
    names = _civ_names(wiring)
    near = ", ".join(
        f"{names.get(str(entry.get('civ')), str(entry.get('civ')))} ({entry.get('hint')})"
        for entry in projection.get("near_unlocks", [])
        if isinstance(entry, dict)
    )
    return "\n".join(
        [
            strings["alliances_next_title"].format(getattr(kingdom, "name", "")),
            strings["alliances_next_kept"].format(_label(names, projection.get("kept", []))),
            strings["alliances_next_arriving"].format(_label(names, projection.get("arriving", []))),
            strings["alliances_next_dropping"].format(_label(names, projection.get("dropping", []))),
            strings["alliances_next_near"].format(near or "—"),
            strings["alliances_next_elsewhere"].format(
                _label(names, projection.get("married_elsewhere", []))
            ),
        ]
    )


async def locked_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """🚫 Every locked civilization and WHY (condition or exclusivity)."""
    projection = await _projection(wiring, kingdom)
    if projection is None:
        return strings["alliances_no_data"]
    names = _civ_names(wiring)
    map_names = _map_names(wiring)
    unlocked = {str(civ) for civ in projection.get("kept", [])}
    unlocked.update(str(civ) for civ in projection.get("arriving", []))
    elsewhere = {str(civ) for civ in projection.get("married_elsewhere", [])}
    lines = [strings["alliances_locked_title"].format(getattr(kingdom, "name", ""))]
    locked = [civ for civ in _catalog(wiring) if str(getattr(civ, "key", civ)) not in unlocked]
    if not locked:
        lines.append(strings["alliances_locked_none"])
        return "\n".join(lines)
    for civ in locked:
        key = str(getattr(civ, "key", civ))
        if key in elsewhere:
            reason = strings["alliances_locked_married"]
        else:
            required = [
                map_names.get(str(map_key), str(map_key))
                for map_key in (getattr(civ, "requires_map_keys", ()) or ())
            ]
            if required:
                reason = strings["alliances_locked_cond_maps"].format(", ".join(required))
            elif getattr(civ, "min_map_count", None):
                reason = strings["alliances_locked_cond_count"].format(int(civ.min_map_count))
            else:
                reason = strings["alliances_locked_cond_none"]
        lines.append(strings["alliances_locked_line"].format(names.get(key, key), reason))
    return "\n".join(lines)


async def history_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """📖 The append-only ledger: every civilization EVER acquired."""
    names = _civ_names(wiring)
    ledger = dict(getattr(kingdom, "civilization_acquisitions", None) or {})
    lines = [strings["alliances_history_title"].format(getattr(kingdom, "name", ""))]
    if not ledger:
        lines.append(strings["alliances_history_none"])
        return "\n".join(lines)
    entries = sorted(
        ((str(civ), info) for civ, info in ledger.items() if isinstance(info, dict)),
        key=lambda item: int(item[1].get("at", 0) or 0),
    )
    for civ, info in entries:
        stamp = datetime.fromtimestamp(int(info.get("at", 0) or 0), tz=ZoneInfo(_PARIS_TZ))
        lines.append(
            strings["alliances_history_line"].format(
                alliance_origin_emoji(info.get("origin")),
                names.get(civ, civ),
                stamp.strftime("%d/%m %Hh%M"),
            )
        )
    return "\n".join(lines)


async def durations_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """⏳ How long each held alliance has been held (game clock)."""
    names = _civ_names(wiring)
    ledger = dict(getattr(kingdom, "civilization_acquisitions", None) or {})
    hand = [str(civ) for civ in getattr(kingdom, "civilizations", []) or []]
    lines = [strings["alliances_durations_title"].format(getattr(kingdom, "name", ""))]
    if not hand:
        lines.append(strings["alliances_durations_none"])
        return "\n".join(lines)
    now_ts = int(game_now().timestamp())
    for civ_key in hand:
        info = ledger.get(civ_key) or {}
        line = strings["alliances_durations_line"].format(
            alliance_origin_emoji(info.get("origin")),
            names.get(civ_key, civ_key),
        )
        acquired_at = info.get("at")
        if acquired_at is not None:
            line += f" — {strings['alliances_durations_since'].format(_format_age(now_ts - int(acquired_at)))}"
        lines.append(line)
    return "\n".join(lines)


async def names_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """🔤 Display name ↔ techtree (provider) name of each held civ."""
    from kingdoms.mods.kingdoms.civ_crests import civ_provider_name

    names = _civ_names(wiring)
    hand = [str(civ) for civ in getattr(kingdom, "civilizations", []) or []]
    lines = [strings["alliances_names_title"].format(getattr(kingdom, "name", ""))]
    if not hand:
        lines.append(strings["alliances_names_none"])
        return "\n".join(lines)
    for civ_key in hand:
        provider = civ_provider_name(civ_key) or strings["unknown"]
        lines.append(strings["alliances_names_line"].format(names.get(civ_key, civ_key), provider))
    return "\n".join(lines)


_KIND_BUILDERS: dict[str, Any] = {
    "next": next_cycle_note,
    "locked": locked_note,
    "durations": durations_note,
    "history": history_note,
    "names": names_note,
}


async def _send_answer(kind: str, interaction: discord.Interaction) -> None:
    """Answer one button click with its ephemeral note (never a crash)."""
    locale = str(interaction.locale) if interaction.locale else "en"
    strings = _strings_of(locale)
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    kingdom = await kingdom_of_interaction(wiring, interaction)
    if kingdom is None:
        await interaction.response.send_message(strings["alliances_kingdom_none"], ephemeral=True)
        return
    try:
        note = await _KIND_BUILDERS[kind](wiring, kingdom, strings)
    except Exception:
        logger.warning("ALLIANCES PANEL: %s note failed", kind, exc_info=True)
        note = strings["alliances_no_data"]
    await interaction.response.send_message(note, ephemeral=True)


class _AlliancesButtonMixin:
    """The shared shape of the five alliances buttons."""

    _KIND: str = ""

    async def answer(self, interaction: discord.Interaction) -> None:
        await _send_answer(self._KIND, interaction)


class KingdomAlliancesNextButton(
    _AlliancesButtonMixin,
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:alliances:next",
):
    """The restart-proof 🔭 Prochain cycle button of the Alliances panel."""

    _KIND = "next"

    def __init__(
        self, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary
    ) -> None:
        super().__init__(
            discord.ui.Button(label=str(label)[:80], custom_id="kingdoms:alliances:next", style=style)
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAlliancesNextButton:
        """Rebuild the button from the wire — the only post-restart path."""
        return cls(_strings_of(interaction.locale)["alliances_btn_next"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the next-cycle projection, ephemeral and read-only."""
        await self.answer(interaction)


class KingdomAlliancesLockedButton(
    _AlliancesButtonMixin,
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:alliances:locked",
):
    """The restart-proof 🚫 Verrouillées button of the Alliances panel."""

    _KIND = "locked"

    def __init__(
        self, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(label)[:80], custom_id="kingdoms:alliances:locked", style=style
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAlliancesLockedButton:
        """Rebuild the button from the wire — the only post-restart path."""
        return cls(_strings_of(interaction.locale)["alliances_btn_locked"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the locked-civs sheet, ephemeral and read-only."""
        await self.answer(interaction)


class KingdomAlliancesHistoryButton(
    _AlliancesButtonMixin,
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:alliances:history",
):
    """The restart-proof 📖 Historique button of the Alliances panel."""

    _KIND = "history"

    def __init__(
        self, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(label)[:80], custom_id="kingdoms:alliances:history", style=style
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAlliancesHistoryButton:
        """Rebuild the button from the wire — the only post-restart path."""
        return cls(_strings_of(interaction.locale)["alliances_btn_history"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the acquisition ledger, ephemeral and read-only."""
        await self.answer(interaction)


class KingdomAlliancesDurationsButton(
    _AlliancesButtonMixin,
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:alliances:durations",
):
    """The restart-proof ⏳ Durées button of the Alliances panel."""

    _KIND = "durations"

    def __init__(
        self, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(label)[:80], custom_id="kingdoms:alliances:durations", style=style
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAlliancesDurationsButton:
        """Rebuild the button from the wire — the only post-restart path."""
        return cls(_strings_of(interaction.locale)["alliances_btn_durations"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the held-durations sheet, ephemeral and read-only."""
        await self.answer(interaction)


class KingdomAlliancesNamesButton(
    _AlliancesButtonMixin,
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:alliances:names",
):
    """The restart-proof 🔤 Noms button of the Alliances panel."""

    _KIND = "names"

    def __init__(
        self, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(label)[:80], custom_id="kingdoms:alliances:names", style=style
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAlliancesNamesButton:
        """Rebuild the button from the wire — the only post-restart path."""
        return cls(_strings_of(interaction.locale)["alliances_btn_names"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the display ↔ techtree names sheet, ephemeral and read-only."""
        await self.answer(interaction)
