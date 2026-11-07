"""game-aoe2 process entrypoint (kingdoms.v1.GameData server).

The single consumer of the AoE2 providers (developer direction):
consumes their ``StreamMatchEvents`` (ext-aoe2lobby realtime,
ext-librematch source of truth), folds them into the enriched
per-player state, and serves the GameData contract (dashboard,
player events, enrollments) to svc-core and the bot.

``UpdateEnrollments`` (pushed by svc-core) is reconciled down to the
realtime provider through its own ``UpdateSubscriptions`` — svc-core
never talks to the providers directly, in either direction.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("kingdoms.game_aoe2")


def main() -> None:
    """Run the game-aoe2 gRPC GameData server."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_serve())


async def _serve() -> None:
    from kingdoms import __version__
    from kingdoms.core.rpc.status import CoreStatus, build_status_server
    from kingdoms.game_aoe2 import GAME_KEY, PROCESS_LABEL
    from kingdoms.game_aoe2.aggregator import GameAoe2Aggregator
    from kingdoms.game_aoe2.rpc import GameDataServicer, add_gamedata_servicer

    aggregator = GameAoe2Aggregator()
    enrollments = _EnrollmentPusher(aggregator)
    # The stats proxy: the game service owns its librematch provider
    # (rule 4 — the bot and the mods ask the service, never the ext).
    stats_provider = None
    stats_uri = os.environ.get("EXT_LIBREMATCH_URI", "")
    if stats_uri:
        from kingdoms.core.rpc.game_client import GameProviderClient

        stats_provider = GameProviderClient(stats_uri, "ext-librematch", GAME_KEY)
    # The map seed door: the game's liquipedia content provider (the
    # import flow's enrichment rides it through the GetMapSeed proxy).
    map_seeds = None
    seeds_uri = os.environ.get("EXT_LIQUIPEDIA_URI", "")
    if seeds_uri:
        from kingdoms.core.rpc.content_client import ContentProviderClient

        map_seeds = ContentProviderClient(seeds_uri)
    # The catalog gateway (rule 5): the game's collections answer from
    # the owner's process — the service + the content store, composed.
    from kingdoms.core.models.db import get_async_database as _get_db
    from kingdoms.core.services.faction_content import FactionContentService
    from kingdoms.core.services.game_data import GameDataService

    from .seed import MongoAoE2Database

    _database = _get_db()
    catalog = _CatalogGateway(
        GameDataService(MongoAoE2Database(_database)), FactionContentService(_database)
    )

    server = build_status_server(
        lambda: CoreStatus(version=__version__, process=PROCESS_LABEL),
    )
    add_gamedata_servicer(
        server,
        GameDataServicer(
            aggregator,
            enrollments=enrollments,
            stats_provider=stats_provider,
            map_seeds=map_seeds,
            catalog=catalog,
        ),
    )

    # The game's collections are ITS OWN (rule 5): the indexes are
    # ensured here, at the game service's boot.
    from kingdoms.core.models.db import get_async_database

    from .db_indexes import ensure_game_indexes

    await ensure_game_indexes(get_async_database())

    consumer = asyncio.ensure_future(_consume_providers(aggregator))
    # No synthetic probe (owner direction 2026-11-05): a probe every
    # minute is indistinguishable from real traffic for the rate-limited
    # upstreams. The providers are consumed by their streams; nothing
    # pings anyone.

    port = os.environ.get("GAME_GRPC_PORT", "50071")
    bind = f"[::]:{port}"
    server.add_insecure_port(bind)
    await server.start()
    logger.info("GAME_AOE2_READY port=%s version=%s game=%s", port, __version__, GAME_KEY)
    try:
        await server.wait_for_termination()
    finally:
        consumer.cancel()


class _EnrollmentPusher:
    """Reconcile the platform's enrollments: roster + provider subscription.

    The enrollments carry the roster (profile ids + their platform
    users) — the dashboard's baseline, so every enrolled player
    renders even before any transition fired — and the realtime
    provider's ``UpdateSubscriptions`` (in-place deltas on its single
    WebSocket session, no teardown).
    """

    def __init__(self, aggregator: Any) -> None:
        self._aggregator = aggregator
        self._last_ids: list[str] | None = None
        self._client: Any = None

    async def apply(
        self, game_key: str, profile_ids: list[str], user_ids: list[str] | None = None
    ) -> tuple[int, bool]:
        """Record the roster, then push the subscription down."""
        self._aggregator.set_roster(
            [
                {"profile_id": pid, "user_id": uid}
                for pid, uid in zip(profile_ids, user_ids or [], strict=False)
            ]
            or profile_ids
        )
        """Push one enrollment list to the realtime provider for the game."""
        if game_key != "aoe2":
            return 0, False
        uri = os.environ.get("EXT_AOE2LOBBY_URI", "")
        if not uri:
            self._last_ids = None
            return len(profile_ids), False
        try:
            from kingdoms.core.rpc.game_client import GameProviderClient

            if self._client is None:
                self._client = GameProviderClient(uri, "ext-aoe2lobby", "aoe2")
            applied = await self._client.update_subscriptions(profile_ids)
            if applied:
                self._last_ids = list(profile_ids)
            return len(profile_ids), applied
        except Exception:
            logger.warning("enrollment push to the realtime provider failed", exc_info=True)
            return len(profile_ids), False


async def _consume_providers(aggregator: Any) -> None:
    """Fold every configured provider's event stream into the aggregator."""
    providers = [
        ("ext-aoe2lobby", os.environ.get("EXT_AOE2LOBBY_URI", "")),
        ("ext-librematch", os.environ.get("EXT_LIBREMATCH_URI", "")),
    ]
    tasks = [
        _consume_one(aggregator, provider_id, uri)
        for provider_id, uri in providers
        if uri
    ]
    if not tasks:
        aggregator.mark_degraded()
        return
    await asyncio.gather(*tasks)


async def _consume_one(aggregator: Any, provider_id: str, uri: str) -> None:
    """Consume one provider stream forever, degrading on silence."""
    from kingdoms.core.rpc.game_client import GameProviderClient

    client = GameProviderClient(uri, provider_id, "aoe2")
    health = asyncio.ensure_future(_watch_provider_health(aggregator, client))
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


async def _watch_provider_health(aggregator: Any, client: Any) -> None:
    """Ping one provider periodically; a reply clears the degraded flag."""
    interval_s = float(os.environ.get("PROVIDER_HEALTH_INTERVAL_S", "30"))
    while True:
        try:
            await client.get_capabilities()
            aggregator.mark_healthy()
        except Exception:
            aggregator.mark_degraded()
        await asyncio.sleep(interval_s)


class _CatalogGateway:
    """The game's catalog + content, composed for the gateway dispatch.

    The GameDataService owns the game's collections (maps, pools,
    packs, factions); the FactionContentService owns the localized
    content docs. The gateway dispatches to either — one door, two
    owners, both in the owner's process (rule 5).
    """

    def __init__(self, service: Any, content: Any) -> None:
        self._service = service
        self._content = content

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    async def content_store(self, content: dict[str, Any]) -> None:
        """Store one localized content doc (the enrichment's write)."""
        await self._content.store(content)

    async def content_get(self, entity_id: str, locale: str) -> dict[str, Any] | None:
        """Read one localized content doc (the enrichment's read)."""
        result: dict[str, Any] | None = await self._content.get(entity_id, locale)
        return result


if __name__ == "__main__":
    main()
