"""Platform message registry model (kingdoms-services#130).

The single place where platform message IDs ever live: keyed by
``(platform, message_key, entity_id)``. Mod collections never store
platform message/channel IDs (reference §2.3) — they reference this
registry by logical key instead.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RegisteredMessageModel(BaseModel):
    """A persistent platform message, addressable by logical key."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    platform: str
    message_key: str
    entity_id: str
    channel_id: str
    message_id: str
    guild_id: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> RegisteredMessageModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
