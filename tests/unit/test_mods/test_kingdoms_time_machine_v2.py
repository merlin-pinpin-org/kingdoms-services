"""Kingdoms time machine v2 — the true game clock (machine-temps-v2, 14/10).

The offset seam: ``game_now() = real now + clock_offset`` everywhere,
the free-duration jump parser, the persisted ``SeasonState.clock_offset``,
the reset button's snapshot-then-zero path, and the snapshot restore
carrying the offset of its day verbatim.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.game_clock import (
    format_offset,
    game_now,
    game_offset_seconds,
    parse_duration,
    set_game_offset,
)
from kingdoms.mods.kingdoms.time_machine import TimeMachineError

from .test_kingdoms_time_machine import MONDAY, Bundle


def test_parse_duration_accepts_minutes_hours_days_and_combinations() -> None:
    """« 90m », « 2h », « 3d », « 1d2h30m » — and the fr « j » alias."""
    assert parse_duration("90m") == 5400
    assert parse_duration("2h") == 7200
    assert parse_duration("3d") == 3 * 86400
    assert parse_duration("3j") == 3 * 86400
    assert parse_duration("1d2h30m") == 86400 + 7200 + 1800
    assert parse_duration("  15 min ") == 900


def test_parse_duration_refuses_junk_zero_and_negative() -> None:
    """A bad jump must fail loudly, never pass as noise."""
    for bad in ("", "   ", "hier", "2x", "-2h", "0m", "abc30m"):
        with pytest.raises(ValueError):
            parse_duration(bad)


def test_game_now_rides_the_offset_and_never_goes_negative() -> None:
    """game_now() = real now + offset; a negative offset is refused."""
    before = game_now()
    set_game_offset(3600)
    try:
        assert game_now() - before >= timedelta(seconds=3600) - timedelta(seconds=5)
        assert game_offset_seconds() == 3600
    finally:
        set_game_offset(0)
    with pytest.raises(ValueError):
        set_game_offset(-1)


def test_format_offset_reads_like_drasah_wants_it() -> None:
    """« +2j 3h 15m », « +90m », « 0 »."""
    assert format_offset(0) == "0"
    assert format_offset(90 * 60) == "+1h 30m"
    assert format_offset(2 * 86400 + 3 * 3600 + 15 * 60) == "+2j 3h 15m"


async def test_advance_seconds_persists_the_offset_on_the_season() -> None:
    """A jump of D seconds adds D to the persisted clock_offset."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.kingdoms.start_season()
    result = await bundle.time.advance_seconds(7200, now=MONDAY)
    assert result["offset"] == 7200
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.clock_offset == 7200
    assert game_offset_seconds() == 7200
    # The status reports BOTH clocks: game now, real now, offset.
    status = await bundle.time.status(now=MONDAY + timedelta(hours=2))
    assert status["offset"] == 7200
    assert status["real_now"] is not None
    status = await bundle.time.status()
    assert status["now"] - datetime.now(tz=UTC) >= timedelta(seconds=7200 - 5)


async def test_advance_duration_accepts_free_text() -> None:
    """« 90m » rides the same offset path as the +N-day buttons."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.kingdoms.start_season()
    result = await bundle.time.advance_duration("90m", now=MONDAY)
    assert result["offset"] == 5400
    with pytest.raises(TimeMachineError):
        await bundle.time.advance_duration("n'importe quoi", now=MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.advance_duration("0m", now=MONDAY)


async def test_two_jumps_accumulate() -> None:
    """+1 day then +2h: the offsets stack on the season."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.kingdoms.start_season()
    await bundle.time.advance(1, now=MONDAY)
    result = await bundle.time.advance_seconds(7200, now=MONDAY + timedelta(days=1))
    assert result["offset"] == 86400 + 7200


async def test_reset_clock_zeros_the_offset_and_resumes_paused() -> None:
    """The reset button: snapshot first, offset 0, frozen True."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.kingdoms.start_season()
    await bundle.time.advance_seconds(86400, now=MONDAY)
    result = await bundle.time.reset_clock()
    assert result["offset"] == 0
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.clock_offset == 0
    assert season.frozen is True
    assert game_offset_seconds() == 0
    # The reset is reversible: the snapshot exists.
    snapshots = await bundle.time.snapshots()
    assert any(str(doc.get("label", "")).startswith("avant reset horloge") for doc in snapshots)


async def test_snapshot_restore_brings_the_offset_back() -> None:
    """A restore is a TRUE return: the clock offset of its day returns."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.kingdoms.start_season()
    snapshot = await bundle.time.create_snapshot("avant saut", now=MONDAY)
    await bundle.time.advance_seconds(172800, now=MONDAY)
    assert game_offset_seconds() == 172800
    await bundle.time.restore(snapshot["_id"], now=MONDAY + timedelta(days=2))
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.clock_offset == 0
    assert game_offset_seconds() == 0


async def test_no_season_reset_clock_refuses() -> None:
    """Without a season the reset answers the time-machine error."""
    bundle = Bundle()
    with pytest.raises(TimeMachineError):
        await bundle.time.reset_clock()
