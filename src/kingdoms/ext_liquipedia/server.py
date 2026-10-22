"""ext-liquipedia process entrypoint (kingdoms.v1.Content provider).

The Liquipedia content source as a real provider process (ADR-0020):
it owns the MediaWiki access and its tables (``liquipedia_maps``,
``liquipedia_pages`` — raw data at the provider), and serves the
Content contract: maps seeding today (``GetMapSeed``), the localized
map content, and the future Liquipedia surfaces (tournaments, matches)
will ride the same contract.

The map seeding stays synchronous-heavy (MediaWiki rate limits), so the
gRPC handlers run it in a worker thread — the server loop never
blocks on a 30 s ``action=parse`` interval.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any


def main() -> None:
    """Run the ext-liquipedia gRPC content provider server."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_serve())


async def _serve() -> None:
    from kingdoms import __version__
    from kingdoms.core.models.db import get_database
    from kingdoms.core.rpc.content import ContentServicer, add_content_servicer
    from kingdoms.core.rpc.status import CoreStatus, build_status_server
    from kingdoms.ext_liquipedia import GAME_KEY, PROVIDER_ID, PROVIDER_KEY, SUPPORTED_LOCALES
    from kingdoms.ext_liquipedia.source import resolve_source

    source = resolve_source(get_database())
    server = build_status_server(
        lambda: CoreStatus(version=__version__, process=PROVIDER_ID),
    )
    add_content_servicer(
        server,
        ContentServicer(
            PROVIDER_KEY,
            GAME_KEY,
            SUPPORTED_LOCALES,
            list_factions=lambda: _wrap(source.faction_keys()),
            faction_content=lambda key, locale: _wrap(source.faction_content(key, locale)),
            map_content=lambda key, locale: _wrap(source.map_content(key, locale)),
            map_seed=lambda game_key, map_key: _wrap(source.map_seed(game_key, map_key)),
        ),
    )
    port = os.environ.get("EXT_GRPC_PORT", "50064")
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logging.getLogger("kingdoms.ext").info(
        "%s_READY port=%s provider=%s game=%s", PROVIDER_ID, port, PROVIDER_KEY, GAME_KEY
    )
    await server.wait_for_termination()


async def _wrap(value: Any) -> Any:
    """Run a blocking upstream call in a worker thread (never block the loop)."""
    import inspect

    if inspect.isawaitable(value):
        return await value
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: value)


if __name__ == "__main__":
    main()
