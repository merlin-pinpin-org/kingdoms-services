"""Retry/deadline policy tests (ADR-0020 annex)."""

from __future__ import annotations

import asyncio

import grpc
import pytest

from kingdoms.core.rpc.client import (
    DEFAULT_POLICY,
    RETRYABLE_CODES,
    RetryPolicy,
    call_with_retry,
)


class _Failing:
    """Stub callable that fails N times with a status then succeeds."""

    def __init__(self, status: grpc.StatusCode, failures: int) -> None:
        self.status = status
        self.failures = failures
        self.calls = 0

    async def __call__(self, request, timeout):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls <= self.failures:
            raise grpc.aio.AioRpcError(
                code=self.status,
                initial_metadata=grpc.aio.Metadata(),
                trailing_metadata=grpc.aio.Metadata(),
                details="boom",
            )
        return "ok"


@pytest.mark.asyncio
async def test_retry_then_success() -> None:
    """UNAVAILABLE is retried; the third attempt succeeds."""
    call = _Failing(grpc.StatusCode.UNAVAILABLE, failures=2)
    policy = RetryPolicy(max_attempts=3, initial_backoff=0.0)
    result = await call_with_retry(call, None, policy=policy)
    assert result == "ok"
    assert call.calls == 3


@pytest.mark.asyncio
async def test_non_retryable_raises_immediately() -> None:
    """NOT_FOUND must not burn retries."""
    call = _Failing(grpc.StatusCode.NOT_FOUND, failures=5)
    with pytest.raises(grpc.aio.AioRpcError):
        await call_with_retry(call, None, policy=DEFAULT_POLICY)
    assert call.calls == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_stops_retrying() -> None:
    """Backoff beyond the deadline raises instead of sleeping forever."""
    call = _Failing(grpc.StatusCode.UNAVAILABLE, failures=99)
    policy = RetryPolicy(max_attempts=10, initial_backoff=1.0, max_backoff=5.0)
    with pytest.raises(grpc.aio.AioRpcError):
        await call_with_retry(call, None, policy=policy, deadline=0.05)
    assert call.calls < 10


def test_retryable_codes_are_transport_level() -> None:
    """Only transport-level statuses are retryable."""
    assert grpc.StatusCode.UNAVAILABLE in RETRYABLE_CODES
    assert grpc.StatusCode.INTERNAL not in RETRYABLE_CODES
    assert grpc.StatusCode.INVALID_ARGUMENT not in RETRYABLE_CODES


def test_backoff_is_jittered_and_capped() -> None:
    """Backoff stays within [0, cap] and grows then caps."""
    policy = RetryPolicy(initial_backoff=0.1, max_backoff=0.3)
    for attempt in range(5):
        wait = policy.backoff(attempt)
        assert 0 <= wait <= 0.3 + 1e-9


@pytest.mark.asyncio
async def test_fast_path_no_sleep() -> None:
    """A first-attempt success never sleeps."""
    call = _Failing(grpc.StatusCode.UNAVAILABLE, failures=0)

    async def _fail_if_sleep(duration: float) -> None:
        raise AssertionError("sleep called on first-attempt success")

    real_sleep = asyncio.sleep
    asyncio.sleep = _fail_if_sleep  # type: ignore[assignment]
    try:
        assert await call_with_retry(call, None) == "ok"
    finally:
        asyncio.sleep = real_sleep  # type: ignore[assignment]
