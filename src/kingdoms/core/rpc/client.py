"""gRPC channel policy for the ADR-0020 seams (client side).

Every cross-process call goes through channels built here so the
ADR-0020 annex rules hold in one place: per-call deadlines, retry with
jitter, and typed error mapping at the seam boundary. Keep the policy
conservative by default — business code must never configure transport
ad-hoc.
"""
from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import grpc


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry policy for one seam method class.

    Retries happen inside the deadline budget: each attempt gets the
    remaining time, and exhausting the budget surfaces the last error to
    the caller (the seam maps it onto the typed taxonomy).
    """

    max_attempts: int = 3
    initial_backoff: float = 0.25
    max_backoff: float = 2.0

    def backoff(self, attempt: int) -> float:
        """Full jitter backoff for the given attempt index (0-based)."""
        cap = min(self.initial_backoff * (2**attempt), self.max_backoff)
        return random.uniform(0, cap)  # noqa: S311 - jitter, not crypto


DEFAULT_POLICY = RetryPolicy()
DEFAULT_DEADLINE: float = 5.0

# Errors that justify a transparent retry (the call never reached the
# server or the server is known-idempotent-safe at this layer).
RETRYABLE_CODES = frozenset(
    {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
    }
)


async def call_with_retry(
    method: Callable[..., Awaitable[object]],
    request: object,
    *,
    policy: RetryPolicy = DEFAULT_POLICY,
    deadline: float = DEFAULT_DEADLINE,
):
    """Invoke a unary gRPC method with deadline and retry-with-jitter.

    ``method`` is a bound stub callable. The deadline covers all
    attempts; non-retryable statuses raise immediately (the exception
    taxonomy mapping happens in the seam, not here).
    """
    remaining = deadline
    last_exc: Exception | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await method(request, timeout=remaining)
        except grpc.aio.AioRpcError as exc:
            last_exc = exc
            if exc.code() not in RETRYABLE_CODES:
                raise
            if attempt == policy.max_attempts - 1:
                raise
            wait = policy.backoff(attempt)
            if wait >= remaining:
                raise
            import asyncio

            await asyncio.sleep(wait)
            remaining -= wait
    raise last_exc if last_exc is not None else RuntimeError("unreachable")


def build_channel(target: str) -> grpc.aio.Channel:
    """Build the aio channel for a process seam (no transport options yet)."""
    return grpc.aio.insecure_channel(target)
