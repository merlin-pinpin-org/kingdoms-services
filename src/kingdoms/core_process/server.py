"""svc-core gRPC server (ADR-0020 bootstrap seam).

Serves the kingdoms.v1 contracts from ``contracts/``: Status (the
cross-process wiring assert) and Live (the aggregated dashboard of
#147 — the background loop consumes the providers' StreamMatchEvents
and folds them into the LiveAggregator).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

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
    consumer = asyncio.ensure_future(_consume_provider_events(aggregator))
    subscriptions = asyncio.ensure_future(_sync_provider_subscriptions(aggregator))
    bind = f"[::]:{port}"
    server.add_insecure_port(bind)
    await server.start()
    logger.info("SVC_CORE_READY port=%s version=%s", port, __version__)
    try:
        await server.wait_for_termination()
    finally:
        consumer.cancel()
        subscriptions.cancel()


async def _sync_provider_subscriptions(aggregator: LiveAggregator) -> None:
    """Keep the realtime provider subscribed to the enrolled players.

    Reloads the profile bindings periodically and pushes the profile id
    list to the realtime provider (``UpdateSubscriptions``): the
    provider reconciles its upstream players subscription in place —
    adds and removals propagate without any session teardown. A push
    that cannot be applied (provider offline) is retried on the next
    tick; the provider also re-syncs the full list on its reconnect.
    """
    import asyncio

    from kingdoms.core.rpc.game_client import GameProviderClient

    interval_s = float(os.environ.get("PROVIDER_SUBSCRIPTIONS_INTERVAL_S", "30"))
    uri = os.environ.get("EXT_AOE2LOBBY_URI", "")
    client: GameProviderClient | None = None
    last_ids: list[str] | None = None
    while True:
        try:
            await aggregator.refresh_bindings()
            profile_ids = sorted(aggregator.profile_ids())
            if profile_ids != last_ids and uri:
                if client is None:
                    client = GameProviderClient(uri, "ext-aoe2lobby", "aoe2")
                applied = await client.update_subscriptions(profile_ids)
                if applied:
                    last_ids = profile_ids
                    logger.info("provider subscriptions synced: %d profiles", len(profile_ids))
            elif last_ids is not None and not await _provider_alive(client):
                last_ids = None
        except Exception:
            logger.warning("provider subscription sync failed — retrying next tick", exc_info=True)
        await asyncio.sleep(interval_s)


async def _provider_alive(client: Any | None) -> bool:
    """Probe the provider so a resurrection re-syncs the subscriptions."""
    if client is None:
        return False
    try:
        await client.get_capabilities()
        return True
    except Exception:
        return False


async def _consume_provider_events(aggregator: LiveAggregator) -> None:
    """Fold every configured provider's event stream into the aggregator."""
    import asyncio

    providers = [
        ("ext-aoe2lobby", os.environ.get("EXT_AOE2LOBBY_URI", ""), "aoe2"),
        ("ext-librematch", os.environ.get("EXT_LIBREMATCH_URI", ""), "aoe2"),
    ]
    tasks = [_consume_one(aggregator, provider_id, uri, game_key) for provider_id, uri, game_key in providers if uri]
    if not tasks:
        aggregator.mark_degraded()
        return
    await asyncio.gather(*tasks)


async def _watch_provider_health(aggregator: LiveAggregator, client: Any) -> None:
    """Ping one provider periodically; a reply clears the degraded flag.

    Streams may legitimately be silent for long stretches (a poll-only
    provider with no open lobbies yields nothing for minutes), so stream
    silence is not a health signal — an explicit capabilities ping is.
    A provider that stops answering re-flags the aggregator degraded.
    """
    import asyncio

    interval_s = float(os.environ.get("PROVIDER_HEALTH_INTERVAL_S", "30"))
    while True:
        try:
            await client.get_capabilities()
            aggregator.mark_healthy()
        except Exception:
            aggregator.mark_degraded()
        await asyncio.sleep(interval_s)


async def _consume_one(aggregator: LiveAggregator, provider_id: str, uri: str, game_key: str) -> None:
    """Consume one provider stream forever, degrading on silence."""
    import asyncio

    from kingdoms.core.rpc.game_client import GameProviderClient

    client = GameProviderClient(uri, provider_id, game_key)
    import asyncio as _asyncio

    health = _asyncio.ensure_future(_watch_provider_health(aggregator, client))
    try:
        while True:
            try:
                async for event in client.stream_match_events(since=0):
                    await aggregator.apply_event(event)
            except Exception:
                logger.warning("provider %s stream lost; retrying in 5s", provider_id)
                await asyncio.sleep(5)
            await asyncio.sleep(1)
    finally:
        health.cancel()


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
