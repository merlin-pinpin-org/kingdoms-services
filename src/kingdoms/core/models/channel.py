"""Channel model: channel registry keyed by mod-scoped channel category.

Reference: kingdoms-services#4.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChannelModel(BaseModel):
    """Channel registry entry."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    guild_id: str
    platform: str
    category: str
    channel_id: str
    name: str

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> ChannelModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
