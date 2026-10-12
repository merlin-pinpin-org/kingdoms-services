"""Kingdoms time machine — unit tests (chantier E, 2026-10-11).

The game clock behind the Machine à temps salon: the status line
(cycle, age, the upcoming key moments), the reversible fast-forward
(+1/+3/+7 days replay the real ``run_due`` orchestrator at a future
date), the jumps to the key moments, and the true rewind through
snapshots — a restore replays back the exact stored state and is
itself restorable.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import AttackService
from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig, default_map_catalog
from kingdoms.mods.kingdoms.events import EventService
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService
from kingdoms.mods.kingdoms.time_machine import (
    TimeMachineError,
    TimeMachineService,
)

from .test_kingdoms_events import Bundle as _EventsBundle
from .test_kingdoms_events import MemoryStore as _EventsStore
from .test_kingdoms_events import _config


class WipeStore(_EventsStore):
    """The events store with the Mongo-grade wipe (D38): a restore must
    clear territories/attacks/technologies/showmatches too, or the
    replay would inherit the fast-forward's documents."""

    async def wipe_season_data(self) -> None:
        await super().wipe_season_data()
        self.territories.clear()
        self.attacks.clear()
        self.technologies.clear()
        self.showmatches.clear()


class Bundle(_EventsBundle):
    """The events bundle rebuilt over the full-wipe store plus the clock."""

    def __init__(self, config: KingdomsSeasonConfig | None = None) -> None:
        cfg = config or _config()
        super().__init__(cfg)
        self.store = WipeStore()
        self.kingdoms = KingdomsService(self.store, cfg)  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, cfg, self.kingdoms)  # type: ignore[arg-type]
        self.attacks = AttackService(self.store, cfg, self.kingdoms, self.territories)  # type: ignore[arg-type]
        self.events = EventService(self.store, cfg, self.kingdoms, self.territories, self.attacks)  # type: ignore[arg-type]
        self.time = TimeMachineService(self.store, cfg, self.kingdoms, self.events)


def _full_catalog_weeks(weeks: int) -> KingdomsSeasonConfig:
    """The full 26-map catalog (a fast-forward must not exhaust the pool)."""
    return _config().model_copy(update={"weeks": weeks, "maps": default_map_catalog()})


MONDAY = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


async def test_status_reports_the_game_clock() -> None:
    """The status shows cycle 1, the current age and the next moments."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(MONDAY)
    status = await bundle.time.status(now=datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
    assert status["season"] is not None
    assert status["cycle"] == 1
    assert status["weeks"] == 4
    assert status["age"] == "dark_age"
    assert status["phase"] == "setup"
    assert status["finished"] is False
    assert status["next_cycle_at"] == datetime(2026, 10, 11, 21, 30, tzinfo=UTC)
    assert status["next_age_at"] == datetime(2026, 10, 13, 22, 0, tzinfo=UTC)
    # The verdict: the LAST cycle slot (Sunday 01/11 23:30 Paris) + 29 min.
    assert status["season_end_at"] == datetime(2026, 11, 1, 22, 59, tzinfo=UTC)
    assert status["snapshot_count"] == 0


async def test_status_without_season_is_empty_but_alive() -> None:
    """No season: the clock reports it without raising."""
    bundle = Bundle()
    status = await bundle.time.status(now=MONDAY)
    assert status["season"] is None
    assert status["snapshot_count"] == 0
    with pytest.raises(TimeMachineError):
        await bundle.time.jump_to("next_cycle", now=MONDAY)


async def test_snapshot_restore_round_trip_is_exact() -> None:
    """A restore replays back the exact stored state, verbatim."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", now=datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    )
    snapshot = await bundle.time.create_snapshot("s1", now=MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    season_before = season.model_dump()
    attacks_before = sorted(bundle.store.attacks)
    territories_before = sorted(bundle.store.territories)
    # Mutate: a second attack and a territory transfer.
    await bundle.attacks.declare_attack(
        "lord-a", (await bundle.owner_maps(bourgogne))[1], "aoe2de://lobby2",
        now=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    )
    result = await bundle.time.restore(snapshot["_id"], now=datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    assert result["restored"] == snapshot["_id"]
    assert sorted(bundle.store.attacks) == attacks_before
    assert sorted(bundle.store.territories) == territories_before
    season_after = await bundle.kingdoms.current_season()
    assert season_after is not None
    # The restore comes back FROZEN (the real clock must not undo the
    # rewind) — every other field matches the snapshot verbatim.
    assert season_after.frozen is True
    assert {k: v for k, v in season_after.model_dump().items() if k != "frozen"} == {
        k: v for k, v in season_before.items() if k != "frozen"
    }
    # The restore auto-snapshotted the pre-restore state: reversible twice.
    assert result["backup"] in [doc["_id"] for doc in await bundle.time.snapshots()]


async def test_advance_fast_forwards_the_calendar() -> None:
    """+7 days replay the real slots: the age switches, the cycle recharges."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    result = await bundle.time.advance(7, now=MONDAY)
    assert [r["kind"] for r in result["reports"]] == ["age", "cycle"]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    assert season.current_age_key == "feudal_age"
    gaia_id = next(k.id for k in await bundle.kingdoms.kingdoms() if k.is_gaia)
    gaia_maps = [
        t.map_key
        for t in await bundle.territories.territories()
        if t.owner_kingdom_id == gaia_id
    ]
    # 8 maps drawn at the initial distribution + 8 at the switch.
    assert len(gaia_maps) == 16


async def test_advance_past_the_end_finishes_the_season() -> None:
    """Fast-forwarding beyond the last Sunday applies the 23:59 verdict."""
    bundle = Bundle(_full_catalog_weeks(2))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    result = await bundle.time.advance(14, now=MONDAY)
    assert [r["kind"] for r in result["reports"]] == ["age", "cycle", "age", "end"]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.finished is True
    assert season.phase == "ended"
    assert await bundle.events.run_due(MONDAY + timedelta(days=20)) == []


async def test_restore_reverts_a_fast_forward() -> None:
    """The auto-snapshot taken before a jump restores the pre-jump clock."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    territories_before = sorted(bundle.store.territories)
    result = await bundle.time.advance(7, now=MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    await bundle.time.restore(result["backup"], now=MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 1
    assert season.current_age_key == "dark_age"
    assert sorted(bundle.store.territories) == territories_before


async def test_jump_to_the_key_moments() -> None:
    """The three jumps land exactly on cycle, age and season end."""
    bundle = Bundle(_full_catalog_weeks(2))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    jumped = await bundle.time.jump_to("next_age", now=MONDAY)
    assert [r["kind"] for r in jumped["reports"]] == ["age"]
    assert jumped["target"] == datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
    jumped = await bundle.time.jump_to("next_cycle", now=jumped["target"])
    assert [r["kind"] for r in jumped["reports"]] == ["cycle"]
    assert jumped["target"] == datetime(2026, 10, 11, 21, 30, tzinfo=UTC)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    # The last jump crosses the end Sunday: age switch then the 23:59 verdict.
    jumped = await bundle.time.jump_to("season_end", now=jumped["target"])
    assert [r["kind"] for r in jumped["reports"]] == ["age", "end"]
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.finished is True
    # Once finished, the same jump is refused (the moment passed).
    with pytest.raises(TimeMachineError):
        await bundle.time.jump_to("season_end", now=datetime(2026, 10, 20, 12, 0, tzinfo=UTC))


async def test_jump_to_unknown_moment_or_past_raises() -> None:
    """Bad moment names and non-positive advances are refused loudly."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.jump_to("next_birthday", now=MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.advance(0, now=MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.advance(-3, now=MONDAY)


async def test_restore_unknown_snapshot_raises() -> None:
    """An unknown snapshot id is a loud error, not a silent wipe."""
    bundle = Bundle()
    await bundle.launch_season()
    with pytest.raises(TimeMachineError):
        await bundle.time.restore("tm-nope", now=MONDAY)


async def test_advance_refuses_while_the_game_sits_in_setup() -> None:
    """Drasah's guard: in setup the time buttons answer "not started"."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.set_started_at(MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.phase == "setup"
    with pytest.raises(TimeMachineError):
        await bundle.time.advance(1, now=MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.jump_to("next_cycle", now=MONDAY)
    # Starting the game unlocks the machine.
    await bundle.kingdoms.start_season()
    assert await bundle.time.jump_to("next_age", now=MONDAY)


async def test_freeze_pauses_and_unfreeze_resumes() -> None:
    """The freeze flag flips and persists on the season."""
    bundle = Bundle()
    await bundle.launch_season()
    assert await bundle.time.freeze() is True
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.frozen is True
    assert await bundle.time.unfreeze() is False
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.frozen is False


async def test_restore_comes_back_frozen() -> None:
    """A rewind pauses the game — the real clock must not undo it."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    result = await bundle.time.advance(7, now=MONDAY)
    assert await bundle.time.restore(result["backup"], now=MONDAY) is not None
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.frozen is True
    assert season.current_cycle == 1


async def test_advance_tags_key_moment_snapshots() -> None:
    """Every replayed slot photographs the exact pre-switch state."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    await bundle.time.advance(7, now=MONDAY)
    moments = {doc["moment"] for doc in await bundle.time.snapshots() if doc.get("moment")}
    assert moments == {"age", "cycle"}
    assert all(doc["moment"] is None for doc in await bundle.time.snapshots() if not doc.get("moment"))


async def test_restore_last_rewinds_one_key_moment() -> None:
    """The one-click rewind lands on the exact pre-switch snapshot."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    await bundle.time.advance(7, now=MONDAY)  # age + cycle switched
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 2
    await bundle.time.restore_last("cycle", now=MONDAY)
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert season.current_cycle == 1
    assert season.current_age_key == "feudal_age"  # the age switch stands (post-cycle snapshot)
    # No snapshot of a moment never reached: loud error, no silent wipe.
    with pytest.raises(TimeMachineError):
        await bundle.time.restore_last("lords_day_end", now=MONDAY)
    with pytest.raises(TimeMachineError):
        await bundle.time.restore_last("next_birthday", now=MONDAY)


async def test_back_to_setup_wipes_without_archiving() -> None:
    """The fresh-start button: everything wiped, NO archive, snapshots alive."""
    bundle = Bundle(_full_catalog_weeks(4))
    await bundle.launch_season()
    await bundle.draw(1)
    await bundle.set_started_at(MONDAY)
    await bundle.kingdoms.start_season()
    await bundle.time.create_snapshot("gardien", now=MONDAY)
    assert await bundle.time.advance(7, now=MONDAY) is not None
    result = await bundle.time.back_to_setup()
    season = await bundle.kingdoms.current_season()
    assert season is not None
    assert result["season"] == season.id
    assert season.phase == "setup"
    assert season.current_cycle == 1
    # Only the freshly recreated Gaia kingdom remains — no player kingdom.
    assert [k for k in await bundle.kingdoms.kingdoms() if not k.is_gaia] == []
    assert await bundle.kingdoms.lords() == []
    # The wipe archives NOTHING: only the time-machine snapshots remain.
    archives = await bundle.store.find_season_archives()
    assert all(doc.get("kind") == "time_machine" for doc in archives)
    assert len(await bundle.time.snapshots()) >= 1


def test_status_text_shows_the_clock_even_in_setup() -> None:
    """Drasah's 2026-10-12 report: the setup panel read as "no timeline
    at all" — the CURRENT game time and the upcoming schedule (cycles,
    ages, season length) must be visible before the season starts."""
    from kingdoms.mods.kingdoms.kingdom_time_panel import _strings, status_text

    text = status_text(
        _strings("fr"),
        {
            "season": None,
            "now": datetime(2026, 10, 12, 10, 30, tzinfo=UTC),
            "snapshot_count": 0,
        },
        "fr",
    )
    assert "Horloge actuelle" in text
    assert "lundi 12 octobre" in text
    assert "Préparation" in text
    assert "dimanche 23h30" in text and "mercredi 00h00" in text

    started = status_text(
        _strings("fr"),
        {
            "season": "s-1",
            "now": datetime(2026, 10, 12, 10, 30, tzinfo=UTC),
            "cycle": 1,
            "weeks": 4,
            "age_display": "Âge sombre",
            "phase": "started",
            "finished": False,
            "frozen": False,
            "next_cycle_at": datetime(2026, 10, 18, 21, 30, tzinfo=UTC),
            "next_age_at": datetime(2026, 10, 13, 22, 0, tzinfo=UTC),
            "season_end_at": datetime(2026, 11, 1, 22, 59, tzinfo=UTC),
            "timezone": "Europe/Paris",
            "snapshot_count": 1,
        },
        "fr",
    )
    assert "Horloge actuelle" in started
    assert "En cours" in started and "1/4" in started
    assert "18 octobre" in started


async def test_advance_elapses_the_marriage_locks_with_the_game_clock() -> None:
    """Drasah's live finding (2026-10-12): the locks were wall-clock —
    a Machine à temps +3 days never expired a 24h honeymoon, so the
    livery test flow (red → ring) was impossible. The game clock now
    elapses the active locks with each fast-forward."""
    bundle = Bundle()
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-a")
    lord.married_civilization = "azteques"
    lord.marriage_locked_until = int((MONDAY + timedelta(hours=12)).timestamp())
    await bundle.store.upsert_lord(lord.to_mongo())

    # +1 day of game time elapses a 12h lock: clamped to 0 (expired)
    assert await bundle.kingdoms._shift_marriage_locks(
        int(timedelta(days=1).total_seconds())
    ) == 1
    fresh = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-a")
    assert fresh.marriage_locked_until == 0

    # a second shift is a no-op (nothing active anymore)
    assert await bundle.kingdoms._shift_marriage_locks(3600) == 0
