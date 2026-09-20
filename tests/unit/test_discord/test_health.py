"""Unit tests for the container health endpoint (kingdoms-infra#4).

The bot image healthcheck and the infra deployment gate depend on
``/healthz``: these tests pin the contract (200 when alive, 503 when the
probe says otherwise, 404 for anything but the health path).
"""

from __future__ import annotations

import asyncio

import pytest

from kingdoms.discord.bot.health import HealthServer


def _parse(response: bytes) -> tuple[int, str]:
    """Return the HTTP status code and the response body."""
    head, _, body = response.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n")[0].decode()
    return int(status_line.split(" ")[1]), body.decode()


async def _get(port: int, path: str) -> tuple[int, str]:
    """Issue one GET against the health server."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return _parse(response)


@pytest.fixture
async def health_port() -> asyncio.Iterator[int]:
    """Run a health server on an ephemeral port for the duration of a test."""
    server = HealthServer(port=0)
    await server.start()
    port = server.sockets[0].getsockname()[1]  # type: ignore[index]
    yield port
    await server.stop()


async def test_healthz_serves_200(health_port: int) -> None:
    """A live server answers the liveness probe with 200."""
    status, body = await _get(health_port, "/healthz")
    assert status == 200
    assert body == "healthy"


async def test_other_paths_are_404(health_port: int) -> None:
    """Only the health path is served; everything else is 404."""
    status, _ = await _get(health_port, "/")
    assert status == 404


async def test_failing_probe_serves_503() -> None:
    """A probe reporting unhealthy must surface as 503."""

    async def probe() -> bool:
        return False

    server = HealthServer(port=0, probe=probe)
    await server.start()
    port = server.sockets[0].getsockname()[1]  # type: ignore[index]
    try:
        status, body = await _get(port, "/healthz")
        assert status == 503
        assert body == "unhealthy"
    finally:
        await server.stop()


async def test_raising_probe_serves_503() -> None:
    """A crashing probe must fail closed (503), never 200."""

    async def probe() -> bool:
        raise RuntimeError("gateway lost")

    server = HealthServer(port=0, probe=probe)
    await server.start()
    port = server.sockets[0].getsockname()[1]  # type: ignore[index]
    try:
        status, _ = await _get(port, "/healthz")
        assert status == 503
    finally:
        await server.stop()


async def test_stop_is_idempotent() -> None:
    """Stopping twice is safe (run_bot's finally block may re-run)."""
    server = HealthServer(port=0)
    await server.start()
    await server.stop()
    await server.stop()
