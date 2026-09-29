"""ext-librematch process entrypoint (kingdoms.v1.Game provider)."""

from __future__ import annotations

import logging
import os


def main() -> None:
    """Run the ext-librematch gRPC provider server."""
    import asyncio

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from kingdoms.core.models.game import GameMap, MatchDetails
    from kingdoms.core_process.ext_server import serve_game_provider
    from kingdoms.ext_librematch import DECLARED_CAPABILITIES, PROVIDER_ID
    from kingdoms.ext_librematch.adapter import LibrematchAdapter

    adapter = LibrematchAdapter(
        base_url=os.environ.get("LIBREMATCH_API_URL", "https://community.ageofempires.com"),
    )

    async def match_details(match_ref: str) -> MatchDetails | None:
        """Fetch one match's details through the live adapter."""
        return await adapter.match_details(match_ref)

    async def list_maps() -> list[GameMap]:
        """Fetch the map catalog through the live adapter."""
        return await adapter.list_maps()

    asyncio.run(
        serve_game_provider(
            DECLARED_CAPABILITIES,
            PROVIDER_ID,
            match_details=match_details,
            list_maps=list_maps,
        )
    )


if __name__ == "__main__":
    main()
