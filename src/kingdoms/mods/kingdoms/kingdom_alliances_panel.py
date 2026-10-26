"""Kingdoms Alliances panel — the read-only clarity buttons (alliances-lisibilite-v1, Drasah 25/10).

Tranche 1 (commit bfd719e) gave every kingdom an append-only
acquisition ledger (``KingdomModel.civilization_acquisitions`` — 🎲
starter draft, 🗺️ cadastre condition, 💍 standard marriage, 👑 royal
marriage, 📜 Chef Mercenaire) and the read-only ``alliance_projection``
of the next cycle. This tranche surfaces them: the 🤝 Alliances salon
of every realm wears a panel that reads the hand with ONE emoji per
acquisition origin, and four restart-proof EPHEMERAL buttons answer the questions
Drasah's players actually ask —

- 🔭 Prochain cycle — the projection: kept / arriving / dropping,
  and the exclusivity of the married elsewhere;
- 🚫 Verrouillées — ONLY the civilizations unavailable because
  MARRIED to another kingdom (alliances-panel-v2, Drasah 26/10 —
  the kingdom's own marriages stay its own);
- ⏳ Durées — how long each alliance has been held (game clock);
- 📖 Historique — ONLY the LOST civilizations (acquired once, no
  longer in the hand), with origin emoji and date.

alliances-panel-v2 (Drasah 26/10): every ephemeral answer
SELF-DESTRUCTS after 10 minutes (ALLIANCES_EPHEMERAL_TTL) so the
salon never silts up with stacked bubbles.

Every button is a ``DynamicItem``: the custom_id carries the button
kind, the kingdom resolves from the salon's realm category at click
time (the salon is readable by every lord — the answer belongs to the
kingdom OF THE SALON, never of the clicker), and the services rebuild
from the live bot. The answers are ephemeral and strictly read-only:
the cycle recalculation alone decides the truth.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
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


def _as_utc(value: object) -> datetime | None:
    """Coerce a slot (datetime or ISO string) to an aware UTC datetime."""
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


async def cycle_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """📅 The next cycle switch countdown (alliances-panel-v3, Drasah 26/10).

    Reads the SAME schedule the EventService runs on: the season's
    beta slots first (beta weekends override the cron), then the weekly
    ``events.cycle_cron`` slots since the season start — the first slot
    after the game clock wins. No season → a plain "not scheduled" line
    (never a crash, the button must always answer).
    """
    service = getattr(wiring, "kingdoms_service", None)
    now = game_now()
    candidates: list[datetime] = []
    try:
        season = await service.current_season() if service is not None else None
    except Exception:
        logger.warning("ALLIANCES PANEL: season read failed", exc_info=True)
        season = None
    if season is not None:
        for raw in getattr(season, "beta_cycle_slots", None) or []:
            stamp = _as_utc(raw)
            if stamp is not None:
                candidates.append(stamp)
        events = getattr(getattr(service, "config", None), "events", None)
        cron = getattr(events, "cycle_cron", None)
        started = _as_utc(getattr(season, "started_at", None))
        if cron and started is not None:
            try:
                tz = ZoneInfo(str(getattr(events, "schedule_timezone", "UTC") or "UTC"))
            except Exception:
                tz = ZoneInfo("UTC")
            from kingdoms.mods.kingdoms.events import cron_slots_between

            candidates.extend(
                cron_slots_between(str(cron), started, now + timedelta(days=8), tz)
            )
    upcoming = sorted(stamp for stamp in candidates if stamp > now)
    lines = [strings["alliances_cycle_title"].format(getattr(kingdom, "name", ""))]
    if not upcoming:
        lines.append(strings["alliances_cycle_none"])
        return "\n".join(lines)
    lines.append(
        strings["alliances_cycle_line"].format(
            _format_age(int((upcoming[0] - now).total_seconds()))
        )
    )
    lines.append(strings["alliances_cycle_hint"])
    return "\n".join(lines)


async def next_cycle_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """🔭 The next-cycle preview: kept / arriving / dropping + hints."""
    projection = await _projection(wiring, kingdom)
    if projection is None:
        return strings["alliances_no_data"]
    names = _civ_names(wiring)
    # alliances-lisibilite-v1 T3 (Drasah 25/10): the 🎯 near-unlock
    # hints ("à 1 map près") are GONE from this answer — Drasah's call,
    # the near data stays in the projection but the view reads short.
    return "\n".join(
        [
            strings["alliances_next_title"].format(getattr(kingdom, "name", "")),
            strings["alliances_next_intro"],
            strings["alliances_next_kept"].format(_label(names, projection.get("kept", []))),
            strings["alliances_next_arriving"].format(_label(names, projection.get("arriving", []))),
            strings["alliances_next_dropping"].format(_label(names, projection.get("dropping", []))),
            strings["alliances_next_elsewhere"].format(
                _label(names, projection.get("married_elsewhere", []))
            ),
        ]
    )


async def locked_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """🚫 ONLY the civs married to OTHER kingdoms (alliances-panel-v2).

    Drasah 26/10: the old sheet listed every locked civ of the
    catalogue (~48 lines) — unreadable, and it overflowed Discord's
    2000-char limit so the interaction FAILED. The button now shows
    the only unavailable-for-a-reason-you-cannot-change civs: those
    MARRIED ELSEWHERE (exclusivité D74) — the kingdom's own marriages
    are acquisitions, they stay out of the list.
    """
    projection = await _projection(wiring, kingdom)
    if projection is None:
        return strings["alliances_no_data"]
    names = _civ_names(wiring)
    elsewhere = [str(civ) for civ in (projection.get("married_elsewhere", []) or [])]
    lines = [strings["alliances_locked_title"].format(getattr(kingdom, "name", ""))]
    lines.append(strings["alliances_locked_intro"])
    if not elsewhere:
        lines.append(strings["alliances_locked_none_married"])
        return "\n".join(lines)
    for civ in elsewhere:
        lines.append(
            strings["alliances_locked_line"].format(
                names.get(civ, civ), strings["alliances_locked_married"]
            )
        )
    return "\n".join(lines)


async def history_note(wiring: Any, kingdom: Any, strings: dict[str, str]) -> str:
    """📖 The LOST civilizations — acquired once, no longer in the hand."""
    names = _civ_names(wiring)
    ledger = dict(getattr(kingdom, "civilization_acquisitions", None) or {})
    lines = [strings["alliances_history_title"].format(getattr(kingdom, "name", ""))]
    # alliances-panel-v2 (Drasah 26/10): ONLY the LOST civs ride the
    # history — acquired once, gone from the hand since.
    hand = {str(civ) for civ in getattr(kingdom, "civilizations", []) or []}
    entries = sorted(
        (
            (str(civ), info)
            for civ, info in ledger.items()
            if isinstance(info, dict) and str(civ) not in hand
        ),
        key=lambda item: int(item[1].get("at", 0) or 0),
    )
    if not entries:
        lines.append(strings["alliances_history_none_lost"])
        return "\n".join(lines)
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


# alliances-panel-v2 (Drasah 26/10): every ephemeral answer
# self-destructs after ALLIANCES_EPHEMERAL_TTL seconds — a new click
# is a new interaction (a new bubble), the old ones must not pile up.
ALLIANCES_EPHEMERAL_TTL = 600.0
_ANSWER_EPHEMERAL_TIMERS: dict[tuple[str, int], asyncio.Task[None]] = {}


def _arm_answer_timer(kind: str, interaction: discord.Interaction) -> None:
    """(Re)arm the auto-destruction of one ephemeral answer."""
    key = (kind, id(interaction))
    existing = _ANSWER_EPHEMERAL_TIMERS.pop(key, None)
    if existing is not None:
        existing.cancel()

    async def _delete_later() -> None:
        try:
            await asyncio.sleep(ALLIANCES_EPHEMERAL_TTL)
            await interaction.delete_original_response()
        except Exception:
            logger.debug("ALLIANCES PANEL: ephemeral auto-delete skipped", exc_info=True)
        finally:
            _ANSWER_EPHEMERAL_TIMERS.pop(key, None)
            for state_key, stored in list(_ANSWER_EPHEMERAL_STATE.items()):
                if id(stored) == id(interaction):
                    _ANSWER_EPHEMERAL_STATE.pop(state_key, None)

    _ANSWER_EPHEMERAL_TIMERS[key] = asyncio.create_task(_delete_later())


# alliances-panel-v3 (Drasah 26/10): ONE ephemeral per (player, salon) —
# the prior bubble of the same player is DELETED before the new answer
# lands, the bubbles never pile up.
_ANSWER_EPHEMERAL_STATE: dict[tuple[str, str], discord.Interaction] = {}


async def _replace_prior_answer(key: tuple[str, str]) -> None:
    """Delete the prior ephemeral answer of one (player, salon) — best-effort."""
    prior = _ANSWER_EPHEMERAL_STATE.pop(key, None)
    if prior is None:
        return
    for timer_key in [k for k in _ANSWER_EPHEMERAL_TIMERS if k[1] == id(prior)]:
        _ANSWER_EPHEMERAL_TIMERS.pop(timer_key).cancel()
    try:
        await prior.delete_original_response()
    except Exception:
        logger.debug("ALLIANCES PANEL: prior ephemeral already gone", exc_info=True)


_KIND_BUILDERS: dict[str, Any] = {
    "cycle": cycle_note,
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
    # alliances-panel-v3 : UNE éphémère par (joueur, salon) — la bulle
    # précédente est supprimée avant que la nouvelle n'arrive.
    answer_key = (
        str(getattr(getattr(interaction, "user", None), "id", "?")),
        str(getattr(getattr(interaction, "channel", None), "id", "?")),
    )
    await _replace_prior_answer(answer_key)
    kingdom = await kingdom_of_interaction(wiring, interaction)
    if kingdom is None:
        await interaction.response.send_message(strings["alliances_kingdom_none"], ephemeral=True)
        _arm_answer_timer(kind, interaction)
        _ANSWER_EPHEMERAL_STATE[answer_key] = interaction
        return
    try:
        note = await _KIND_BUILDERS[kind](wiring, kingdom, strings)
    except Exception:
        logger.warning("ALLIANCES PANEL: %s note failed", kind, exc_info=True)
        note = strings["alliances_no_data"]
    await interaction.response.send_message(note, ephemeral=True)
    _arm_answer_timer(kind, interaction)
    _ANSWER_EPHEMERAL_STATE[answer_key] = interaction


class _AlliancesButtonMixin:
    """The shared shape of the five alliances buttons."""

    _KIND: str = ""

    async def answer(self, interaction: discord.Interaction) -> None:
        await _send_answer(self._KIND, interaction)


class KingdomAlliancesCycleButton(
    _AlliancesButtonMixin,
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:alliances:cycle",
):
    """The restart-proof 📅 countdown button of the Alliances panel (v3)."""

    _KIND = "cycle"

    def __init__(
        self, label: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=str(label)[:80], custom_id="kingdoms:alliances:cycle", style=style
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomAlliancesCycleButton:
        """Rebuild the button from the wire — the only post-restart path."""
        return cls(_strings_of(interaction.locale)["alliances_btn_cycle"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Send the next-cycle countdown, ephemeral and read-only."""
        await self.answer(interaction)


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
