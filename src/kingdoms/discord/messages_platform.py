"""Discord platform adapters for the message registry (kingdoms-services#147).

The live test dashboard's persistent message is addressed by its logical
key through the MessageRegistryService (#130): the registry is the only
place platform message IDs ever live. The adapters here are the narrow
Mongo (source) and StateService (cache) seams the service needs, built
the same way as the other platform adapters (logs_platform).
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.models.registered_message import RegisteredMessageModel
from kingdoms.core.services.message_registry import MESSAGES_COLLECTION
from kingdoms.core.services.state import StateService


class MongoMessagesDatabase:
    """Async MongoDB persistence for the registered platform messages."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (get_async_database)."""
        self._messages = database[MESSAGES_COLLECTION]

    async def find_message(self, platform: str, message_key: str, entity_id: str) -> RegisteredMessageModel | None:
        """Find one registered message document; None when absent."""
        document = await self._messages.find_one({"_id": f"{platform}:{message_key}:{entity_id}"})
        return RegisteredMessageModel.from_mongo(document) if document else None

    async def upsert_message(self, message: RegisteredMessageModel) -> None:
        """Insert or replace one registered message document."""
        await self._messages.replace_one({"_id": message.id}, message.to_mongo(), upsert=True)

    async def delete_message(self, platform: str, message_key: str, entity_id: str) -> bool:
        """Drop one registered message document; True when one was removed."""
        result = await self._messages.delete_one({"_id": f"{platform}:{message_key}:{entity_id}"})
        return bool(result.deleted_count > 0)


class StateMessagesCache:
    """StateService-backed cache seam for the message registry."""

    def __init__(self, state: StateService) -> None:
        """Wrap a StateService (Redis or in-memory store)."""
        self._state = state

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        """Read one cached resolution; None on miss (store may be down)."""
        return await self._state.get_state(scope, key)

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        """Write one cached resolution with a TTL (best-effort)."""
        return await self._state.set_state(scope, key, value, ttl=ttl)

    async def delete_state(self, scope: str, key: str) -> bool:
        """Drop one cached resolution (best-effort)."""
        return await self._state.delete_state(scope, key)


def build_message_registry() -> Any:
    """Build the registry from env; None when Mongo/Redis are not configured."""
    import os

    if not os.environ.get("MONGO_URI") or not os.environ.get("REDIS_URI"):
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.message_registry import MessageRegistryService
        from kingdoms.core.services.state import StateService

        state = StateService(redis_uri=os.environ["REDIS_URI"])
        return MessageRegistryService(
            database=MongoMessagesDatabase(get_async_database()),
            cache=StateMessagesCache(state),
        )
    except Exception:
        return None
