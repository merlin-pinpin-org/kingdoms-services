"""Content provider process entrypoints (ext-<source> family).

Each ext content process serves the kingdoms.v1.Content contract,
backed by its own upstream source (vendored dataset, HTTP API, ...).
This module holds the shared serving loop so content entrypoints stay
one-liners, mirroring the Game provider loop (ADR-0020).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import grpc

from kingdoms.core.rpc.content import ContentServicer, add_content_servicer

logger = logging.getLogger("kingdoms.ext")


async def serve_content_provider(
    provider_key: str,
    game_key: str,
    locales: tuple[str, ...],
    process_label: str,
    source: Any,
) -> None:
    """Serve the kingdoms.v1.Content contract until terminated.

    Reads ``EXT_GRPC_PORT`` (default 50063 for the content family). The
    upstream source is opaque here: it only needs the faction/map
    descriptor interface, so a dataset or an HTTP API serve identically.
    """
    server = grpc.aio.server()
    add_content_servicer(
        server,
        ContentServicer(
            provider_key,
            game_key,
            locales,
            list_factions=lambda: _async_wrap(source.faction_keys()),
            faction_content=lambda key, locale: _async_wrap(source.faction_content(key, locale)),
            map_content=lambda key, locale: _async_wrap(source.map_content(key, locale)),
        ),
    )
    port = os.environ.get("EXT_GRPC_PORT", "50063")
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info(
        "%s_READY port=%s provider=%s game=%s",
        process_label,
        port,
        provider_key,
        game_key,
    )
    await server.wait_for_termination()


async def _async_wrap(value: Any) -> Any:
    """Adapt sync upstream calls to the servicer's awaitable contract."""
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
