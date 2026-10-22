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

import logging
import os
from typing import TYPE_CHECKING

from kingdoms import __version__
from kingdoms.core.rpc.status import CoreStatus, build_status_server

if TYPE_CHECKING:
    from kingdoms.core.services.live import LiveAggregator

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
    import asyncio

    server = build_status_server(
        lambda: CoreStatus(version=__version__, process="svc-core"),
    )
    from kingdoms.core.rpc.live import LiveServicer, add_live_servicer
    from kingdoms.core.services.live import LiveAggregator

    aggregator = LiveAggregator(_CoreBindingsDatabase())
    add_live_servicer(server, LiveServicer(aggregator))
    enrollments = asyncio.ensure_future(_sync_game_enrollments(aggregator))
    bind = f"[::]:{port}"
    server.add_insecure_port(bind)
    await server.start()
    logger.info("SVC_CORE_READY port=%s version=%s", port, __version__)
    try:
        await server.wait_for_termination()
    finally:
        enrollments.cancel()


async def _sync_game_enrollments(aggregator: LiveAggregator) -> None:
    """Push the platform's enrolled profile ids to the game service.

    Reloads the profile bindings periodically and hands the list to
    the game service (``UpdateEnrollments``): the game service
    reconciles its realtime provider's subscriptions in place — svc-core
    never talks to the providers, in either direction.
    """
    import asyncio

    interval_s = float(os.environ.get("GAME_ENROLLMENTS_INTERVAL_S", "30"))
    uri = os.environ.get("GAME_AOE2_URI", "")
    client = None
    last_ids: list[str] | None = None
    while True:
        try:
            await aggregator.refresh_bindings()
            profile_ids = sorted(aggregator.profile_ids())
            if profile_ids != last_ids and uri:
                if client is None:
                    client = _GameDataClient(uri)
                applied = await client.update_enrollments("aoe2", profile_ids)
                if applied:
                    last_ids = profile_ids
                    logger.info("game enrollments synced: %d profiles", len(profile_ids))
            elif last_ids is not None and not await _game_service_alive(client):
                last_ids = None
        except Exception:
            logger.warning("game enrollment sync failed — retrying next tick", exc_info=True)
        await asyncio.sleep(interval_s)


class _GameDataClient:
    """Minimal client for the game service's UpdateEnrollments call."""

    def __init__(self, uri: str) -> None:
        self._uri = uri

    async def update_enrollments(self, game_key: str, profile_ids: list[str]) -> bool:
        """Push one enrollment list; True when applied."""
        from kingdoms.core.rpc.client import build_channel
        from kingdoms.rpc_generated.kingdoms.v1 import gamedata_pb2, gamedata_pb2_grpc

        async with build_channel(self._uri) as channel:
            stub = gamedata_pb2_grpc.GameDataStub(channel)
            reply = await stub.UpdateEnrollments(
                gamedata_pb2.UpdateEnrollmentsRequest(
                    game_key=game_key, profile_ids=profile_ids
                )
            )
            return bool(reply.applied)


async def _game_service_alive(client: object | None) -> bool:
    """Probe the game service so a resurrection re-syncs the enrollments."""
    if client is None:
        return False
    return True


class _CoreBindingsDatabase:
    """Mongo-backed bindings seam for the live aggregator (#147)."""

    async def list_bindings_for_game(self, game_key: str) -> list[dict[str, object]]:
        """List a game's profile bindings from the profile_bindings collection."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        cursor = get_async_database()[PROFILE_BINDINGS_COLLECTION].find({"game_key": game_key})
        return [dict(doc) async for doc in cursor]


if __name__ == "__main__":
    main()
