"""StateService: hot state in Redis with MongoDB as durable backing store.

Reference: ADR-0005. Implemented in kingdoms-services#9.
"""

from __future__ import annotations

from typing import Any


class StateService:
    """Manage short-lived workflow state in Redis."""

    async def get(self, key: str) -> dict[str, Any] | None:
        """Read a state entry; returns None when missing."""
        raise NotImplementedError("Implemented in kingdoms-services#9")

    async def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        """Write a state entry with an optional TTL."""
        raise NotImplementedError("Implemented in kingdoms-services#9")
