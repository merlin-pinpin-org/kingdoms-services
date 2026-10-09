"""Core staff service: per-mod staff applications and membership.

Every mod wants a **staff**: members who can act as an admin inside
the mod's scope. The flow is the same for every mod, so the core owns
it once:

1. a member **applies** — through the platform's surface (a context
   menu, a home button) — the application is persisted with the
   pending state;
2. the bot **notifies** its admin channel (the platform seam delivers
   the notice);
3. an admin **accepts** or **declines** — the application moves to
   accepted/declined and the caller syncs the mod's staff role
   (SeasonRolesService on the platform wiring).

The staff list is per (guild, mod): the platform resolves "who is
staff" through ``list_staff`` — a guard seam for mod actions.

Reference: §0/§6 (the core never imports platform code), ADR-0020.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.staff")

STAFF_COLLECTION = "staff"
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DECLINED = "declined"


class StaffDatabase(Protocol):
    """Narrow persistence seam for staff documents."""

    async def upsert_staff(self, document: dict[str, Any]) -> None:
        """Insert or replace one staff document."""
        ...

    async def find_staff(self, staff_id: str) -> dict[str, Any] | None:
        """Return one staff document; None when absent."""
        ...

    async def find_mod_staff(self, guild_id: str, mod: str) -> list[dict[str, Any]]:
        """Every staff document of one (guild, mod)."""
        ...


class StaffEvents(Protocol):
    """Narrow seam to notify the admins (application / decision)."""

    async def notify_admins(self, message: str, payload: dict[str, Any]) -> None:
        """Deliver an admin notice through the platform (best-effort)."""
        ...


class StaffService:
    """Own the staff lifecycle: apply, decide, list, remove."""

    def __init__(self, database: StaffDatabase, events: StaffEvents | None = None) -> None:
        self._db = database
        self._events = events

    @staticmethod
    def staff_id(guild_id: str, mod: str, user_id: str) -> str:
        """Build the staff document id: one per (guild, mod, user)."""
        return f"staff:{guild_id}:{mod}:{user_id}"

    async def apply(self, guild_id: str, mod: str, user_id: str, now: int, message: str = "") -> dict[str, Any]:
        """Record a staff application (idempotent while pending).

        An existing accepted staff re-applying is a no-op; a declined
        application may re-apply (the document returns to pending).
        ``message`` is the applicant's free-text motivation, carried to
        the admin notice (never stored empty).
        """
        staff_id = self.staff_id(guild_id, mod, user_id)
        existing = await self._db.find_staff(staff_id)
        if existing is not None and existing.get("status") == STATUS_ACCEPTED:
            return existing
        document = {
            "_id": staff_id,
            "guild_id": guild_id,
            "mod": mod,
            "user_id": user_id,
            "status": STATUS_PENDING,
            "applied_at": now,
            "message": (message or "").strip()[:1000],
            "decided_at": None,
            "decided_by": None,
        }
        await self._db.upsert_staff(document)
        if self._events is not None:
            await self._events.notify_admins(
                f"<@{user_id}> applied for the {mod} staff.",
                {
                    "kind": "staff.applied",
                    "guild_id": guild_id,
                    "mod": mod,
                    "user_id": user_id,
                    "message": document["message"],
                },
            )
        return document

    async def decide(
        self, guild_id: str, mod: str, user_id: str, accept: bool, decided_by: str, now: int
    ) -> dict[str, Any] | None:
        """Accept or decline a pending application; the updated document.

        Returns None when there is no pending application for that
        member — an accepted staff stays (use remove), a decline does
        not block a later re-apply recorded again as pending.
        """
        staff_id = self.staff_id(guild_id, mod, user_id)
        existing = await self._db.find_staff(staff_id)
        if existing is None or existing.get("status") != STATUS_PENDING:
            return None
        existing["status"] = STATUS_ACCEPTED if accept else STATUS_DECLINED
        existing["decided_at"] = now
        existing["decided_by"] = decided_by
        await self._db.upsert_staff(existing)
        if self._events is not None:
            decision = "accepted" if accept else "declined"
            await self._events.notify_admins(
                f"<@{user_id}> was {decision} into the {mod} staff.",
                {"kind": f"staff.{decision}", "guild_id": guild_id, "mod": mod, "user_id": user_id},
            )
        return existing

    async def remove(self, guild_id: str, mod: str, user_id: str) -> bool:
        """Remove one staff member (admin action); True when removed."""
        staff_id = self.staff_id(guild_id, mod, user_id)
        existing = await self._db.find_staff(staff_id)
        if existing is None or existing.get("status") != STATUS_ACCEPTED:
            return False
        existing["status"] = "removed"
        await self._db.upsert_staff(existing)
        if self._events is not None:
            await self._events.notify_admins(
                f"<@{user_id}> was removed from the {mod} staff.",
                {"kind": "staff.removed", "guild_id": guild_id, "mod": mod, "user_id": user_id},
            )
        return True

    async def list_staff(self, guild_id: str, mod: str) -> list[str]:
        """Every accepted staff member of one (guild, mod)."""
        return [
            str(doc["user_id"])
            for doc in await self._db.find_mod_staff(guild_id, mod)
            if doc.get("status") == STATUS_ACCEPTED
        ]

    async def is_staff(self, guild_id: str, mod: str, user_id: str) -> bool:
        """Whether one member is accepted staff of the mod."""
        return user_id in await self.list_staff(guild_id, mod)
