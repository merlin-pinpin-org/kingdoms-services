"""Unit tests for the MessageRegistryService (kingdoms-services#130).

Covers register/resolve/forget round-trips, cache-aside behaviour,
replacement on re-registration and cache-failure degradation.
"""

from __future__ import annotations

from kingdoms.core.models.registered_message import RegisteredMessageModel
from kingdoms.core.services.message_registry import MessageRegistryService
from kingdoms.core.services.state import StateService
from tests.mocks.state_mock import FakeClock, InMemoryStateStore


class FakeMessagesDatabase:
    """In-memory MessagesDatabase: documents keyed by platform:key:entity."""

    def __init__(self) -> None:
        self.messages: dict[str, RegisteredMessageModel] = {}
        self.upserts: list[RegisteredMessageModel] = []
        self.deletes: list[str] = []

    async def find_message(self, platform: str, message_key: str, entity_id: str) -> RegisteredMessageModel | None:
        return self.messages.get(f"{platform}:{message_key}:{entity_id}")

    async def upsert_message(self, message: RegisteredMessageModel) -> None:
        self.messages[message.id] = message
        self.upserts.append(message)

    async def delete_message(self, platform: str, message_key: str, entity_id: str) -> bool:
        key = f"{platform}:{message_key}:{entity_id}"
        existed = key in self.messages
        if existed:
            self.deletes.append(key)
            del self.messages[key]
        return existed


class BrokenCache:
    """A cache seam that always fails — the service must degrade."""

    async def get_state(self, scope: str, key: str) -> dict[str, object] | None:
        raise RuntimeError("cache down")

    async def set_state(self, scope: str, key: str, value: dict[str, object], ttl: int) -> None:
        raise RuntimeError("cache down")

    async def delete_state(self, scope: str, key: str) -> bool:
        raise RuntimeError("cache down")


def make_service(cache: object | None = None) -> tuple[MessageRegistryService, FakeMessagesDatabase]:
    """Build the service with an in-memory database and a cache."""
    db = FakeMessagesDatabase()
    if cache is None:
        clock = FakeClock()
        cache = StateService(InMemoryStateStore(clock=clock))
    return MessageRegistryService(database=db, cache=cache), db


class TestRegisterAndResolve:
    async def test_register_then_resolve_round_trip(self) -> None:
        service, db = make_service()
        await service.register(
            platform="discord",
            message_key="ladder:panel",
            entity_id="42",
            channel_id="ch1",
            message_id="msg1",
            guild_id="g1",
        )
        resolved = await service.resolve("discord", "ladder:panel", "42")
        assert resolved is not None
        assert resolved.message_id == "msg1"
        assert resolved.channel_id == "ch1"
        assert db.messages["discord:ladder:panel:42"].message_id == "msg1"

    async def test_resolve_unknown_returns_none(self) -> None:
        service, _db = make_service()
        assert await service.resolve("discord", "ladder:panel", "nope") is None

    async def test_reregistration_replaces_the_message(self) -> None:
        service, db = make_service()
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="42", channel_id="ch1", message_id="old"
        )
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="42", channel_id="ch1", message_id="new"
        )
        resolved = await service.resolve("discord", "ladder:panel", "42")
        assert resolved is not None
        assert resolved.message_id == "new"
        assert db.messages["discord:ladder:panel:42"].message_id == "new"

    async def test_keys_are_isolated_per_entity_and_platform(self) -> None:
        service, _db = make_service()
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="1", channel_id="c", message_id="m1"
        )
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="2", channel_id="c", message_id="m2"
        )
        first = await service.resolve("discord", "ladder:panel", "1")
        second = await service.resolve("discord", "ladder:panel", "2")
        assert first is not None and first.message_id == "m1"
        assert second is not None and second.message_id == "m2"
        assert await service.resolve("other", "ladder:panel", "1") is None


class TestCacheAside:
    async def test_second_resolve_uses_the_cache_not_the_db(self) -> None:
        service, db = make_service()
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="42", channel_id="ch1", message_id="msg1"
        )
        db.messages.clear()
        resolved = await service.resolve("discord", "ladder:panel", "42")
        assert resolved is not None
        assert resolved.message_id == "msg1"

    async def test_broken_cache_degrades_to_the_database(self) -> None:
        service, db = make_service(cache=BrokenCache())
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="42", channel_id="ch1", message_id="msg1"
        )
        resolved = await service.resolve("discord", "ladder:panel", "42")
        assert resolved is not None
        assert resolved.message_id == "msg1"
        assert db.messages["discord:ladder:panel:42"].message_id == "msg1"


class TestForget:
    async def test_forget_removes_the_registration(self) -> None:
        service, _db = make_service()
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="42", channel_id="ch1", message_id="msg1"
        )
        assert await service.forget("discord", "ladder:panel", "42") is True
        assert await service.resolve("discord", "ladder:panel", "42") is None
        assert await service.forget("discord", "ladder:panel", "42") is False

    async def test_forget_invalidates_the_cache(self) -> None:
        service, db = make_service()
        await service.register(
            platform="discord", message_key="ladder:panel", entity_id="42", channel_id="ch1", message_id="msg1"
        )
        first = await service.resolve("discord", "ladder:panel", "42")
        assert first is not None and first.message_id == "msg1"
        await service.forget("discord", "ladder:panel", "42")
        db.messages["discord:ladder:panel:42"] = RegisteredMessageModel(
            _id="discord:ladder:panel:42",
            platform="discord",
            message_key="ladder:panel",
            entity_id="42",
            channel_id="ch1",
            message_id="stale",
        )
        resolved = await service.resolve("discord", "ladder:panel", "42")
        assert resolved is not None
        assert resolved.message_id == "stale"
        db.messages.clear()
        db.messages["discord:ladder:panel:99"] = RegisteredMessageModel(
            _id="discord:ladder:panel:99",
            platform="discord",
            message_key="ladder:panel",
            entity_id="99",
            channel_id="ch1",
            message_id="other",
        )
        other = await service.resolve("discord", "ladder:panel", "99")
        assert other is not None and other.message_id == "other"
