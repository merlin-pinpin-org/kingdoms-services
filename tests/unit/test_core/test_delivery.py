"""Unit tests for the notification delivery pipeline (kingdoms-services#140)."""

from __future__ import annotations

import random
from typing import Any

import pytest

from kingdoms.core.services.delivery import (
    MAX_BACKOFF_S,
    NotificationPipeline,
    RateLimitedError,
)


class RecordingSink:
    """Sink recording batches; can simulate sustained rate limiting."""

    def __init__(self, rate_limited_destinations: set[str] | None = None) -> None:
        self.batches: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []
        self.rate_limited = rate_limited_destinations or set()

    async def send(self, destination: str, batch: list[tuple[str, dict[str, Any]]]) -> None:
        if destination in self.rate_limited:
            raise RateLimitedError(destination)
        self.batches.append((destination, batch))


class FakeClock:
    """Manually-advanced clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_burst_coalesces_last_write_wins() -> None:
    sink = RecordingSink()
    pipe = NotificationPipeline(sink)
    for i in range(10):
        pipe.enqueue("matches", "match.feed", {"i": i})
    results = await pipe.drain()
    assert results["matches"] == 1
    destination, batch = sink.batches[0]
    assert destination == "matches"
    assert batch == [("match.feed", {"i": 9})]


@pytest.mark.asyncio
async def test_distinct_intents_stay_distinct() -> None:
    sink = RecordingSink()
    pipe = NotificationPipeline(sink)
    pipe.enqueue("play", "queue.joined", {"user": "u1"})
    pipe.enqueue("play", "queue.left", {"user": "u1"})
    await pipe.drain()
    assert len(sink.batches[0][1]) == 2


@pytest.mark.asyncio
async def test_limit_hit_degrades_and_keeps_queue() -> None:
    sink = RecordingSink(rate_limited_destinations={"matches"})
    clock = FakeClock()
    pipe = NotificationPipeline(sink, clock=clock)
    pipe.enqueue("matches", "match.created", {"match": "m1"})
    results = await pipe.drain()
    assert results["matches"] == 0
    assert pipe.pending("matches") == 1
    clock.advance(0.1)
    results = await pipe.drain()
    assert results["matches"] == 0
    assert pipe.pending("matches") == 1


@pytest.mark.asyncio
async def test_drain_recovers_after_backoff_elapses() -> None:
    sink = RecordingSink(rate_limited_destinations={"matches"})
    clock = FakeClock()
    pipe = NotificationPipeline(sink, clock=clock, rng=random.Random(42))  # noqa: S311 - test rng
    pipe.enqueue("matches", "match.created", {"match": "m1"})
    await pipe.drain()
    assert pipe.pending("matches") == 1
    sink.rate_limited.clear()
    clock.advance(120.0)
    results = await pipe.drain()
    assert results["matches"] == 1
    assert pipe.pending("matches") == 0
    assert sink.batches[0][1] == [("match.created", {"match": "m1"})]


@pytest.mark.asyncio
async def test_healthy_destination_unaffected_by_degraded_one() -> None:
    sink = RecordingSink(rate_limited_destinations={"matches"})
    clock = FakeClock()
    pipe = NotificationPipeline(sink, clock=clock)
    pipe.enqueue("matches", "match.created", {"m": 1})
    pipe.enqueue("play", "queue.joined", {"u": 1})
    results = await pipe.drain()
    assert results == {"matches": 0, "play": 1}


@pytest.mark.asyncio
async def test_backoff_is_bounded() -> None:
    rng = random.Random(0)  # noqa: S311 - test rng
    for failures in range(1, 20):
        from kingdoms.core.services.delivery import _jittered_backoff

        backoff = _jittered_backoff(failures, rng)
        assert 0.25 <= backoff <= MAX_BACKOFF_S
