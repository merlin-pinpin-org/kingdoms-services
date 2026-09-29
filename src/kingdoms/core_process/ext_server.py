"""Provider process entrypoints (ADR-0020 ext-<provider> family).

Each ext process serves the kingdoms.v1.Game contract, backed by its
own adapter to the external API. This module holds the shared serving
loop so provider entrypoints stay one-liners.
"""

from __future__ import annotations

import logging
import os

import grpc

from kingdoms.core.models.game import ProviderCapabilities
from kingdoms.core.rpc.game import GameServicer

logger = logging.getLogger("kingdoms.ext")


async def serve_game_provider(
    capabilities: ProviderCapabilities,
    process_label: str,
    match_details: object | None = None,
    list_maps: object | None = None,
    match_events: object | None = None,
) -> None:
    """Serve the kingdoms.v1.Game contract until terminated.

    Reads ``EXT_GRPC_PORT`` (default 50061). Providers with no live
    adapter yet still serve GetCapabilities — the declared capabilities
    are the provider's single source of truth.
    """
    server = grpc.aio.server()
    game_pb2_grpc_add(
        GameServicer(
            lambda: capabilities,
            match_details=match_details,
            list_maps=list_maps,
            match_events=match_events,
        ),
        server,
    )
    port = os.environ.get("EXT_GRPC_PORT", "50061")
    bind = f"[::]:{port}"
    server.add_insecure_port(bind)
    await server.start()
    logger.info("%s_READY port=%s provider=%s", process_label, port, capabilities.provider_id)
    await server.wait_for_termination()


def game_pb2_grpc_add(servicer: GameServicer, server: grpc.aio.Server) -> None:
    """Bind the Game servicer onto the server (kept out of the entry loop)."""
    from kingdoms.rpc_generated.kingdoms.v1 import game_pb2_grpc

    game_pb2_grpc.add_GameServicer_to_server(servicer, server)
