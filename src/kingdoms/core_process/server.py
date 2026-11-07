"""svc-core gRPC server (ADR-0020 bootstrap seam).

Serves the kingdoms.v1 contracts from ``contracts/``: Status (the
cross-process wiring assert) and Live (the aggregated dashboard).

Direction développeur (architecture cible): svc-core never talks to
the external providers — the game service (game-aoe2) owns that seam.
The background loop pushes the platform's enrollments (profile
bindings) down to the game service via ``UpdateEnrollments``; the
dashboard and player events flow back over the Live contract, fed by
the game service's streams.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from kingdoms import __version__
from kingdoms.core.rpc.status import CoreStatus, build_status_server

if TYPE_CHECKING:
    pass

logger = logging.getLogger("kingdoms.core_process")


def main() -> None:
    """Run the svc-core gRPC server."""
    import asyncio

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = os.environ.get("CORE_GRPC_PORT", "50051")
    asyncio.run(_serve(port))


async def _serve(port: str) -> None:

    server = build_status_server(
        lambda: CoreStatus(version=__version__, process="svc-core"),
    )
    # NO Live servicer here (owner direction 2026-11-05): the player
    # state has ONE owner — the game service's aggregator. The core's
    # duplicate fold (core.services.live) is deleted; the core serves
    # the platform's own surfaces only.
    #
    # The ENROLLMENT PUSH stays (the dashboard's roster depends on it):
    # the platform's bound profiles ride down to the game service
    # (UpdateEnrollments) — the game reconciles its provider's
    # subscriptions; core never talks to a provider, in either direction.
    enrollments = asyncio.ensure_future(_sync_game_enrollments())
    bind = f"[::]:{port}"
    server.add_insecure_port(bind)
    await server.start()
    logger.info("SVC_CORE_READY port=%s version=%s", port, __version__)
    try:
        await server.wait_for_termination()
    finally:
        enrollments.cancel()


async def _sync_game_enrollments() -> None:
    """Push the platform's enrolled profile ids to the game service.

    Reloads the profile bindings periodically (``profile_bindings`` is
    CORE's own collection — rule 5: the owner reads it) and hands the
    list to the game service (``UpdateEnrollments``): the game service
    reconciles its realtime provider's subscriptions in place.
    """
    interval_s = float(os.environ.get("GAME_ENROLLMENTS_INTERVAL_S", "30"))
    uri = os.environ.get("GAME_AOE2_URI", "")
    client: Any = None
    last_ids: list[str] | None = None
    while True:
        try:
            from kingdoms.core.models.db import get_async_database

            database = get_async_database()
            profile_ids: list[str] = []
            user_ids: list[str] = []
            async for binding in database["profile_bindings"].find({"game_key": "aoe2"}):
                profile_ids.append(str(binding.get("profile_id", "")))
                user_ids.append(str(binding.get("user_id", "")))
            profile_ids_sorted = sorted(profile_ids)
            if profile_ids_sorted != last_ids and uri:
                if client is None:
                    client = _GameDataClient(uri)
                applied = await client.update_enrollments("aoe2", profile_ids_sorted, user_ids)
                if applied:
                    last_ids = profile_ids_sorted
                    logger.info("GAME ENROLLMENTS pushed: %d profiles", len(profile_ids_sorted))
        except Exception:
            logger.warning("game enrollments sync failed — retrying", exc_info=True)
        await asyncio.sleep(interval_s)


class _GameDataClient:
    """Minimal client for the game service's UpdateEnrollments call."""

    def __init__(self, uri: str) -> None:
        self._uri = uri

    async def update_enrollments(
        self, game_key: str, profile_ids: list[str], user_ids: list[str]
    ) -> bool:
        """Push one enrollment list (profiles + owners); True when applied."""
        from kingdoms.core.rpc.client import build_channel
        from kingdoms.rpc_generated.kingdoms.v1 import gamedata_pb2, gamedata_pb2_grpc

        async with build_channel(self._uri) as channel:
            stub = gamedata_pb2_grpc.GameDataStub(channel)
            reply = await stub.UpdateEnrollments(
                gamedata_pb2.UpdateEnrollmentsRequest(
                    game_key=game_key, profile_ids=profile_ids, user_ids=user_ids
                )
            )
            return bool(reply.applied)


if __name__ == "__main__":
    main()
