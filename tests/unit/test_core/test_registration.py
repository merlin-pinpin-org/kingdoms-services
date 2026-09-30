"""Unit tests for the RegistrationService and DM workflow (kingdoms-services#133)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.models.workflow import WorkflowStatus
from kingdoms.core.services.registration import (
    PROFILE_BINDINGS_COLLECTION,
    REGISTRATIONS_COLLECTION,
    InvalidProfileError,
    ProfileBoundError,
    RegistrationService,
)
from kingdoms.core.workflows.registration import RegistrationWorkflow
from tests.unit.test_core.test_game_data import FakeAudit


class FakeRegistrationDatabase:
    """In-memory RegistrationDatabase."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _collection(self, name: str) -> dict[str, dict[str, Any]]:
        return self.collections.setdefault(name, {})

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        self._collection(collection)[document["_id"]] = document

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        return self._collection(collection).get(entry_id)

    async def find_user_bindings(self, user_id: str) -> list[dict[str, Any]]:
        return [d for d in self._collection(PROFILE_BINDINGS_COLLECTION).values() if d["user_id"] == user_id]

    async def find_binding_by_profile(self, game_key: str, profile_id: str) -> dict[str, Any] | None:
        for d in self._collection(PROFILE_BINDINGS_COLLECTION).values():
            if d["game_key"] == game_key and d["profile_id"] == profile_id:
                return d
        return None


class FakeProfileSeam:
    """Game seam validating a fixed set of profile ids."""

    def __init__(self, valid: set[str]) -> None:
        self.valid = valid

    async def validate_profile(self, profile_id: str) -> dict[str, Any] | None:
        if profile_id not in self.valid:
            return None
        return {"profile_id": profile_id, "display_name": f"player-{profile_id}"}


class FakeEvents:
    """In-memory notification-intent recorder."""

    def __init__(self) -> None:
        self.intents: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, intent: str, payload: dict[str, Any]) -> None:
        self.intents.append((intent, payload))


def _env(valid: set[str] | None = None) -> tuple[RegistrationService, FakeRegistrationDatabase, FakeEvents]:
    db, events = FakeRegistrationDatabase(), FakeEvents()
    seam = FakeProfileSeam(valid or {"123456"})
    svc = RegistrationService(db, {"aoe2": seam}, events, FakeAudit())
    return svc, db, events


@pytest.mark.asyncio
async def test_bind_profile_stores_game_side() -> None:
    svc, db, _ = _env()
    binding = await svc.bind_profile("user:1", "aoe2", "123456")
    assert binding["profile_id"] == "123456"
    assert binding["profile"]["display_name"] == "player-123456"
    assert await db.find_entry(PROFILE_BINDINGS_COLLECTION, "binding:aoe2:user:1") is not None


@pytest.mark.asyncio
async def test_bind_invalid_profile_rejected() -> None:
    svc, _, _ = _env(valid={"123456"})
    with pytest.raises(InvalidProfileError):
        await svc.bind_profile("user:1", "aoe2", "999999")


@pytest.mark.asyncio
async def test_bind_profile_already_owned() -> None:
    svc, _, _ = _env()
    await svc.bind_profile("user:1", "aoe2", "123456")
    with pytest.raises(ProfileBoundError):
        await svc.bind_profile("user:2", "aoe2", "123456")


@pytest.mark.asyncio
async def test_rebind_same_user_idempotent() -> None:
    svc, _, _ = _env()
    first = await svc.bind_profile("user:1", "aoe2", "123456")
    second = await svc.bind_profile("user:1", "aoe2", "123456")
    assert first["_id"] == second["_id"]


@pytest.mark.asyncio
async def test_unknown_game_rejected() -> None:
    svc, _, _ = _env()
    with pytest.raises(ValueError, match="no profile seam"):
        await svc.bind_profile("user:1", "chess", "123456")


@pytest.mark.asyncio
async def test_has_any_profile_and_list() -> None:
    svc, _, _ = _env()
    assert await svc.has_any_profile("user:1") is False
    await svc.bind_profile("user:1", "aoe2", "123456")
    assert await svc.has_any_profile("user:1") is True
    bindings = await svc.list_bindings("user:1")
    assert [b["game_key"] for b in bindings] == ["aoe2"]


@pytest.mark.asyncio
async def test_request_enrollment_notifies_admins() -> None:
    svc, db, events = _env()
    request = await svc.request_enrollment("user:1", "guild:1")
    assert request["status"] == "pending"
    assert ("registration.requested", {"user_id": "user:1", "guild_id": "guild:1"}) in events.intents
    assert await db.find_entry(REGISTRATIONS_COLLECTION, "registration:guild:1:user:1") is not None


# ── Workflow ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_full_happy_path() -> None:
    svc, _, _ = _env()
    wf = RegistrationWorkflow(svc)
    state = await wf.start({"locale": "fr"})
    assert state.current_step == "entry" and state.status == WorkflowStatus.IN_PROGRESS
    state = await wf.handle_interaction(state, {"action": "confirm", "user_id": "user:9"})
    assert state.current_step == "profile"
    state = await wf.handle_interaction(state, {"action": "profile", "user_id": "user:9", "profile_id": "123456"})
    assert state.current_step == "done" and state.status == WorkflowStatus.COMPLETED
    assert await svc.has_any_profile("user:9") is True


@pytest.mark.asyncio
async def test_workflow_decline_cancels() -> None:
    svc, _, _ = _env()
    wf = RegistrationWorkflow(svc)
    state = await wf.start({})
    state = await wf.handle_interaction(state, {"action": "decline"})
    assert state.status == WorkflowStatus.CANCELLED
    assert state.payload["cancel_reason"] == "entry_declined"


@pytest.mark.asyncio
async def test_workflow_invalid_profile_retries_same_step() -> None:
    svc, _, _ = _env(valid={"123456"})
    wf = RegistrationWorkflow(svc)
    state = await wf.start({})
    state = await wf.handle_interaction(state, {"action": "confirm", "user_id": "user:1"})
    state = await wf.handle_interaction(state, {"action": "profile", "user_id": "user:1", "profile_id": "000"})
    assert state.current_step == "profile"
    assert state.payload["error"] == "invalid_profile"
    state = await wf.handle_interaction(state, {"action": "profile", "user_id": "user:1", "profile_id": "123456"})
    assert state.status == WorkflowStatus.COMPLETED


@pytest.mark.asyncio
async def test_workflow_empty_profile_id_retries() -> None:
    svc, _, _ = _env()
    wf = RegistrationWorkflow(svc)
    state = await wf.start({})
    state = await wf.handle_interaction(state, {"action": "confirm", "user_id": "user:1"})
    state = await wf.handle_interaction(state, {"action": "profile", "user_id": "user:1", "profile_id": "  "})
    assert state.current_step == "profile" and state.payload["error"] == "empty_profile_id"


@pytest.mark.asyncio
async def test_workflow_timeout_cancels() -> None:
    svc, _, _ = _env()
    wf = RegistrationWorkflow(svc)
    state = await wf.start({})
    state = await wf.on_timeout(state)
    assert state.status == WorkflowStatus.CANCELLED
    assert state.payload["cancel_reason"] == "timeout"


@pytest.mark.asyncio
async def test_workflow_with_engine_roundtrip() -> None:
    from kingdoms.core.services.state import StateService
    from kingdoms.core.services.workflow import WorkflowEngine
    from tests.mocks.workflow_store_mock import InMemoryWorkflowStore
    from tests.unit.test_core.test_workflow_engine import InMemoryStateStore

    svc, _, _ = _env()
    engine = WorkflowEngine(InMemoryWorkflowStore(), StateService(InMemoryStateStore()))
    engine.register_workflow(RegistrationWorkflow(svc))
    await engine.start()
    workflow_id = await engine.start_workflow("registration", "guild:1", "user:7", {"locale": "fr"})
    state = await engine.handle_interaction(workflow_id, {"action": "confirm", "user_id": "user:7"})
    assert state.current_step == "profile"
    state = await engine.handle_interaction(
        workflow_id, {"action": "profile", "user_id": "user:7", "profile_id": "123456"}
    )
    assert state.status == WorkflowStatus.COMPLETED
    assert await svc.has_any_profile("user:7") is True
    await engine.stop()
