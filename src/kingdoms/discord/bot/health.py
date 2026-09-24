"""Container health endpoint: a minimal asyncio HTTP server for ``/healthz``.

The bot image (``Dockerfile``) declares a ``HEALTHCHECK`` probing
``http://localhost:8000/healthz`` and the infra deployment gate
(``kingdoms-infra/scripts/deploy.sh``) waits on compose health status.
The bot is the only process in the container, so a liveness signal is
enough: if the process is up, Docker keeps restarting it when it dies, and
the health gate detects a crash-looping bot as ``unhealthy``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("kingdoms.bot.health")

HealthStatus = Callable[[], Awaitable[bool]]

DEFAULT_PORT = 8000
HEALTH_PATH = "/healthz"


class HealthServer:
    """Serve ``/healthz`` on localhost; everything else is 404."""

    def __init__(self, port: int = DEFAULT_PORT, probe: HealthStatus | None = None) -> None:
        """Prepare the server; call :meth:`start` to bind the port."""
        self._port = port
        self._probe = probe
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        """Return the port the server binds (exposed for tests)."""
        return self._port

    @property
    def running(self) -> bool:
        """Return whether the server is accepting connections."""
        return self._server is not None

    @property
    def sockets(self) -> tuple[object, ...]:
        """Return the listening sockets (tests read the bound port from them)."""
        if self._server is None:
            return ()
        return self._server.sockets or ()

    async def start(self) -> None:
        """Bind the health port on localhost and start serving."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self._port)
        logger.info("health server listening on 127.0.0.1:%d", self._port)

    async def stop(self) -> None:
        """Close the listener and wait for pending handlers to drain."""
        if self._server is None:
            return
        server, self._server = self._server, None
        server.close()
        await server.wait_closed()
        logger.info("health server stopped")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answer one HTTP/1.1 request with the liveness verdict."""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            method, path, _ = request_line.decode("latin-1").split(" ", 2)
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass
        except (TimeoutError, ValueError):
            await self._respond(writer, 400, "bad request")
            return
        if method != "GET" or path != HEALTH_PATH:
            await self._respond(writer, 404, "not found")
            return
        healthy = True
        if self._probe is not None:
            try:
                healthy = await self._probe()
            except Exception:
                healthy = False
        if healthy:
            await self._respond(writer, 200, "healthy")
        else:
            await self._respond(writer, 503, "unhealthy")
            logger.warning("HEALTH_PROBE_FAILED")

    async def _respond(self, writer: asyncio.StreamWriter, status: int, body: str) -> None:
        """Write one HTTP response and close the connection."""
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 503: "Service Unavailable"}[status]
        payload = body.encode()
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Connection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()
