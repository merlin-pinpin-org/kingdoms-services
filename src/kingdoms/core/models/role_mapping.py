"""Role mapping model: mod/game role key bound to a platform role id.

Mods and game providers reference **logical role keys** (``mod:role_key``,
e.g. ``clans:clan_leader``); the platform role id is resolved through this
mapping, never hardcoded. Admins can rebind a logical role to an existing
platform role without touching mod code.

Reference: kingdoms-services#26, ADR-0014 (RBAC permissions).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RoleMappingModel(BaseModel):
    """Persistent mapping between a mod/game role and a platform role."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    guild_id: str
    mod: str
    role_key: str
    role_id: str

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> RoleMappingModel:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
