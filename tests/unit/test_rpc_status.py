"""Status seam tests: server serves, client fetches, payloads round-trip."""
from __future__ import annotations

import pytest

from kingdoms.core.rpc.status import (
    CoreStatus,
    build_status_server,
    fetch_core_status,
)


async def _pick_port(server) -> int:  # type: ignore[no-untyped-def]
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return port


@pytest.mark.asyncio
async def test_status_roundtrip() -> None:
    """The client receives the server-provided status over the wire."""
    server = build_status_server(
        lambda: CoreStatus(version="1.2.3", process="svc-core")
    )
    port = await _pick_port(server)
    try:
        status = await fetch_core_status(f"127.0.0.1:{port}")
        assert status == CoreStatus(version="1.2.3", process="svc-core")
    finally:
        await server.stop(grace=None)


@pytest.mark.asyncio
async def test_status_provider_is_called_per_request() -> None:
    """The provider is live: status changes are reflected on the wire."""
    calls: list[int] = []

    def provider() -> CoreStatus:
        calls.append(1)
        return CoreStatus(version=f"v{len(calls)}", process="svc-core")

    server = build_status_server(provider)
    port = await _pick_port(server)
    try:
        first = await fetch_core_status(f"127.0.0.1:{port}")
        second = await fetch_core_status(f"127.0.0.1:{port}")
        assert first.version == "v1"
        assert second.version == "v2"
    finally:
        await server.stop(grace=None)
