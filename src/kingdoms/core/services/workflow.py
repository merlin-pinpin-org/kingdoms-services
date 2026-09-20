"""WorkflowEngine: workflow execution.

Loads workflow definitions, drives step transitions, persists state through
StateService and dispatches UI events. Implemented in kingdoms-services#6.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.interfaces.platform import IWorkflow


class WorkflowEngine:
    """Execute workflows and persist their state."""

    async def start_workflow(self, workflow: IWorkflow, context: dict[str, Any]) -> None:
        """Start a workflow with an initial context."""
        raise NotImplementedError("Implemented in kingdoms-services#6")

    async def handle_interaction(self, event: dict[str, Any]) -> None:
        """Dispatch an interaction event to the right running workflow."""
        raise NotImplementedError("Implemented in kingdoms-services#6")
