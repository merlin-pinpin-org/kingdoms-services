"""Unit tests for StateService (kingdoms-services#9).

Uses the in-memory IStateStore substitute with a controllable clock; the
RedisStateStore path itself is exercised by CI against real Redis (docker
compose), per docs/architecture/testing.md.
"""

from __future__ import annotations

import pytest

from kingdoms.core.services.state import (
    IStateStore,
    LockNotAcquiredError,
    RedisStateStore,
    StateService,
    state_key,
)
from tests.mocks.state_mock import FakeClock, InMemoryStateStore, any_event_callback


@pytest.fixture
def clock() -> FakeClock:
    """A controllable clock starting at zero."""
    return FakeClock()


@pytest.fixture
def store(clock: FakeClock) -> InMemoryStateStore:
    """A started in-memory state store."""
    return InMemoryStateStore(clock=clock)


@pytest.fixture
async def service(store: InMemoryStateStore) -> StateService:
    """A StateService wired to the in-memory store."""
    svc = StateService(store=store)
    await svc.start()
    return svc


def test_in_memory_store_satisfies_protocol(store: InMemoryStateStore) -> None:
    assert isinstance(store, IStateStore)


def test_redis_store_satisfies_protocol() -> None:
    assert isinstance(RedisStateStore("redis://localhost:6379"), IStateStore)


def test_state_key_namespace() -> None:
    assert state_key("workflow", "wf-1") == "kingdoms:workflow:wf-1"


async def test_set_and_get_state(service: StateService, store: InMemoryStateStore) -> None:
    await service.set_state("workflow", "wf-1", {"step": "ask_name"})
    assert await service.get_state("workflow", "wf-1") == {"step": "ask_name"}
    assert "kingdoms:workflow:wf-1" in store.raw_entries()


async def test_get_state_missing_returns_none(service: StateService) -> None:
    assert await service.get_state("workflow", "missing") is None


async def test_set_state_ttl_expires(service: StateService, clock: FakeClock, store: InMemoryStateStore) -> None:
    await service.set_state("workflow", "wf-1", {"step": "ask_name"}, ttl=60)
    clock.advance(59)
    assert await service.get_state("workflow", "wf-1") == {"step": "ask_name"}
    clock.advance(2)
    assert await service.get_state("workflow", "wf-1") is None
    assert "kingdoms:workflow:wf-1" not in store.raw_entries()


async def test_delete_state(service: StateService) -> None:
    await service.set_state("workflow", "wf-1", {"step": "ask_name"})
    assert await service.delete_state("workflow", "wf-1") is True
    assert await service.delete_state("workflow", "wf-1") is False
    assert await service.get_state("workflow", "wf-1") is None


async def test_scopes_are_isolated(service: StateService) -> None:
    await service.set_state("workflow", "shared", {"a": 1})
    await service.set_state("ladder", "shared", {"b": 2})
    assert await service.get_state("workflow", "shared") == {"a": 1}
    assert await service.get_state("ladder", "shared") == {"b": 2}


async def test_acquire_lock_is_exclusive(service: StateService) -> None:
    token = await service.acquire_lock("workflow", "wf-1", ttl=30)
    assert token is not None
    assert await service.acquire_lock("workflow", "wf-1", ttl=30) is None


async def test_release_lock_allows_reacquire(service: StateService) -> None:
    token = await service.acquire_lock("workflow", "wf-1", ttl=30)
    assert token is not None
    assert await service.release_lock("workflow", "wf-1", token) is True
    assert await service.acquire_lock("workflow", "wf-1", ttl=30) is not None


async def test_release_lock_rejects_foreign_token(service: StateService) -> None:
    token = await service.acquire_lock("workflow", "wf-1", ttl=30)
    assert token is not None
    assert await service.release_lock("workflow", "wf-1", "not-the-token") is False
    assert await service.acquire_lock("workflow", "wf-1", ttl=30) is None


async def test_lock_expires_with_ttl(service: StateService, clock: FakeClock) -> None:
    token = await service.acquire_lock("workflow", "wf-1", ttl=30)
    assert token is not None
    clock.advance(31)
    assert await service.acquire_lock("workflow", "wf-1", ttl=30) is not None


async def test_lock_context_manager(service: StateService) -> None:
    async with service.lock("workflow", "wf-1", ttl=30):
        assert await service.acquire_lock("workflow", "wf-1", ttl=30) is None
    assert await service.acquire_lock("workflow", "wf-1", ttl=30) is not None


async def test_lock_context_manager_raises_when_held(service: StateService) -> None:
    held = await service.acquire_lock("workflow", "wf-1", ttl=30)
    assert held is not None
    with pytest.raises(LockNotAcquiredError):
        async with service.lock("workflow", "wf-1", ttl=30):
            pass


async def test_rate_limit_allows_under_limit(service: StateService) -> None:
    for _ in range(3):
        assert await service.check_rate_limit("mod", "cmd:user-1", limit=3, window=60) is True
    assert await service.check_rate_limit("mod", "cmd:user-1", limit=3, window=60) is False


async def test_rate_limit_window_resets(service: StateService, clock: FakeClock) -> None:
    for _ in range(3):
        assert await service.check_rate_limit("mod", "cmd:user-1", limit=3, window=60) is True
    assert await service.check_rate_limit("mod", "cmd:user-1", limit=3, window=60) is False
    clock.advance(61)
    assert await service.check_rate_limit("mod", "cmd:user-1", limit=3, window=60) is True


async def test_rate_limit_keys_are_independent(service: StateService) -> None:
    assert await service.check_rate_limit("mod", "cmd:user-1", limit=1, window=60) is True
    assert await service.check_rate_limit("mod", "cmd:user-2", limit=1, window=60) is True
    assert await service.check_rate_limit("mod", "cmd:user-1", limit=1, window=60) is False


async def test_publish_subscribe(service: StateService) -> None:
    received: list[tuple[str, dict[str, object]]] = []
    callback = any_event_callback(received)
    await service.subscribe("workflow", callback)
    subscribers = await service.publish("workflow", "step.done", {"step": "ask_name"})
    assert subscribers == 1
    assert received == [("step.done", {"step": "ask_name"})]


async def test_publish_without_subscribers(service: StateService) -> None:
    assert await service.publish("workflow", "step.done", {"step": "ask_name"}) == 0


async def test_multiple_subscribers_receive_events(service: StateService) -> None:
    first: list[tuple[str, dict[str, object]]] = []
    second: list[tuple[str, dict[str, object]]] = []
    await service.subscribe("workflow", any_event_callback(first))
    await service.subscribe("workflow", any_event_callback(second))
    subscribers = await service.publish("workflow", "step.done", {"step": "ask_name"})
    assert subscribers == 2
    assert first == second == [("step.done", {"step": "ask_name"})]


async def test_unsubscribe_stops_delivery(service: StateService) -> None:
    received: list[tuple[str, dict[str, object]]] = []
    callback = any_event_callback(received)
    await service.subscribe("workflow", callback)
    await service.unsubscribe("workflow", callback)
    assert await service.publish("workflow", "step.done", {"step": "ask_name"}) == 0
    assert received == []


async def test_scoped_events_are_isolated(service: StateService) -> None:
    received: list[tuple[str, dict[str, object]]] = []
    await service.subscribe("workflow", any_event_callback(received))
    await service.publish("ladder", "step.done", {"step": "ask_name"})
    assert received == []
