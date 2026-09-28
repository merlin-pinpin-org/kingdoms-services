"""Status seam — server side and client helpers (kingdoms.v1.Status).

The bootstrap seam of the process split: lets any process assert
svc-core wiring (version, reachability) without domain dependencies.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import grpc

from kingdoms.rpc_generated.kingdoms.v1 import status_pb2, status_pb2_grpc


@dataclass(frozen=True, slots=True)
class CoreStatus:
    """Platform-agnostic status payload (pydantic-style plain model)."""

    version: str
    process: str


class _StatusServicer(status_pb2_grpc.StatusServicer):
    def __init__(self, status_provider: Callable[[], CoreStatus]) -> None:
        self._provider = status_provider

    async def GetCoreStatus(
        self,
        request: status_pb2.CoreStatusRequest,
        context: grpc.aio.ServicerContext,
    ) -> status_pb2.CoreStatus:
        """Serve the provider's current status payload as the wire message."""
        status = self._provider()
        return status_pb2.CoreStatus(version=status.version, process=status.process)


def build_status_server(
    status_provider: Callable[[], CoreStatus],
) -> grpc.aio.Server:
    """Build the aio gRPC server with the Status service bound."""
    server = grpc.aio.server()
    status_pb2_grpc.add_StatusServicer_to_server(_StatusServicer(status_provider), server)
    return server


async def fetch_core_status(core_uri: str) -> CoreStatus:
    """Fetch svc-core status over the wire (used by tests and preflight)."""
    async with grpc.aio.insecure_channel(core_uri) as channel:
        stub = status_pb2_grpc.StatusStub(channel)
        reply = await stub.GetCoreStatus(
            status_pb2.CoreStatusRequest(), timeout=5.0
        )
        return CoreStatus(version=reply.version, process=reply.process)
