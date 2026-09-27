"""Workflow exceptions (kingdoms-services#10).

Workflow failures are user-visible by nature (a registration that
cannot proceed, a step that does not exist), so the taxonomy is
explicit: each failure names its workflow and, when known, its step.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.exceptions.base import ErrorContext, KingdomsError

__all__ = [
    "InvalidWorkflowStepError",
    "WorkflowAlreadyCompletedError",
    "WorkflowError",
    "WorkflowNotFoundError",
    "WorkflowTimeoutError",
]


def _context(**data: Any) -> ErrorContext:
    """Build a context keeping only the non-empty entries."""
    return ErrorContext(data={k: v for k, v in data.items() if v})


class WorkflowError(KingdomsError):
    """Base error for workflow failures."""

    code = "WORKFLOW_ERROR"

    def __init__(self, message: str, workflow_id: str = "", step: str = "") -> None:
        """Create the error, carrying the workflow and step ids."""
        super().__init__(message, _context(workflow_id=workflow_id, step=step))


class WorkflowNotFoundError(WorkflowError):
    """Raised when no instance exists for the workflow id."""

    def __init__(self, workflow_id: str) -> None:
        """Create the error for one missing workflow instance."""
        super().__init__(f"Workflow not found: {workflow_id}", workflow_id=workflow_id)


class WorkflowAlreadyCompletedError(WorkflowError):
    """Raised when trying to resume a completed workflow."""

    def __init__(self, workflow_id: str) -> None:
        """Create the error for one completed workflow."""
        super().__init__(f"Workflow already completed: {workflow_id}", workflow_id=workflow_id)


class WorkflowTimeoutError(WorkflowError):
    """Raised when a workflow times out."""

    def __init__(self, workflow_id: str, timeout: int) -> None:
        """Create the error, carrying the timeout value."""
        super().__init__(f"Workflow timed out after {timeout}s: {workflow_id}", workflow_id=workflow_id)
        self.context = self.context.extend(timeout=timeout)


class InvalidWorkflowStepError(WorkflowError):
    """Raised when an invalid workflow step is encountered."""

    def __init__(self, workflow_id: str, step: str, expected_steps: list[str]) -> None:
        """Create the error, naming the expected steps."""
        super().__init__(
            f"Invalid step '{step}' for workflow {workflow_id}. Expected one of: {', '.join(expected_steps)}",
            workflow_id=workflow_id,
            step=step,
        )
        self.context = self.context.extend(expected_steps=", ".join(expected_steps))
