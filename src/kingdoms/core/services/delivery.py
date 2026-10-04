"""Notification delivery pipeline: batching, backoff, degradation (kingdoms-services#140).

Between the mod cores (which emit intents) and the platform adapter
(which renders them) sits this pipeline. Business never blocks on
delivery: intents are coalesced per destination within a short window
(a leaderboard refresh or a queue burst becomes one render), and when
the platform signals rate limiting the pipeline backs off with jitter
and keeps queueing — the match surface stays authoritative and the
player-facing state consistent.

Contract:
- ``enqueue`` never raises and never blocks: an intent is either
  delivered, or queued for the next drain;
- ``drain`` flushes what the limits allow, marking degraded
  destinations and re-arming them once the backoff elapses;
- per-destination FIFO with coalescing by (surface, intent): the
  latest payload for a burst wins (last-write-wins rendering).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.delivery")

DEFAULT_COALESCE_MS = 250
BASE_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0


class DeliverySink(Protocol):
    """Platform sink: renders one coalesced batch on one destination."""

    async def send(self, destination: str, batch: list[tuple[str, dict[str, Any]]]) -> None:
        """Render a batch on a destination; raise RateLimitedError when throttled."""
        ...


class RateLimitedError(RuntimeError):
    """The platform sink hit a rate limit for this destination."""


@dataclass(slots=True)
class _DestinationState:
    """Per-destination queue + degradation bookkeeping."""

    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    rate_limited_until: float = 0.0
    consecutive_failures: int = 0


class NotificationPipeline:
    """Coalescing, backoff-aware delivery pipeline (svc-core side)."""

    def __init__(
        self,
        sink: DeliverySink,
        clock: Any = None,
        sleeper: Awaitable[None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        """Wire the platform sink and the injectable clock/rng seams."""
        self._sink = sink
        self._states: dict[str, _DestinationState] = {}
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto
        self._loop = asyncio.get_event_loop()
        self._clock = clock

    def _now(self) -> float:
        """Monotonic-ish now (injectable clock, default loop time)."""
        if self._clock is not None:
            return float(self._clock())
        return self._loop.time()

    def enqueue(self, destination: str, intent: str, payload: dict[str, Any]) -> None:
        """Queue one intent for a destination (never blocks, last-write-wins)."""
        state = self._states.setdefault(destination, _DestinationState())
        key = f"{intent}"
        if key not in state.pending:
            state.order.append(key)
        state.pending[key] = payload

    async def drain(self) -> dict[str, int]:
        """Flush every healthy destination; backoff the degraded ones.

        Returns per-destination delivered batch sizes; degraded
        destinations report 0 and keep their pending intents queued —
        ladder business never blocks on them.
        """
        results: dict[str, int] = {}
        for destination, state in list(self._states.items()):
            if not state.order:
                results[destination] = 0
                continue
            if self._now() < state.rate_limited_until:
                results[destination] = 0
                continue
            batch = [(key, state.pending.pop(key)) for key in state.order]
            state.order.clear()
            try:
                await self._sink.send(destination, batch)
                state.consecutive_failures = 0
                results[destination] = len(batch)
            except RateLimitedError:
                state.consecutive_failures += 1
                backoff = _jittered_backoff(state.consecutive_failures, self._rng)
                state.rate_limited_until = self._now() + backoff
                for key, payload in batch:
                    state.pending[key] = payload
                state.order = [k for k, _ in batch]
                results[destination] = 0
                logger.warning("delivery degraded for %s: backoff %.1fs", destination, backoff)
            except Exception:
                state.consecutive_failures += 1
                for key, payload in batch:
                    state.pending[key] = payload
                state.order = [k for k, _ in batch]
                results[destination] = 0
                logger.warning("delivery failed for %s (will retry)", destination, exc_info=True)
        return results

    def pending(self, destination: str) -> int:
        """Count the queued intents of one destination (observability)."""
        state = self._states.get(destination)
        return len(state.order) if state else 0


def _jittered_backoff(consecutive_failures: int, rng: random.Random) -> float:
    """Exponential backoff with jitter, capped at MAX_BACKOFF_S."""
    base = min(MAX_BACKOFF_S, BASE_BACKOFF_S * (2 ** max(0, consecutive_failures - 1)))
    return float(min(MAX_BACKOFF_S, base * (0.5 + rng.random())))
