"""Game provider RPC seam — server side (kingdoms.v1.Game).

A game provider process (ext-librematch, ext-aoe2lobby) builds its gRPC
server from these servicers, backed by its own adapters to the external
APIs. The seam keeps provider code out of the core: it only translates
between the wire contract and plain core models.
"""

from __future__ import annotations

from collections.abc import Callable

import grpc

from kingdoms.core.models.game import ProviderCapabilities
from kingdoms.rpc_generated.kingdoms.v1 import game_pb2, game_pb2_grpc


def capabilities_to_wire(caps: ProviderCapabilities) -> game_pb2.Capabilities:
    """Translate a core capability declaration into the wire message."""
    return game_pb2.Capabilities(
        provider_id=caps.provider_id,
        game_key=caps.game_key,
        realtime=caps.realtime,
        reliable_results=caps.reliable_results,
        check_map=caps.check_map,
        player_stats=caps.player_stats,
    )


def capabilities_from_wire(wire: game_pb2.Capabilities) -> ProviderCapabilities:
    """Translate a wire capability message into the core model."""
    return ProviderCapabilities(
        provider_id=wire.provider_id,
        game_key=wire.game_key,
        realtime=wire.realtime,
        reliable_results=wire.reliable_results,
        check_map=wire.check_map,
        player_stats=wire.player_stats,
    )


class GameServicer(game_pb2_grpc.GameServicer):
    """Serve the kingdoms.v1.Game contract from provider-backed callables.

    Providers register plain async callables; this servicer adapts them to
    gRPC. Providers that do not support a capability raise
    ``UnsupportedCapability`` in the backing callable, mapped to
    UNIMPLEMENTED on the wire so the core degrades cleanly.
    """

    def __init__(
        self,
        capabilities: Callable[[], ProviderCapabilities],
    ) -> None:
        self._capabilities = capabilities

    async def GetCapabilities(
        self,
        request: game_pb2.CapabilitiesRequest,
        context: grpc.aio.ServicerContext,
    ) -> game_pb2.Capabilities:
        """Serve the provider's declared capabilities as the wire message."""
        return capabilities_to_wire(self._capabilities())
