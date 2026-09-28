"""ext-aoe2lobby process entrypoint (kingdoms.v1.Game provider)."""

from __future__ import annotations

import logging
import os


def main() -> None:
    """Run the ext-aoe2lobby gRPC provider server."""
    import asyncio

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from kingdoms.core_process.ext_server import serve_game_provider
    from kingdoms.ext_aoe2lobby import DECLARED_CAPABILITIES, PROVIDER_ID

    asyncio.run(serve_game_provider(DECLARED_CAPABILITIES, PROVIDER_ID))


if __name__ == "__main__":
    main()
