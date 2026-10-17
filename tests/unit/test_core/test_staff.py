"""Tests for the core staff service: apply, decide, list, remove.

The staff lifecycle contract: an application is pending until an admin
decides, an accepted staff re-applying is a no-op, a declined member
may re-apply, and only the accepted members are staff.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.staff import StaffService

NOW = 1_000


class _FakeDatabase:
    """In-memory StaffDatabase stand-in."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    async def upsert_staff(self, document: dict[str, Any]) -> None:
        self.documents[document["_id"]] = document

    async def find_staff(self, staff_id: str) -> dict[str, Any] | None:
        return self.documents.get(staff_id)

    async def find_mod_staff(self, guild_id: str, mod: str) -> list[dict[str, Any]]:
        return [doc for doc in self.documents.values() if doc["guild_id"] == guild_id and doc["mod"] == mod]


class _RecordingEvents:
    """Events stand-in recording every admin notice."""

    def __init__(self) -> None:
        self.notices: list[str] = []

    async def notify_admins(self, message: str, payload: dict[str, Any]) -> None:
        self.notices.append(message)


@pytest.mark.asyncio
async def test_apply_records_a_pending_application_and_notifies() -> None:
    """An application lands pending, and the admins are notified."""
    db, events = _FakeDatabase(), _RecordingEvents()
    service = StaffService(db, events)
    document = await service.apply("42", "ladder", "777", now=NOW)
    assert document["status"] == "pending"
    assert events.notices == ["<@777> applied for the ladder staff."]


@pytest.mark.asyncio
async def test_apply_is_idempotent_for_accepted_staff() -> None:
    """An accepted staff re-applying changes nothing (still accepted)."""
    db = _FakeDatabase()
    service = StaffService(db)
    await service.apply("42", "ladder", "777", now=NOW)
    await service.decide("42", "ladder", "777", accept=True, decided_by="1", now=NOW)
    again = await service.apply("42", "ladder", "777", now=NOW + 10)
    assert again["status"] == "accepted"
    assert await service.is_staff("42", "ladder", "777") is True


@pytest.mark.asyncio
async def test_decide_accepts_a_pending_application() -> None:
    """An accept moves the application to accepted."""
    db, events = _FakeDatabase(), _RecordingEvents()
    service = StaffService(db, events)
    await service.apply("42", "ladder", "777", now=NOW)
    updated = await service.decide("42", "ladder", "777", accept=True, decided_by="1", now=NOW + 5)
    assert updated is not None and updated["status"] == "accepted"
    assert "<@777> was accepted" in events.notices[-1]


@pytest.mark.asyncio
async def test_decide_declines_and_allows_reapply() -> None:
    """A decline moves to declined, and the member may re-apply."""
    service = StaffService(_FakeDatabase())
    await service.apply("42", "ladder", "777", now=NOW)
    declined = await service.decide("42", "ladder", "777", accept=False, decided_by="1", now=NOW + 5)
    assert declined is not None and declined["status"] == "declined"
    assert await service.is_staff("42", "ladder", "777") is False
    reapplied = await service.apply("42", "ladder", "777", now=NOW + 10)
    assert reapplied["status"] == "pending"


@pytest.mark.asyncio
async def test_decide_without_application_returns_none() -> None:
    """Deciding on a member without a pending application is a None."""
    service = StaffService(_FakeDatabase())
    assert await service.decide("42", "ladder", "777", accept=True, decided_by="1", now=NOW) is None


@pytest.mark.asyncio
async def test_remove_drops_an_accepted_staff_only() -> None:
    """Remove works on accepted staff; a pending application is untouched."""
    service = StaffService(_FakeDatabase())
    await service.apply("42", "ladder", "777", now=NOW)
    assert await service.remove("42", "ladder", "777") is False
    await service.decide("42", "ladder", "777", accept=True, decided_by="1", now=NOW)
    assert await service.remove("42", "ladder", "777") is True
    assert await service.is_staff("42", "ladder", "777") is False


@pytest.mark.asyncio
async def test_list_staff_returns_only_accepted_of_the_mod() -> None:
    """The staff list is scoped per (guild, mod) and accepted-only."""
    db = _FakeDatabase()
    service = StaffService(db)
    await service.apply("42", "ladder", "a", now=NOW)
    await service.apply("42", "ladder", "b", now=NOW)
    await service.apply("42", "groups", "c", now=NOW)
    await service.decide("42", "ladder", "a", accept=True, decided_by="1", now=NOW)
    await service.decide("42", "groups", "c", accept=True, decided_by="1", now=NOW)
    assert await service.list_staff("42", "ladder") == ["a"]
    assert await service.list_staff("42", "groups") == ["c"]
    assert await service.list_staff("42", "ladder") == ["a"]


@pytest.mark.asyncio
async def test_apply_carries_the_motivation_message_to_the_notice() -> None:
    """The applicant's message rides the document and the admin notice."""
    db = _FakeDatabase()
    service = StaffService(db, _RecordingPayloadEvents())
    await service.apply("42", "ladder", "777", now=1_000, message="Dispo chaque soir !")
    doc = await db.find_staff(StaffService.staff_id("42", "ladder", "777"))
    assert doc is not None and doc["message"] == "Dispo chaque soir !"
    assert _RecordingPayloadEvents.last and _RecordingPayloadEvents.last["message"] == "Dispo chaque soir !"


class _RecordingPayloadEvents:
    """Events stand-in recording the last notice payload."""

    last: dict[str, Any] | None = None

    async def notify_admins(self, message: str, payload: dict[str, Any]) -> None:
        _RecordingPayloadEvents.last = payload
