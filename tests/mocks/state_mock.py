"""In-memory IStateStore substitute for core tests (kingdoms-services#9).

Same interface as ``RedisStateStore`` with TTLs simulated by a controllable
clock: tests advance time explicitly instead of sleeping. Published events
are dispatched synchronously to registered callbacks, so pub/sub tests are
deterministic. See docs/architecture/testing.md (in-memory Redis
substitute).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kingdoms.core.services.state import RawStateCallback

ClockAdvance = Callable[[float], None]


class FakeClock:
    """Monotonic test clock in whole-second resolution."""

    def __init__(self) -> None:
        """Start at time zero."""
        self.now: float = 0.0

    def advance(self, seconds: float) -> None:
        """Move time forward by ``seconds``."""
        self.now += seconds


class InMemoryStateStore:
    """Structural IStateStore implementation: a dict with expiries and callbacks."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        """Wire the optional clock; a private one is created otherwise."""
        self.clock = clock or FakeClock()
        self.started = False
        self.values: dict[str, str] = {}
        self.expiries: dict[str, float] = {}
        self.channels: dict[str, list[RawStateCallback]] = {}

    def _expire(self, key: str) -> None:
        """Drop the key when its TTL elapsed."""
        deadline = self.expiries.get(key)
        if deadline is not None and self.clock.now >= deadline:
            self.values.pop(key, None)
            self.expiries.pop(key, None)

    async def start(self) -> None:
        """Mark the store as started."""
        self.started = True

    async def stop(self) -> None:
        """Mark the store as stopped."""
        self.started = False

    async def get(self, key: str) -> str | None:
        """Read a raw value; returns None when missing or expired."""
        self._expire(key)
        return self.values.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False) -> bool:
        """Write a raw value with an optional TTL in seconds."""
        self._expire(key)
        if only_if_absent and key in self.values:
            return False
        self.values[key] = value
        if ttl is not None:
            self.expiries[key] = self.clock.now + ttl
        else:
            self.expiries.pop(key, None)
        return True

    async def delete(self, key: str) -> bool:
        """Delete a raw value; returns True when a key was removed."""
        self._expire(key)
        self.expiries.pop(key, None)
        return self.values.pop(key, None) is not None

    async def increment(self, key: str, window: int | None = None) -> int:
        """Increment a counter, expiring it ``window`` seconds after creation."""
        self._expire(key)
        count = int(self.values.get(key, "0")) + 1
        self.values[key] = str(count)
        if window is not None and key not in self.expiries:
            self.expiries[key] = self.clock.now + window
        return count

    async def compare_delete(self, key: str, expected: str) -> bool:
        """Delete the key only when it still holds the expected value."""
        self._expire(key)
        if self.values.get(key) == expected:
            self.values.pop(key, None)
            self.expiries.pop(key, None)
            return True
        return False

    async def publish(self, channel: str, message: str) -> int:
        """Dispatch the message to registered callbacks; returns their count."""
        subscribers = self.channels.get(channel, [])
        for callback in list(subscribers):
            await callback(message)
        return len(subscribers)

    async def subscribe(self, channel: str, callback: RawStateCallback) -> None:
        """Register a raw-message callback on a channel."""
        callbacks = self.channels.setdefault(channel, [])
        if callback not in callbacks:
            callbacks.append(callback)

    async def unsubscribe(self, channel: str, callback: RawStateCallback) -> None:
        """Unregister a raw-message callback from a channel."""
        callbacks = self.channels.get(channel, [])
        if callback in callbacks:
            callbacks.remove(callback)
        if not callbacks:
            self.channels.pop(channel, None)

    def raw_entries(self) -> dict[str, str]:
        """Expose the live entries for key-naming assertions."""
        return {key: value for key, value in self.values.items() if not self._expired(key)}

    def _expired(self, key: str) -> bool:
        """Check whether a key's TTL elapsed."""
        deadline = self.expiries.get(key)
        return deadline is not None and self.clock.now >= deadline


def any_event_callback(sink: list[tuple[str, dict[str, Any]]]) -> Any:
    """Build a recording StateEventCallback appending (event, payload) pairs."""

    async def callback(event: str, payload: dict[str, Any]) -> None:
        sink.append((event, payload))

    return callback
