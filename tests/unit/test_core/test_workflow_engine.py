"""Unit tests for the WorkflowEngine (kingdoms-services#6).

Uses in-memory substitutes for the durable store and the hot-state service
(docs/architecture/testing.md). The MongoDB-backed store path is exercised
by CI against real MongoDB via docker compose.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.models.workflow import WorkflowStatus, WorkflowTransition
from kingdoms.core.services.state import StateService
from kingdoms.core.services.workflow import (
    IWorkflowStore,
    WorkflowEngine,
    WorkflowNotFoundError,
    WorkflowNotRegisteredError,
)
from tests.mocks.state_mock import FakeClock, InMemoryStateStore
from tests.mocks.workflow_store_mock import InMemoryWorkflowStore


class ScriptedWorkflow:
    """IWorkflow whose transitions replay a scripted sequence."""

    def __init__(self, name: str, script: list[WorkflowTransition]) -> None:
        """Record the workflow name and the transition script."""
        self.name = name
        self._script = list(script)
        self.interactions: list[dict[str, Any]] = []
        self.timeouts = 0
        self.started_with: dict[str, Any] | None = None

    def steps(self) -> list[str]:
        """Ordered step names of the scripted transitions."""
        return [transition.current_step for transition in self._script]

    async def start(self, context: dict[str, Any]) -> WorkflowTransition:
        """Return the first scripted transition."""
        self.started_with = context
        return self._script[0]

    async def handle_interaction(self, state: WorkflowTransition, event: dict[str, Any]) -> WorkflowTransition:
        """Record the event and return the next scripted transition."""
        self.interactions.append(event)
        index = min(
            (i for i, transition in enumerate(self._script) if transition.current_step == state.current_step),
            default=len(self._script) - 1,
        )
        return self._script[min(index + 1, len(self._script) - 1)]

    async def on_timeout(self, state: WorkflowTransition) -> WorkflowTransition:
        """Return the TIMED_OUT transition and count the timeout."""
        self.timeouts += 1
        return WorkflowTransition(
            current_step=state.current_step,
            status=WorkflowStatus.TIMED_OUT,
            payload=dict(state.payload),
        )


def registration_script() -> list[WorkflowTransition]:
    """Three-step registration: ask_name -> ask_game -> done."""
    return [
        WorkflowTransition(current_step="ask_name", status=WorkflowStatus.PENDING),
        WorkflowTransition(current_step="ask_game", status=WorkflowStatus.IN_PROGRESS),
        WorkflowTransition(current_step="done", status=WorkflowStatus.COMPLETED),
    ]


@pytest.fixture
def clock() -> FakeClock:
    """A controllable clock starting at zero."""
    return FakeClock()


@pytest.fixture
def store() -> InMemoryWorkflowStore:
    """A durable in-memory workflow store."""
    return InMemoryWorkflowStore()


@pytest.fixture
def state_store(clock: FakeClock) -> InMemoryStateStore:
    """A hot-state in-memory store with a controllable clock."""
    return InMemoryStateStore(clock=clock)


@pytest.fixture
async def state_service(state_store: InMemoryStateStore) -> StateService:
    """A StateService wired to the in-memory hot-state store."""
    service = StateService(store=state_store)
    await service.start()
    return service


@pytest.fixture
async def engine(store: InMemoryWorkflowStore, state_service: StateService) -> WorkflowEngine:
    """An engine with a fast step timeout and no registered workflows."""
    return WorkflowEngine(store=store, state=state_service, step_timeout=0.05)


async def test_store_satisfies_protocol(store: InMemoryWorkflowStore) -> None:
    assert isinstance(store, IWorkflowStore)


async def test_start_workflow_unknown_name_raises(engine: WorkflowEngine) -> None:
    with pytest.raises(WorkflowNotRegisteredError):
        await engine.start_workflow("registration", "guild-1", "user-1")


async def test_start_workflow_persists_first_transition(engine: WorkflowEngine, store: InMemoryWorkflowStore) -> None:
    workflow = ScriptedWorkflow("registration", registration_script())
    engine.register_workflow(workflow)
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1", {"locale": "fr"})
    state = await engine.get_state(workflow_id)
    assert state.workflow_name == "registration"
    assert state.guild_id == "guild-1"
    assert state.user_id == "user-1"
    assert state.current_step == "ask_name"
    assert state.status == WorkflowStatus.PENDING.value
    assert workflow.started_with == {"locale": "fr"}
    assert store.documents[workflow_id].status == WorkflowStatus.PENDING.value


async def test_start_workflow_mirrors_hot_state(engine: WorkflowEngine, state_service: StateService) -> None:
    engine.register_workflow(ScriptedWorkflow("registration", registration_script()))
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1")
    hot = await state_service.get_state("workflow", workflow_id)
    assert hot == {"current_step": "ask_name", "status": "PENDING"}


async def test_handle_interaction_advances_steps(
    engine: WorkflowEngine,
) -> None:
    workflow = ScriptedWorkflow("registration", registration_script())
    engine.register_workflow(workflow)
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1")

    state = await engine.handle_interaction(workflow_id, {"name": "PlayerOne"})
    assert state.current_step == "ask_game"
    assert state.status == WorkflowStatus.IN_PROGRESS.value

    state = await engine.handle_interaction(workflow_id, {"game": "aoe2"})
    assert state.current_step == "done"
    assert state.status == WorkflowStatus.COMPLETED.value
    assert workflow.interactions == [{"name": "PlayerOne"}, {"game": "aoe2"}]


async def test_handle_interaction_on_terminal_instance_is_inert(
    engine: WorkflowEngine,
) -> None:
    workflow = ScriptedWorkflow("registration", registration_script())
    engine.register_workflow(workflow)
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1")
    await engine.handle_interaction(workflow_id, {"name": "PlayerOne"})
    await engine.handle_interaction(workflow_id, {"game": "aoe2"})
    final = await engine.handle_interaction(workflow_id, {"late": True})
    assert final.status == WorkflowStatus.COMPLETED.value
    assert final.current_step == "done"
    assert workflow.interactions == [{"name": "PlayerOne"}, {"game": "aoe2"}]


async def test_handle_interaction_unknown_instance_raises(engine: WorkflowEngine) -> None:
    with pytest.raises(WorkflowNotFoundError):
        await engine.handle_interaction("registration:user-1:nope", {"name": "PlayerOne"})


async def test_cancel_marks_cancelled(engine: WorkflowEngine, state_service: StateService) -> None:
    engine.register_workflow(ScriptedWorkflow("registration", registration_script()))
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1")

    state = await engine.cancel(workflow_id)
    assert state.status == WorkflowStatus.CANCELLED.value
    state = await engine.cancel(workflow_id)
    assert state.status == WorkflowStatus.CANCELLED.value
    assert await state_service.get_state("workflow", workflow_id) is None


async def test_terminal_state_clears_hot_copy(engine: WorkflowEngine, state_service: StateService) -> None:
    engine.register_workflow(ScriptedWorkflow("registration", registration_script()))
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1")
    await engine.handle_interaction(workflow_id, {"name": "PlayerOne"})
    await engine.handle_interaction(workflow_id, {"game": "aoe2"})
    assert await state_service.get_state("workflow", workflow_id) is None


async def test_step_timeout_marks_timed_out(engine: WorkflowEngine, store: InMemoryWorkflowStore) -> None:
    import asyncio

    workflow = ScriptedWorkflow("registration", registration_script())
    engine.register_workflow(workflow)
    await engine.start_workflow("registration", "guild-1", "user-1")
    await asyncio.sleep(0.12)
    [state] = store.documents.values()
    assert state.status == WorkflowStatus.TIMED_OUT.value
    assert workflow.timeouts == 1


async def test_resume_reloads_and_rearms(store: InMemoryWorkflowStore, state_service: StateService) -> None:
    import asyncio

    engine = WorkflowEngine(store=store, state=state_service, step_timeout=10)
    engine.register_workflow(ScriptedWorkflow("registration", registration_script()))
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1")
    await engine.handle_interaction(workflow_id, {"name": "PlayerOne"})
    await engine.stop()

    engine2 = WorkflowEngine(store=store, state=state_service, step_timeout=0.05)
    engine2.register_workflow(ScriptedWorkflow("registration", registration_script()))
    await engine2.start()
    state = await engine2.resume(workflow_id)
    assert state.current_step == "ask_game"
    assert state.status == WorkflowStatus.IN_PROGRESS.value
    await asyncio.sleep(0.12)
    [state2] = [s for s in store.documents.values() if s.id == workflow_id]
    assert state2.status == WorkflowStatus.TIMED_OUT.value


async def test_startup_times_out_unknown_definition(store: InMemoryWorkflowStore, state_service: StateService) -> None:
    from kingdoms.core.models.workflow import WorkflowState

    orphan = WorkflowState(
        _id="legacy:user-1:abc",
        workflow_name="legacy",
        guild_id="guild-1",
        user_id="user-1",
        current_step="ask_name",
        status=WorkflowStatus.IN_PROGRESS.value,
    )
    await store.save(orphan)

    engine = WorkflowEngine(store=store, state=state_service, step_timeout=10)
    await engine.start()
    assert store.documents["legacy:user-1:abc"].status == WorkflowStatus.TIMED_OUT.value


async def test_idempotent_replay_after_restart(store: InMemoryWorkflowStore, state_service: StateService) -> None:
    engine = WorkflowEngine(store=store, state=state_service, step_timeout=10)
    engine.register_workflow(ScriptedWorkflow("registration", registration_script()))
    workflow_id = await engine.start_workflow("registration", "guild-1", "user-1")
    await engine.handle_interaction(workflow_id, {"name": "PlayerOne"})

    engine2 = WorkflowEngine(store=store, state=state_service, step_timeout=10)
    engine2.register_workflow(ScriptedWorkflow("registration", registration_script()))
    await engine2.start()
    state = await engine2.resume(workflow_id)
    assert state.current_step == "ask_game"
    state = await engine2.handle_interaction(workflow_id, {"name": "Replayed"})
    assert state.current_step == "done"
