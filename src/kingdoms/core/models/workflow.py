"""Workflow status, transition and persisted state models.

Reference: kingdoms-services#4, kingdoms-services#6 and ADR-0002
(workflow engine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStatus(StrEnum):
    """Lifecycle of a workflow instance (WORKFLOWS.md, ADR-0002)."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True, slots=True)
class WorkflowTransition:
    """Result of one workflow execution, persisted by the WorkflowEngine.

    Workflows are stateless executors: they receive the instance state in
    the event envelope and return the next transition. The engine owns
    persistence, so transitions survive restarts by construction.
    """

    current_step: str
    status: WorkflowStatus
    payload: dict[str, Any] = field(default_factory=dict)


class WorkflowState(BaseModel):
    """A persisted workflow instance so flows survive restarts."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    workflow_name: str
    guild_id: str
    user_id: str
    current_step: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def as_transition(self) -> WorkflowTransition:
        """View the persisted state as the transition to resume from."""
        return WorkflowTransition(
            current_step=self.current_step,
            status=WorkflowStatus(self.status),
            payload=dict(self.payload),
        )

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> WorkflowState:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
