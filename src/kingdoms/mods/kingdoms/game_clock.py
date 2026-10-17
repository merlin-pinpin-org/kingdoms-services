"""The game clock seam (machine-temps-v2, Drasah 14/10/2026).

The v1 time machine replayed the missed slots but did NOT move the
clock: ``status()`` answered the real wall time and the ~29 battle
phases, locks, cooldowns and cron reads still consulted
``datetime.now()`` — a « +1 day » left every T-15/T-5 phase anchored
on the real calendar.

This module owns the ONE seam the whole mod reads the time through:

- ``clock_offset`` — the persisted seconds the game clock runs AHEAD
  of the real clock (``SeasonState.clock_offset``, admin/test state
  only: a normal game always runs at offset 0);
- ``game_now()`` — the game clock: ``real now + offset``. Every
  service, scheduler and surface that used to read ``datetime.now()``
  reads this instead;
- ``parse_duration()`` — the free-jump parser (« 90m », « 2h »,
  « 15m », « 3d », combinable like « 1d2h30m »);
- ``format_offset()`` — the « +2h 15m » label of the panel line.

The offset cache is process-wide and ALWAYS re-synced from the
persisted season: at every loop tick, at every time-machine read and
mutation, and after every snapshot restore — a restart converges back
to the persisted offset within one tick. The offset is never negative
(a rewind goes through the snapshots, never through the clock).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.service import KingdomsService

_OFFSET_SECONDS: int = 0
"""The process-wide game-clock offset, re-synced from the season doc."""

_DURATION_UNITS: dict[str, int] = {
    "m": 60,
    "min": 60,
    "h": 3600,
    "d": 86400,
    "j": 86400,
}
_DURATION_RE = re.compile(r"(?P<value>\d+)\s*(?P<unit>min|h|d|j|m)", re.IGNORECASE)


def game_now() -> datetime:
    """Return the game clock: the real UTC instant plus the test offset."""
    return datetime.now(tz=UTC) + timedelta(seconds=_OFFSET_SECONDS)


def game_offset_seconds() -> int:
    """Return the current game-clock offset (seconds, never negative)."""
    return _OFFSET_SECONDS


def set_game_offset(seconds: int) -> None:
    """Set the process game-clock offset (negative values are refused)."""
    global _OFFSET_SECONDS
    value = int(seconds)
    if value < 0:
        raise ValueError("the game-clock offset can never be negative")
    _OFFSET_SECONDS = value


async def sync_game_offset(kingdoms_service: KingdomsService | None) -> int:
    """Re-sync the process offset from the persisted season (best effort).

    Returns the applied offset. A missing service or season keeps the
    cache untouched — a bot without a season runs on the real clock.
    """
    if kingdoms_service is None:
        return _OFFSET_SECONDS
    try:
        season = await kingdoms_service.current_season()
    except Exception:
        return _OFFSET_SECONDS
    if season is None:
        return _OFFSET_SECONDS
    set_game_offset(int(getattr(season, "clock_offset", 0) or 0))
    return _OFFSET_SECONDS


def parse_duration(text: str) -> int:
    """Parse a free-jump duration into seconds (« 90m », « 2h », « 3d »).

    Units: ``m``/``min`` (minutes), ``h`` (hours), ``d``/``j`` (days) —
    combinable (« 1d2h30m »). Spaces are free, the value must be
    strictly positive: a negative or zero jump is refused loudly (the
    rewind goes through the snapshots, never through the clock).
    """
    cleaned = str(text).strip().lower().replace(" ", "")
    if not cleaned:
        raise ValueError("the duration is empty")
    matches = list(_DURATION_RE.finditer(cleaned))
    # Refuse leftovers: « 2x » or « hier » must not pass as noise.
    if not matches or _DURATION_RE.sub("", cleaned).strip():
        raise ValueError(f"the duration {text!r} does not parse (m/h/d)")
    total = sum(int(m.group("value")) * _DURATION_UNITS[m.group("unit")] for m in matches)
    if total <= 0:
        raise ValueError("the duration must be strictly positive")
    return total


def format_offset(seconds: int) -> str:
    """Render the offset as Drasah reads it: « +2h 15m », « +3j »."""
    value = int(seconds)
    if value <= 0:
        return "0"
    days, rest = divmod(value, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}j")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return "+" + " ".join(parts)
