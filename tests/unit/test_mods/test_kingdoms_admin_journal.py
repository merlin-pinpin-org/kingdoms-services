"""Kingdoms admin journal — unit tests (D75, epic #214 phase 1.2).

The foundation rules of D75, on an in-memory store: the reason is
mandatory without exception, the rollback window closes after 2
hours, one action rolls back at most once, and the 48-hour retention
compacts the payloads while keeping the summary.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.admin_journal import (
    RETENTION,
    ROLLBACK_WINDOW,
    AdminJournalService,
    AdminReasonRequiredError,
    AlreadyRolledBackError,
    RollbackWindowClosedError,
)

from .test_kingdoms_service import MemoryStore


def _journal() -> tuple[AdminJournalService, MemoryStore]:
    store = MemoryStore()
    return AdminJournalService(store), store  # type: ignore[arg-type]


async def test_record_requires_a_reason_without_exception() -> None:
    journal, store = _journal()
    for reason in ("", "   ", "\t"):
        with pytest.raises(AdminReasonRequiredError):
            await journal.record(
                season_id="s-1",
                action_type="rename_kingdom",
                actor_id="admin",
                actor_name="Drasah",
                reason=reason,
                target_kind="kingdom",
                target_id="k-1",
            )
    assert store.admin_actions == {}


async def test_record_journals_the_snapshots() -> None:
    journal, store = _journal()
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    action = await journal.record(
        season_id="s-1",
        action_type="rename_kingdom",
        actor_id="admin",
        actor_name="Drasah",
        reason="  Typo in the name  ",
        target_kind="kingdom",
        target_id="k-1",
        before={"kingdoms_kingdoms:k-1": {"_id": "k-1", "name": "Old"}},
        after={"kingdoms_kingdoms:k-1": {"_id": "k-1", "name": "New"}},
        now=now,
    )
    assert action.reason == "Typo in the name"  # stripped, verbatim otherwise
    assert action.before["kingdoms_kingdoms:k-1"]["name"] == "Old"
    assert store.admin_actions[action.id]["_id"] == action.id
    listed = await journal.actions("s-1")
    assert [entry.id for entry in listed] == [action.id]
    assert listed[0].action_type == "rename_kingdom"


async def test_rollback_returns_the_restore_payload_once() -> None:
    journal, _ = _journal()
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    action = await journal.record(
        season_id="s-1",
        action_type="eject_to_queue",
        actor_id="admin",
        actor_name="Drasah",
        reason="rule 13",
        target_kind="lord",
        target_id="p1",
        before={"kingdoms_lords:p1": {"_id": "p1"}},
        now=now,
    )
    rolled = await journal.rollback(action.id, now=now + timedelta(minutes=30))
    assert rolled.rolled_back is True
    assert rolled.before["kingdoms_lords:p1"]["_id"] == "p1"
    with pytest.raises(AlreadyRolledBackError):
        await journal.rollback(action.id, now=now + timedelta(minutes=31))


async def test_rollback_window_closes_after_two_hours() -> None:
    journal, _ = _journal()
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    action = await journal.record(
        season_id="s-1",
        action_type="rename_kingdom",
        actor_id="admin",
        actor_name="Drasah",
        reason="typo",
        target_kind="kingdom",
        target_id="k-1",
        now=now,
    )
    with pytest.raises(RollbackWindowClosedError):
        await journal.rollback(action.id, now=now + ROLLBACK_WINDOW + timedelta(seconds=1))
    # inside the window, the last second still rolls back
    rolled = await journal.rollback(action.id, now=now + ROLLBACK_WINDOW)
    assert rolled.rolled_back is True


async def test_retention_compacts_payloads_keeps_summary() -> None:
    journal, _ = _journal()
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    action = await journal.record(
        season_id="s-1",
        action_type="set_quotas",
        actor_id="admin",
        actor_name="Drasah",
        reason="season is full",
        target_kind="season",
        target_id="s-1",
        before={"kingdoms_seasons:s-1": {"_id": "s-1"}},
        after={"kingdoms_seasons:s-1": {"_id": "s-1"}},
        now=now,
    )
    assert await journal.compact(now=now + RETENTION) == 0
    assert await journal.compact(now=now + RETENTION + timedelta(minutes=1)) == 1
    listed = await journal.actions("s-1")
    assert listed[0].compacted is True
    assert listed[0].reason == "season is full"  # the summary line survives
    assert listed[0].before == {} and listed[0].after == {}
    with pytest.raises(RollbackWindowClosedError):
        await journal.rollback(action.id, now=now + RETENTION + timedelta(minutes=2))
    # idempotent: a second pass compacts nothing
    assert await journal.compact(now=now + RETENTION + timedelta(minutes=2)) == 0


async def test_rollback_of_an_unknown_action_raises() -> None:
    journal, _ = _journal()
    with pytest.raises(KeyError):
        await journal.rollback("aj-does-not-exist")
