"""StateService: hot state in Redis with MongoDB as durable backing store.

The single gateway to Redis: core services and mods never issue raw Redis
commands, they call the typed accessors below. Everything written through
StateService must be reproducible from durable data (MongoDB or YAML
config): Redis loss degrades performance, never correctness.

Keys follow the ``kingdoms:{scope}:{key}`` convention (ADR-0005). The
storage seam is the structural ``IStateStore`` protocol (ADR-0011):
production wires ``RedisStateStore``, tests wire an in-memory substitute
with a controllable clock (docs/architecture/testing.md).

Reference: kingdoms-services#9, ADR-0005, docs/architecture/core.md §3.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, Protocol, runtime_checkable

from redis.asyncio import Redis
from redis.asyncio.client import PubSub

STATE_KEY_PREFIX = "kingdoms"
DEFAULT_REDIS_URI = "redis://localhost:6379"

RawStateCallback = Callable[[str], Awaitable[None]]
StateEventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


def state_key(scope: str, key: str) -> str:
    """Build a namespaced key: ``kingdoms:{scope}:{key}``."""
    return f"{STATE_KEY_PREFIX}:{scope}:{key}"


class StateServiceError(Exception):
    """A state store operation failed."""


class LockNotAcquiredError(StateServiceError):
    """A distributed lock could not be acquired."""


@runtime_checkable
class IStateStore(Protocol):
    """Narrow async storage seam the StateService depends on."""

    async def start(self) -> None:
        """Connect and verify the store is reachable."""
        ...

    async def stop(self) -> None:
        """Release the store's resources."""
        ...

    async def get(self, key: str) -> str | None:
        """Read a raw value; returns None when missing or expired."""
        ...

    async def set(self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False) -> bool:
        """Write a raw value with an optional TTL in seconds."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a raw value; returns True when a key was removed."""
        ...

    async def increment(self, key: str, window: int | None = None) -> int:
        """Increment a counter, expiring it ``window`` seconds after creation."""
        ...

    async def compare_delete(self, key: str, expected: str) -> bool:
        """Delete the key only when it still holds the expected value."""
        ...

    async def publish(self, channel: str, message: str) -> int:
        """Publish a raw message; returns the subscriber count."""
        ...

    async def subscribe(self, channel: str, callback: RawStateCallback) -> None:
        """Register a callback receiving raw messages on a channel."""
        ...

    async def unsubscribe(self, channel: str, callback: RawStateCallback) -> None:
        """Unregister a callback from a channel."""
        ...


_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class RedisStateStore:
    """IStateStore implementation backed by redis-py (``redis.asyncio``)."""

    def __init__(self, redis_uri: str | None = None) -> None:
        """Keep the URI; the client connects lazily on ``start()``."""
        self.redis_uri = redis_uri or os.environ.get("REDIS_URI", DEFAULT_REDIS_URI)
        self._client: Redis[str] | None = None
        self._pubsub: PubSub | None = None
        self._listener: asyncio.Task[None] | None = None
        self._callbacks: dict[str, list[RawStateCallback]] = {}
        self._active_channels: set[str] = set()

    async def start(self) -> None:
        """Connect the client and verify Redis is reachable."""
        if self._client is None:
            self._client = Redis.from_url(self.redis_uri, decode_responses=True)
        try:
            await self._client.ping()
        except Exception as exc:
            raise StateServiceError(f"Redis unreachable at {self.redis_uri}: {exc}") from exc

    async def stop(self) -> None:
        """Cancel the listener and close the client."""
        if self._listener is not None:
            self._listener.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None
        if self._pubsub is not None:
            await self._pubsub.close()
            self._pubsub = None
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._active_channels.clear()

    async def get(self, key: str) -> str | None:
        """Read a raw value; returns None when missing or expired."""
        if self._client is None:
            raise StateServiceError("state store not started")
        value = await self._client.get(key)
        return value if value is None else str(value)

    async def set(self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False) -> bool:
        """Write a raw value with an optional TTL in seconds."""
        if self._client is None:
            raise StateServiceError("state store not started")
        written = await self._client.set(key, value, ex=ttl, nx=only_if_absent)
        return written is not None

    async def delete(self, key: str) -> bool:
        """Delete a raw value; returns True when a key was removed."""
        if self._client is None:
            raise StateServiceError("state store not started")
        return bool(await self._client.delete(key))

    async def increment(self, key: str, window: int | None = None) -> int:
        """Increment a counter; ``window`` sets the TTL on creation only (EXPIRE NX)."""
        if self._client is None:
            raise StateServiceError("state store not started")
        count = await self._client.incr(key)
        if window is not None:
            await self._client.expire(key, window, nx=True)
        return int(count)

    async def compare_delete(self, key: str, expected: str) -> bool:
        """Atomically delete the key only when it still holds the expected value."""
        if self._client is None:
            raise StateServiceError("state store not started")
        script = self._client.register_script(_RELEASE_LOCK_LUA)
        result = await script(keys=[key], args=[expected])
        return bool(result)

    async def publish(self, channel: str, message: str) -> int:
        """Publish a raw message; returns the subscriber count."""
        if self._client is None:
            raise StateServiceError("state store not started")
        return int(await self._client.publish(channel, message))

    async def subscribe(self, channel: str, callback: RawStateCallback) -> None:
        """Register a raw-message callback; the listener starts lazily."""
        if self._client is None:
            raise StateServiceError("state store not started")
        callbacks = self._callbacks.setdefault(channel, [])
        if callback not in callbacks:
            callbacks.append(callback)
        if self._pubsub is None:
            self._pubsub = self._client.pubsub()
        if channel not in self._active_channels:
            await self._pubsub.subscribe(channel)
            self._active_channels.add(channel)
        if self._listener is None:
            self._listener = asyncio.create_task(self._listen())

    async def unsubscribe(self, channel: str, callback: RawStateCallback) -> None:
        """Unregister a raw-message callback and drop idle subscriptions."""
        callbacks = self._callbacks.get(channel, [])
        if callback in callbacks:
            callbacks.remove(callback)
        if callbacks:
            return
        self._callbacks.pop(channel, None)
        if channel in self._active_channels and self._pubsub is not None:
            await self._pubsub.unsubscribe(channel)
            self._active_channels.discard(channel)

    async def _listen(self) -> None:
        """Dispatch published messages to the registered callbacks."""
        if self._pubsub is None:
            return
        while True:
            message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None or message.get("type") != "message":
                continue
            channel = str(message["channel"])
            data = str(message["data"])
            for callback in list(self._callbacks.get(channel, [])):
                await callback(data)


class StateService:
    """Typed gateway to hot state; the only entry point to Redis."""

    def __init__(self, store: IStateStore | None = None, redis_uri: str | None = None) -> None:
        """Use the injected store, or a ``RedisStateStore`` built from ``redis_uri``/``REDIS_URI``."""
        if store is None:
            store = RedisStateStore(redis_uri)
        self._store = store
        self._event_callbacks: dict[str, list[StateEventCallback]] = {}
        self._dispatchers: dict[str, RawStateCallback] = {}

    async def start(self) -> None:
        """Connect the underlying store."""
        await self._store.start()

    async def close(self) -> None:
        """Release the underlying store's resources."""
        await self._store.stop()

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        """Read a hot-state entry; returns None when missing or expired."""
        raw = await self._store.get(state_key(scope, key))
        return json.loads(raw) if raw is not None else None

    async def set_state(
        self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None
    ) -> None:
        """Write a hot-state entry with an optional TTL in seconds."""
        await self._store.set(state_key(scope, key), json.dumps(value), ttl=ttl)

    async def delete_state(self, scope: str, key: str) -> bool:
        """Delete a hot-state entry; returns True when a key was removed."""
        return await self._store.delete(state_key(scope, key))

    async def acquire_lock(self, scope: str, key: str, ttl: int = 30) -> str | None:
        """Acquire a distributed lock; returns its token, or None when held."""
        token = uuid.uuid4().hex
        acquired = await self._store.set(state_key(scope, f"lock:{key}"), token, ttl=ttl, only_if_absent=True)
        return token if acquired else None

    async def release_lock(self, scope: str, key: str, token: str) -> bool:
        """Release a lock; only the token returned by ``acquire_lock`` succeeds."""
        return await self._store.compare_delete(state_key(scope, f"lock:{key}"), token)

    @asynccontextmanager
    async def lock(self, scope: str, key: str, ttl: int = 30) -> AsyncIterator[None]:
        """Hold a distributed lock for the duration of the context."""
        token = await self.acquire_lock(scope, key, ttl)
        if token is None:
            raise LockNotAcquiredError(state_key(scope, f"lock:{key}"))
        try:
            yield
        finally:
            await self.release_lock(scope, key, token)

    async def check_rate_limit(self, scope: str, key: str, limit: int, window: int) -> bool:
        """Count a hit against a fixed-window rate limit; True when still allowed."""
        count = await self._store.increment(state_key(scope, f"ratelimit:{key}"), window=window)
        return count <= limit

    async def publish(self, scope: str, event: str, payload: dict[str, Any]) -> int:
        """Publish an event to the scope's channel; returns the subscriber count."""
        channel = state_key(scope, "events")
        message = json.dumps({"event": event, "payload": payload})
        await self._store.publish(channel, message)
        return len(self._event_callbacks.get(channel, []))

    async def subscribe(self, scope: str, callback: StateEventCallback) -> None:
        """Register an async callback for events published in a scope."""
        channel = state_key(scope, "events")
        callbacks = self._event_callbacks.setdefault(channel, [])
        if callback not in callbacks:
            callbacks.append(callback)
        if channel not in self._dispatchers:
            dispatcher = self._make_dispatcher(channel)
            self._dispatchers[channel] = dispatcher
            await self._store.subscribe(channel, dispatcher)

    async def unsubscribe(self, scope: str, callback: StateEventCallback) -> None:
        """Unregister an event callback and drop the raw subscription when idle."""
        channel = state_key(scope, "events")
        callbacks = self._event_callbacks.get(channel, [])
        if callback in callbacks:
            callbacks.remove(callback)
        if callbacks:
            return
        self._event_callbacks.pop(channel, None)
        dispatcher = self._dispatchers.pop(channel, None)
        if dispatcher is not None:
            await self._store.unsubscribe(channel, dispatcher)

    def _make_dispatcher(self, channel: str) -> RawStateCallback:
        """Build the raw-message callback decoding the event envelope."""

        async def dispatch(raw: str) -> None:
            envelope = json.loads(raw)
            event = str(envelope["event"])
            payload = dict(envelope["payload"])
            for callback in list(self._event_callbacks.get(channel, [])):
                await callback(event, payload)

        return dispatch
