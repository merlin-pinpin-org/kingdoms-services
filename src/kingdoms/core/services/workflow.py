"""WorkflowEngine: workflow execution.

Loads workflow definitions (``IWorkflow``), drives step transitions under a
distributed lock, persists every transition as a durable ``WorkflowState``
document (MongoDB) with a hot copy in Redis, enforces per-step timeouts and
resumes in-progress instances on startup.

Workflows are stateless executors: they receive the current state and
return the next ``WorkflowTransition``; the engine owns persistence, so
flows survive restarts by construction (ADR-0002, docs/architecture/core.md §1).

Storage seams are structural protocols (ADR-0011): production wires
``MongoWorkflowStore`` and ``RedisStateStore``, tests wire in-memory
substitutes (docs/architecture/testing.md).

Reference: kingdoms-services#6, ADR-0002.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Protocol, runtime_checkable

from kingdoms.core.interfaces.platform import IWorkflow
from kingdoms.core.models.workflow import WorkflowState, WorkflowStatus, WorkflowTransition
from kingdoms.core.services.state import StateService

HOT_STATE_SCOPE = "workflow"
HOT_TTL_GRACE_SECONDS = 300


@runtime_checkable
class IWorkflowStore(Protocol):
    """Durable persistence seam for workflow instances."""

    async def start(self) -> None:
        """Connect to the durable store."""
        ...

    async def stop(self) -> None:
        """Release the durable store's resources."""
        ...

    async def save(self, state: WorkflowState) -> None:
        """Insert or update a workflow instance document."""
        ...

    async def get(self, workflow_id: str) -> WorkflowState | None:
        """Read a workflow instance; returns None when unknown."""
        ...

    async def delete(self, workflow_id: str) -> bool:
        """Delete an instance; returns True when a document was removed."""
        ...

    async def list_by_status(self, status: WorkflowStatus) -> list[WorkflowState]:
        """List instances in the given status (restart recovery scan)."""
        ...


class WorkflowNotFoundError(Exception):
    """No instance exists for the given workflow ID."""


class WorkflowNotRegisteredError(Exception):
    """No workflow definition is registered under the requested name."""


class WorkflowEngine:
    """Execute registered workflows and persist their state."""

    def __init__(
        self,
        store: IWorkflowStore,
        state: StateService,
        step_timeout: float = 180.0,
    ) -> None:
        """Wire the durable store, the hot-state service and the step timeout."""
        self._store = store
        self._state = state
        self._step_timeout = step_timeout
        self._workflows: dict[str, IWorkflow] = {}
        self._timeout_tasks: dict[str, asyncio.TimerHandle] = {}

    async def start(self) -> None:
        """Connect the store and resume in-progress instances (never drop them)."""
        await self._store.start()
        await self._resume_pending()

    async def stop(self) -> None:
        """Cancel step timeouts and release the store."""
        for handle in self._timeout_tasks.values():
            handle.cancel()
        self._timeout_tasks.clear()
        await self._store.stop()

    def register_workflow(self, workflow: IWorkflow) -> None:
        """Register a workflow definition under its name."""
        self._workflows[workflow.name] = workflow

    @property
    def step_timeout(self) -> float:
        """Seconds of inactivity before a step times out."""
        return self._step_timeout

    async def start_workflow(
        self, workflow_name: str, guild_id: str, user_id: str, context: dict[str, Any] | None = None
    ) -> str:
        """Start a workflow instance for a user; returns its ID."""
        workflow = self._workflows.get(workflow_name)
        if workflow is None:
            raise WorkflowNotRegisteredError(workflow_name)
        workflow_id = f"{workflow_name}:{user_id}:{uuid.uuid4().hex[:12]}"
        transition = await workflow.start(context or {})
        await self._apply(workflow_id, workflow_name, guild_id, user_id, transition)
        return workflow_id

    async def resume(self, workflow_id: str) -> WorkflowState:
        """Reload an instance from the durable store and restart its step timeout."""
        state = await self._require(workflow_id)
        if state.status not in (WorkflowStatus.PENDING.value, WorkflowStatus.IN_PROGRESS.value):
            return state
        self._arm_timeout(workflow_id)
        return state

    async def handle_interaction(self, workflow_id: str, event: dict[str, Any]) -> WorkflowState:
        """Dispatch an interaction to the running instance under its lock."""
        return await self._drive(workflow_id, lambda wf, state: wf.handle_interaction(state, event))

    async def cancel(self, workflow_id: str) -> WorkflowState:
        """Mark a non-terminal instance CANCELLED and stop its timeout."""
        state = await self._require(workflow_id)
        if state.status in WorkflowEngine._TERMINAL_STATUSES:
            return state
        transition = WorkflowTransition(
            current_step=state.current_step,
            status=WorkflowStatus.CANCELLED,
            payload=dict(state.payload),
        )
        return await self._apply(workflow_id, state.workflow_name, state.guild_id, state.user_id, transition)

    async def get_state(self, workflow_id: str) -> WorkflowState:
        """Read the durable state of an instance."""
        return await self._require(workflow_id)

    _TERMINAL_STATUSES = frozenset(
        {
            WorkflowStatus.COMPLETED.value,
            WorkflowStatus.FAILED.value,
            WorkflowStatus.CANCELLED.value,
            WorkflowStatus.TIMED_OUT.value,
        }
    )

    async def _require(self, workflow_id: str) -> WorkflowState:
        """Load an instance or raise WorkflowNotFoundError."""
        state = await self._store.get(workflow_id)
        if state is None:
            raise WorkflowNotFoundError(workflow_id)
        return state

    async def _drive(
        self,
        workflow_id: str,
        drive: Any,
    ) -> WorkflowState:
        """Apply a transition under the workflow's distributed lock."""
        async with self._state.lock(HOT_STATE_SCOPE, workflow_id):
            state = await self._require(workflow_id)
            if state.status in WorkflowEngine._TERMINAL_STATUSES:
                return state
            workflow = self._require_definition(state.workflow_name)
            transition = await drive(workflow, state.as_transition())
            return await self._apply(
                workflow_id, state.workflow_name, state.guild_id, state.user_id, transition
            )

    async def _apply(
        self,
        workflow_id: str,
        workflow_name: str,
        guild_id: str,
        user_id: str,
        transition: WorkflowTransition,
    ) -> WorkflowState:
        """Persist a transition durably, mirror it hot, and manage timeouts."""
        state = WorkflowState(
            _id=workflow_id,
            workflow_name=workflow_name,
            guild_id=guild_id,
            user_id=user_id,
            current_step=transition.current_step,
            status=transition.status.value,
            payload=transition.payload,
        )
        await self._store.save(state)
        await self._state.set_state(
            HOT_STATE_SCOPE,
            workflow_id,
            {"current_step": transition.current_step, "status": transition.status.value},
            ttl=int(self._step_timeout) + HOT_TTL_GRACE_SECONDS,
        )
        if transition.status in (WorkflowStatus.PENDING, WorkflowStatus.IN_PROGRESS):
            self._arm_timeout(workflow_id)
        else:
            self._disarm_timeout(workflow_id)
            await self._state.delete_state(HOT_STATE_SCOPE, workflow_id)
        return state

    def _require_definition(self, workflow_name: str) -> IWorkflow:
        """Load a workflow definition or raise WorkflowNotRegisteredError."""
        workflow = self._workflows.get(workflow_name)
        if workflow is None:
            raise WorkflowNotRegisteredError(workflow_name)
        return workflow

    def _arm_timeout(self, workflow_id: str) -> None:
        """(Re)start the step timeout for a live instance."""
        self._disarm_timeout(workflow_id)
        loop = asyncio.get_running_loop()
        handle = loop.call_later(self._step_timeout, lambda: asyncio.create_task(self._timeout(workflow_id)))
        self._timeout_tasks[workflow_id] = handle

    def _disarm_timeout(self, workflow_id: str) -> None:
        """Cancel the pending step timeout of an instance, if any."""
        handle = self._timeout_tasks.pop(workflow_id, None)
        if handle is not None:
            handle.cancel()

    async def _timeout(self, workflow_id: str) -> None:
        """Apply the workflow's timeout transition when the step timer fires."""
        self._timeout_tasks.pop(workflow_id, None)
        try:
            await self._drive(workflow_id, lambda wf, state: wf.on_timeout(state))
        except (WorkflowNotFoundError, WorkflowNotRegisteredError):
            return

    async def _resume_pending(self) -> None:
        """Resume PENDING and IN_PROGRESS instances found on startup."""
        for status in (WorkflowStatus.PENDING, WorkflowStatus.IN_PROGRESS):
            for state in await self._store.list_by_status(status):
                if self._require_definition_optional(state.workflow_name) is None:
                    transition = WorkflowTransition(
                        current_step=state.current_step,
                        status=WorkflowStatus.TIMED_OUT,
                        payload=dict(state.payload),
                    )
                    await self._apply(
                        state.id, state.workflow_name, state.guild_id, state.user_id, transition
                    )
                else:
                    self._arm_timeout(state.id)

    def _require_definition_optional(self, workflow_name: str) -> IWorkflow | None:
        """Return a workflow definition or None when it is not registered."""
        return self._workflows.get(workflow_name)


class MongoWorkflowStore:
    """IWorkflowStore implementation backed by MongoDB (``workflow_states``)."""

    COLLECTION = "workflow_states"

    def __init__(self, database: Any) -> None:
        """Keep the database handle; the collection is accessed lazily."""
        self._database = database

    async def start(self) -> None:
        """Ensure the collection and its status index exist."""
        await self._collection.create_index("status")

    async def stop(self) -> None:
        """Nothing to release: the shared client owns the connection."""

    @property
    def _collection(self) -> Any:
        return self._database[self.COLLECTION]

    async def save(self, state: WorkflowState) -> None:
        """Upsert the instance document."""
        await self._collection.update_one(
            {"_id": state.id}, {"$set": state.to_mongo()}, upsert=True
        )

    async def get(self, workflow_id: str) -> WorkflowState | None:
        """Read an instance document."""
        document = await self._collection.find_one({"_id": workflow_id})
        return WorkflowState.from_mongo(document) if document else None

    async def delete(self, workflow_id: str) -> bool:
        """Delete an instance document."""
        result = await self._collection.delete_one({"_id": workflow_id})
        return bool(getattr(result, "deleted_count", 0))

    async def list_by_status(self, status: WorkflowStatus) -> list[WorkflowState]:
        """List instance documents in the given status."""
        cursor = self._collection.find({"status": status.value})
        return [WorkflowState.from_mongo(document) async for document in cursor]
