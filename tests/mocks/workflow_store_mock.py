"""In-memory IWorkflowStore substitute for core tests (kingdoms-services#6).

Same interface as ``MongoWorkflowStore`` without a server. See
docs/architecture/testing.md (in-memory MongoDB substitute).
"""

from __future__ import annotations

from kingdoms.core.models.workflow import WorkflowState, WorkflowStatus


class InMemoryWorkflowStore:
    """Structural IWorkflowStore implementation backed by a dict."""

    def __init__(self) -> None:
        """Start with an empty store."""
        self.documents: dict[str, WorkflowState] = {}
        self.started = False

    async def start(self) -> None:
        """Mark the store as started."""
        self.started = True

    async def stop(self) -> None:
        """Mark the store as stopped."""
        self.started = False

    async def save(self, state: WorkflowState) -> None:
        """Insert or update a workflow instance document."""
        self.documents[state.id] = state

    async def get(self, workflow_id: str) -> WorkflowState | None:
        """Read a workflow instance; returns None when unknown."""
        return self.documents.get(workflow_id)

    async def delete(self, workflow_id: str) -> bool:
        """Delete an instance; returns True when a document was removed."""
        return self.documents.pop(workflow_id, None) is not None

    async def list_by_status(self, status: WorkflowStatus) -> list[WorkflowState]:
        """List instances in the given status."""
        return [state for state in self.documents.values() if state.status == status.value]
