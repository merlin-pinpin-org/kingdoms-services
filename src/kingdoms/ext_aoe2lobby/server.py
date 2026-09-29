"""ext-aoe2lobby process entrypoint (kingdoms.v1.Game live provider)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator


def main() -> None:
    """Run the ext-aoe2lobby gRPC provider server."""
    import asyncio

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from kingdoms.core.models.game import MatchEvent
    from kingdoms.core_process.ext_server import serve_game_provider
    from kingdoms.ext_aoe2lobby import DECLARED_CAPABILITIES, PROVIDER_ID
    from kingdoms.ext_aoe2lobby.adapter import Aoe2LobbyAdapter

    adapter = Aoe2LobbyAdapter(
        ws_url=os.environ.get("AOE2LOBBY_WS_URL", "wss://aoe2lobby.com/ws"),
    )

    async def match_events() -> AsyncIterator[MatchEvent]:
        """Yield normalized WS frames as core MatchEvent models."""
        async for frame in adapter.stream_events():
            yield MatchEvent(
                match_ref=frame["match_ref"],
                type=frame["type"],
                occurred_at=frame["occurred_at"],
                profile_ids=tuple(frame["profile_ids"]),
                metadata=tuple(sorted(frame["metadata"].items())),
            )

    asyncio.run(
        serve_game_provider(
            DECLARED_CAPABILITIES,
            PROVIDER_ID,
            match_events=match_events,
        )
    )


if __name__ == "__main__":
    main()
