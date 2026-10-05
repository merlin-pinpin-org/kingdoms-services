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
    import redis.asyncio as redis

    from kingdoms.core.models.game import GameMap, MatchDetails, PlayerStats
    from kingdoms.core.rpc.rate_limit import ProviderRateLimiter
    from kingdoms.core_process.ext_server import serve_game_provider
    from kingdoms.ext_librematch import DECLARED_CAPABILITIES, PROVIDER_ID
    from kingdoms.ext_librematch.adapter import LibrematchAdapter

    redis_uri = os.environ.get("REDIS_URI", "")
    rate_limiter = None
    if redis_uri:
        rate_limiter = ProviderRateLimiter(
            redis.from_url(redis_uri, decode_responses=True),
            game_key="aoe2",
            max_calls=int(os.environ.get("LIBREMATCH_RATE_MAX_CALLS", "60")),
            window_s=int(os.environ.get("LIBREMATCH_RATE_WINDOW_S", "60")),
        )

    adapter = LibrematchAdapter(
        base_url=os.environ.get("LIBREMATCH_API_URL", "https://community.ageofempires.com"),
        api_key=os.environ.get("LIBREMATCH_API_KEY", ""),
        rate_limiter=rate_limiter,
    )

    async def match_details(match_ref: str) -> MatchDetails | None:
        """Fetch one match's details through the live adapter."""
        return await adapter.match_details(match_ref)

    async def list_maps() -> list[GameMap]:
        """Fetch the map catalog through the live adapter."""
        return await adapter.list_maps()

    async def player_stats(profile_id: str) -> PlayerStats | None:
        """Fetch a profile's leaderboard blocks (None: degraded, no key)."""
        return await adapter.player_stats(profile_id)

    asyncio.run(
        serve_game_provider(
            DECLARED_CAPABILITIES,
            PROVIDER_ID,
            match_details=match_details,
            list_maps=list_maps,
            player_stats=player_stats,
        )
    )


if __name__ == "__main__":
    main()
