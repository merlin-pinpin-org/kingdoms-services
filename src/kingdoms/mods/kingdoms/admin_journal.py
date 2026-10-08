"""Kingdoms mod — the common admin foundation: Journal + Snapshot + Rollback.

Decision D75 (kingdoms#166, epic kingdoms-services#214 phase 1.2): every
admin action of the six Admin panels rides the **same** foundation —
a reason **mandatory without exception**, a before/after snapshot of the
touched documents, a timestamped journal entry, and a per-action
rollback:

- **Reason**: ``record`` refuses an empty reason — the check lives here,
  in the one place every surface goes through, so no panel can bypass it.
- **Rollback**: per action (never per timestamp), inside a 2-hour
  window, at most once per action; the ``before`` snapshot of the entry
  is the restoration payload — the domain service reapplies it.
- **Retention**: after 48 hours the entry is *compacted* — the before/
  after payloads are dropped, the summary line (who, what, why, when)
  stays. The journal is an audit trail first, a restore source second.

The service is storage-agnostic (the ``KingdomsStore`` Protocol — the
three ``*_admin_action`` seams); Mongo is the production adapter. No
Discord here: the panels localize their own strings, the journal
stores the raw reason verbatim.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from kingdoms.core.exceptions import KingdomsError
from kingdoms.mods.kingdoms.models import AdminActionModel
from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.admin_journal")

__all__ = [
    "ADMIN_JOURNAL_COLLECTION",
    "RETENTION",
    "ROLLBACK_WINDOW",
    "AdminActionModel",
    "AdminJournalService",
    "AdminReasonRequiredError",
    "AlreadyRolledBackError",
    "RollbackWindowClosedError",
    "compact_action",
]

ADMIN_JOURNAL_COLLECTION = "kingdoms_admin_journal"
ROLLBACK_WINDOW = timedelta(hours=2)
"""D75: a rollback is possible for 2 hours after the action, once."""

RETENTION = timedelta(hours=48)
"""D75: after 48 hours the payloads are compacted, the summary stays."""

_ACTION_SEQ = count()


class AdminJournalError(KingdomsError):
    """Base of the journal rule errors — the surface localizes them."""

    code = "KINGDOMS_ADMIN_JOURNAL"
    message_key = "kingdoms.errors.unexpected"


class AdminReasonRequiredError(AdminJournalError):
    """Raised when an admin action is recorded without a reason (D75)."""

    code = "KINGDOMS_ADMIN_REASON_REQUIRED"
    message_key = "kingdoms.errors.admin_reason_required"


class RollbackWindowClosedError(AdminJournalError):
    """Raised when the 2-hour rollback window of an action has expired."""

    code = "KINGDOMS_ROLLBACK_WINDOW_CLOSED"
    message_key = "kingdoms.errors.rollback_window_closed"


class AlreadyRolledBackError(AdminJournalError):
    """Raised when an action is rolled back twice (1 max per action)."""

    code = "KINGDOMS_ALREADY_ROLLED_BACK"
    message_key = "kingdoms.errors.already_rolled_back"


def compact_action(action: AdminActionModel) -> AdminActionModel:
    """Drop the restore payloads of one entry (the 48h archival, D75).

    Pure function: the caller persists the result. A compacted entry
    keeps the summary — action, actor, target, reason, timestamps.
    """
    action.compacted = True
    action.before = {}
    action.after = {}
    return action


class AdminJournalService:
    """The single journal every Admin panel records through (D75).

    Storage-agnostic over the ``KingdomsStore`` Protocol; the caller
    provides the clock (``now=``) so the window and retention rules are
    deterministically testable.
    """

    def __init__(self, store: KingdomsStore) -> None:
        """Store the persistence seam."""
        self._store = store

    async def record(
        self,
        *,
        season_id: str,
        action_type: str,
        actor_id: str,
        actor_name: str,
        reason: str,
        target_kind: str,
        target_id: str,
        before: dict[str, dict[str, Any]] | None = None,
        after: dict[str, dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> AdminActionModel:
        """Journal one admin action with its snapshots (reason mandatory).

        ``before``/``after`` are keyed by collection name, each value a
        raw store document. An empty or blank reason raises — D75 makes
        the reason mandatory **without exception**.
        """
        if not (reason or "").strip():
            raise AdminReasonRequiredError("an admin action requires a reason (D75)")
        moment = now or datetime.now(tz=UTC)
        action = AdminActionModel(
            _id=f"aj-{moment:%Y%m%d%H%M%S}-{next(_ACTION_SEQ)}",
            season_id=season_id,
            action_type=action_type,
            actor_id=actor_id,
            actor_name=actor_name,
            reason=reason.strip(),
            target_kind=target_kind,
            target_id=target_id,
            before=before or {},
            after=after or {},
            created_at=moment,
        )
        await self._store.insert_admin_action(action.to_mongo())
        logger.info("kingdoms: admin action %s %s on %s", action_type, action.id, target_id)
        return action

    async def actions(self, season_id: str | None = None) -> list[AdminActionModel]:
        """Return the journal entries, newest first."""
        docs = await self._store.find_admin_actions()
        entries = [AdminActionModel.from_mongo(doc) for doc in docs]
        if season_id is not None:
            entries = [entry for entry in entries if entry.season_id == season_id]
        return sorted(entries, key=lambda entry: entry.created_at, reverse=True)

    async def rollback(self, action_id: str, *, now: datetime | None = None) -> AdminActionModel:
        """Mark one action rolled back — once, inside the 2h window (D75).

        Returns the entry: the caller restores the state from its
        ``before`` payloads (the domain owns the reapplication). Raises
        after the window or on a second rollback of the same action.
        """
        action = await self._require_action(action_id)
        if action.rolled_back:
            raise AlreadyRolledBackError("this action has already been rolled back")
        if action.compacted:
            raise RollbackWindowClosedError("the entry is compacted — no restore payload left")
        moment = now or datetime.now(tz=UTC)
        created = action.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if moment - created > ROLLBACK_WINDOW:
            raise RollbackWindowClosedError("the 2-hour rollback window has closed (D75)")
        action.rolled_back = True
        action.rolled_back_at = moment
        await self._store.upsert_admin_action(action.to_mongo())
        logger.info("kingdoms: admin action %s rolled back", action.id)
        return action

    async def compact(self, *, now: datetime | None = None) -> int:
        """Compact every entry past the 48h retention (D75 archival).

        Returns the number of compacted entries; idempotent.
        """
        moment = now or datetime.now(tz=UTC)
        compacted = 0
        for action in await self.actions():
            created = action.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if action.compacted or moment - created <= RETENTION:
                continue
            await self._store.upsert_admin_action(compact_action(action).to_mongo())
            compacted += 1
        if compacted:
            logger.info("kingdoms: %s admin journal entries compacted", compacted)
        return compacted

    async def _require_action(self, action_id: str) -> AdminActionModel:
        """Return one journal entry or raise."""
        for action in await self.actions():
            if action.id == action_id:
                return action
        raise KeyError(f"no admin action with id {action_id}")
