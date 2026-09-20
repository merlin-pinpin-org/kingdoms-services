"""Workflow state model: persisted workflow instances.

Reference: kingdoms-services#4 and ADR-0002 (workflow engine).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> WorkflowState:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
