"""game-aoe2 RPC seam — the kingdoms.v1.GameData servicer.

Translates between the GameData contract and the aggregator's
snapshots/transitions. The match summaries ride the wire as the
``GameMatchSummary`` message (map, format, slots, host, players with
civs, aoe2lobby join/spectate URLs, aoe2de deep link).
"""

from __future__ import annotations

import logging
from typing import Any

import grpc

from kingdoms.core.rpc.game import (
    capabilities_to_wire,
    match_result_to_wire,
    player_stats_to_wire,
)
from kingdoms.rpc_generated.kingdoms.v1 import gamedata_pb2, gamedata_pb2_grpc

logger = logging.getLogger("kingdoms.game_aoe2")


def summary_to_wire(summary: dict[str, Any]) -> gamedata_pb2.GameMatchSummary:
    """Translate one match summary dict into the wire message.

    The summary's ``players`` field rides as the compact
    ``Name(Civ),Name(Civ)`` string (the adapter's metadata shape); it is
    parsed into the structured slots here so the wire stays typed, and
    rejoined on the way back (round-trip stable).
    """
    return gamedata_pb2.GameMatchSummary(
        match_ref=str(summary.get("match_ref", "")),
        map_name=str(summary.get("map", "")),
        format=str(summary.get("format", "")),
        status=str(summary.get("status", "")),
        slots_taken=int(summary.get("slots_taken") or 0),
        slots_total=int(summary.get("slots_total") or 0),
        host_name=str(summary.get("host", "")),
        players=_parse_players(str(summary.get("players", ""))),
        lobby_url=str(summary.get("lobby_url", "")),
        spectate_url=str(summary.get("spectate_url", "")),
        deep_link=str(summary.get("deep_link", "")),
        match_type=str(summary.get("match_type", "")),
    )


def _parse_players(compact: str) -> list[gamedata_pb2.GamePlayerSlot]:
    """Parse the summary's players JSON into typed slots.

    The adapter ships the occupied slots as a JSON array
    (profile_id, name, civ, team) — the wire stays typed, the
    consumers (dashboard rendering, mod notifications) never re-parse
    display strings.
    """
    import json

    try:
        entries = json.loads(compact) if compact else []
    except (json.JSONDecodeError, TypeError):
        return []
    slots: list[gamedata_pb2.GamePlayerSlot] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        slots.append(
            gamedata_pb2.GamePlayerSlot(
                profile_id=str(entry.get("profile_id", "")),
                name=str(entry.get("name", "")),
                civilization=str(entry.get("civ", "")),
                team=int(entry.get("team", 0) or 0),
                filled=True,
                avatar_url=str(entry.get("avatar", "")),
                country=str(entry.get("country", "")),
            )
        )
    return slots


def player_state_to_wire(state: dict[str, Any]) -> gamedata_pb2.GamePlayerState:
    """Translate one player state dict into the wire message."""
    summary = state.get("match") or {}
    return gamedata_pb2.GamePlayerState(
        user_id=str(state.get("user_id", "")),
        profile_id=str(state.get("profile_id", "")),
        state=str(state.get("state", "offline")),
        match_ref=str(state.get("match_ref", "")),
        since=int(state.get("since", 0)),
        match=summary_to_wire(summary) if summary else None,
    )


def dashboard_to_wire(snapshot: dict[str, Any]) -> gamedata_pb2.GameDashboard:
    """Translate one aggregator snapshot into the wire message."""
    return gamedata_pb2.GameDashboard(
        players=[player_state_to_wire(p) for p in snapshot.get("players", [])],
        generated_at=int(snapshot.get("generated_at", 0)),
        degraded=bool(snapshot.get("degraded", False)),
    )


def event_to_wire(transition: dict[str, Any], game_key: str = "aoe2") -> gamedata_pb2.GamePlayerEvent:
    """Translate one normalized transition into the wire message."""
    return gamedata_pb2.GamePlayerEvent(
        game_key=game_key,
        profile_id=str(transition.get("profile_id", "")),
        user_id=str(transition.get("user_id", "")),
        old_status=str(transition.get("old_status", "")),
        new_status=str(transition.get("new_status", "")),
        occurred_at=int(transition.get("occurred_at", 0)),
        match=summary_to_wire(transition.get("match") or {}) or None,
    )


def dashboard_from_wire(wire: gamedata_pb2.GameDashboard) -> dict[str, Any]:
    """Translate a wire dashboard back into the plain dict shape."""
    return {
        "players": [
            {
                "user_id": p.user_id,
                "profile_id": p.profile_id,
                "state": p.state,
                "match_ref": p.match_ref,
                "since": p.since,
                "match": summary_from_wire(p.match) if p.HasField("match") else {},
            }
            for p in wire.players
        ],
        "generated_at": wire.generated_at,
        "degraded": wire.degraded,
    }


def event_from_wire(wire: gamedata_pb2.GamePlayerEvent) -> dict[str, Any]:
    """Translate a wire player event back into the plain dict shape."""
    return {
        "game_key": wire.game_key,
        "profile_id": wire.profile_id,
        "user_id": wire.user_id,
        "old_status": wire.old_status,
        "new_status": wire.new_status,
        "occurred_at": wire.occurred_at,
        "match": summary_from_wire(wire.match) if wire.HasField("match") else {},
    }


def summary_from_wire(wire: gamedata_pb2.GameMatchSummary) -> dict[str, Any]:
    """Translate a wire match summary back into the plain dict shape."""
    return {
        "match_ref": wire.match_ref,
        "map": wire.map_name,
        "format": wire.format,
        "status": wire.status,
        "slots_taken": str(wire.slots_taken),
        "slots_total": str(wire.slots_total),
        "host": wire.host_name,
        "players": [
            {
                "profile_id": p.profile_id,
                "name": p.name,
                "civ": p.civilization,
                "avatar": p.avatar_url,
                "country": p.country,
                "team": p.team,
            }
            for p in wire.players
        ],
        "match_type": wire.match_type,
        "lobby_url": wire.lobby_url,
        "spectate_url": wire.spectate_url,
        "deep_link": wire.deep_link,
    }


class GameDataServicer(gamedata_pb2_grpc.GameDataServicer):
    """Serve the kingdoms.v1.GameData contract from the aggregator."""

    def __init__(
        self,
        aggregator: Any,
        poll_interval_s: float = 2.0,
        enrollments: Any = None,
        stats_provider: Any = None,
        map_seeds: Any = None,
        catalog: Any = None,
    ) -> None:
        """Wire the aggregator, the poll interval, the enrollments and the stats proxy."""
        self._aggregator = aggregator
        self._poll_interval_s = poll_interval_s
        self._enrollments = enrollments
        self._stats_provider = stats_provider
        self._map_seeds = map_seeds
        self._catalog = catalog

    async def GetCoverage(
        self,
        request: gamedata_pb2.CoverageRequest,
        context: grpc.aio.ServicerContext,
    ) -> gamedata_pb2.GameCoverage:
        """Serve the games this process covers and its health."""
        return gamedata_pb2.GameCoverage(
            game_keys=["aoe2"],
            realtime=True,
            degraded=self._aggregator.snapshot()["degraded"],
        )

    async def WatchDashboard(
        self,
        request: gamedata_pb2.WatchGameDataRequest,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Stream snapshots: one immediately, then one per effective change.

        The aggregator folds the provider (aoe2lobby) events in the
        background consumer; this stream polls its snapshot, emitting
        only when the payload changed so the channel never spams.
        """
        import asyncio

        last: Any = None
        while True:
            snapshot = self._aggregator.snapshot()
            if last is None or snapshot["players"] != last:
                last = snapshot["players"]
                yield dashboard_to_wire(snapshot)
            await asyncio.sleep(self._poll_interval_s)

    async def WatchPlayerEvents(
        self,
        request: gamedata_pb2.WatchGameDataRequest,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Stream the player transitions to every subscriber (fan-out).

        The service subscribes to its provider (aoe2lobby) in its
        background consumer, maintains the coherent player state (the
        aggregator) and serves its subscribers — a mod, the bot: each
        stream owns its queue, one consumer never starves another.
        """
        queue = self._aggregator.subscribe()
        try:
            while True:
                transition = await queue.get()
                if request.game_key and transition.get("game_key") != request.game_key:
                    continue
                if request.since and int(transition.get("occurred_at", 0)) < request.since:
                    continue
                match = transition.get("match") or {}
                yield gamedata_pb2.GamePlayerEvent(
                    game_key=str(transition.get("game_key", "")),
                    profile_id=str(transition.get("profile_id", "")),
                    user_id=str(transition.get("user_id", "")),
                    old_status=str(transition.get("old_status", "")),
                    new_status=str(transition.get("new_status", "")),
                    occurred_at=int(transition.get("occurred_at", 0)),
                    match=summary_to_wire(match) if match else None,
                )
        finally:
            self._aggregator.unsubscribe(queue)

    async def SyncContent(
        self,
        request: gamedata_pb2.SyncContentRequest,
        context: grpc.aio.ServicerContext,
    ) -> gamedata_pb2.SyncContentReply:
        """Run the content sync against the game's providers, in here.

        The civs sync rides the aoe2techtree content provider, the maps
        enrichment rides the liquipedia provider — the game service owns
        both (the bot never touches a provider, it asks this service).
        A provider failure comes back as a FUNCTIONAL error the UI shows
        as-is; it never crashes the servicer.
        """
        from kingdoms.game_aoe2.content_refresh import (
            refresh_aoe2_content,
            refresh_aoe2_maps,
        )

        try:
            if request.what == "factions":
                result = await refresh_aoe2_content()
            elif request.what == "maps":
                result = await refresh_aoe2_maps()
            else:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"unknown sync target: {request.what!r}",
                )
                raise AssertionError  # abort never returns
        except Exception as exc:  # a functional error, not a crash
            logger.warning("SyncContent(%s) failed: %s", request.what, exc)
            return gamedata_pb2.SyncContentReply(
                ok=False,
                error=f"{type(exc).__name__}: {exc}"[:400],
            )
        counts = {
            key: int(value)
            for key, value in result.items()
            if isinstance(value, int)
        }
        raw_new = result.get("new_factions", ())
        new_factions = (
            [str(name) for name in raw_new if isinstance(name, str)]
            if isinstance(raw_new, (list, tuple))
            else []
        )
        return gamedata_pb2.SyncContentReply(
            ok=True,
            counts=counts,
            new_factions=new_factions,
        )

    async def GetProfileStats(
        self,
        request: Any,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Proxy the profile stats through the game's stats provider.

        The bot and the mods ask the GAME SERVICE (rule 4); the service
        asks its provider — the caller never sees the provider.
        """
        if self._stats_provider is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "stats provider not wired")
            raise AssertionError
        stats = await self._stats_provider.get_player_stats(request.profile_id)
        if stats is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "no stats for this profile")
            raise AssertionError
        return player_stats_to_wire(stats)

    async def GetMatchResult(
        self,
        request: Any,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Proxy a finished match's outcome (the activity feed's cards)."""
        if self._stats_provider is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "stats provider not wired")
            raise AssertionError
        result = await self._stats_provider.get_match_result(
            list(request.profile_ids), int(request.started_after)
        )
        if result is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "no result yet")
            raise AssertionError
        return match_result_to_wire(result)

    async def GetProviderCapabilities(
        self,
        request: Any,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Proxy the stats provider's declared capabilities."""
        if self._stats_provider is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "stats provider not wired")
            raise AssertionError
        caps = await self._stats_provider.get_capabilities()
        return capabilities_to_wire(caps)

    async def GetLastCompletedTimes(
        self,
        request: Any,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Aggregate the profiles' last completed-match times (rule 5).

        ``matches`` is the GAME's collection — the aggregation runs
        here, in the owner's process; the callers never read it raw.
        """
        from kingdoms.core.models.db import get_async_database

        database = get_async_database()
        times = await _last_completed_matches_ms(database, list(request.profile_ids))
        return gamedata_pb2.GetLastCompletedTimesReply(times_ms=times)

    async def GetMapSeed(
        self,
        request: Any,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Proxy the map seed lookup through the game's liquipedia provider.

        The import flow (the admin's map creation) asks the GAME SERVICE;
        the service asks its content provider — the caller never sees
        the provider (rule 4), and the seed worker's coalescing, pacing
        and circuit breaker apply as one.
        """
        if self._map_seeds is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "map seed provider not wired")
            raise AssertionError
        from kingdoms.rpc_generated.kingdoms.v1 import content_pb2

        logger.info(
            "GET MAP SEED: %r (game %s) — proxying to the liquipedia provider",
            request.map_key,
            request.game_key,
        )
        try:
            descriptor = await self._map_seeds.get_map_seed(
                request.game_key, request.map_key
            )
        except Exception as exc:
            logger.warning("GET MAP SEED: %r failed: %s", request.map_key, exc)
            raise
        if descriptor is None:
            return content_pb2.MapSeedReply(found=False)
        return _map_seed_reply_of(descriptor)

    async def Call(
        self,
        request: Any,
        context: grpc.aio.ServicerContext,
    ) -> Any:
        """Dispatch one catalog op over the game's service (the gateway).

        The game's collections (maps, pools, packs, factions, content)
        answer from the OWNER's process (rule 5). The service's
        validation, audit and lifecycle logic stay THE logic — the
        gateway is a thin, allowlisted dispatch; an unknown op is a
        protocol error, a service error a functional reply.
        """
        import json

        if self._catalog is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "catalog not wired")
            raise AssertionError
        op = request.op
        if op not in _CATALOG_OPS:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"unknown op {op!r} — the gateway routes only: "
                f"{', '.join(sorted(_CATALOG_OPS))}",
            )
            raise AssertionError
        method = getattr(self._catalog, op)
        try:
            kwargs = json.loads(request.payload_json) if request.payload_json else {}
            result = await method(**kwargs)
        except Exception as exc:
            logger.warning("catalog op %s failed: %s", op, exc)
            return gamedata_pb2.GameDataCallReply(
                ok=False,
                error_kind=type(exc).__name__,
                error=str(exc)[:400],
            )
        return gamedata_pb2.GameDataCallReply(
            ok=True,
            result_json=json.dumps(_jsonable(result)),
        )

    async def UpdateEnrollments(
        self,
        request: gamedata_pb2.UpdateEnrollmentsRequest,
        context: grpc.aio.ServicerContext,
    ) -> gamedata_pb2.UpdateEnrollmentsResponse:
        """Forward the platform's enrollments to the realtime provider."""
        if self._enrollments is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "enrollments not wired")
        subscribed, applied = await self._enrollments.apply(
            request.game_key, list(request.profile_ids), list(request.user_ids)
        )
        return gamedata_pb2.UpdateEnrollmentsResponse(subscribed=subscribed, applied=applied)


def add_gamedata_servicer(server: grpc.aio.Server, servicer: GameDataServicer) -> None:
    """Bind the GameData servicer onto the server."""
    gamedata_pb2_grpc.add_GameDataServicer_to_server(servicer, server)


async def _last_completed_matches_ms(database: Any, profile_ids: list[str]) -> dict[str, int]:
    """Aggregate every profile's most recent completed-match time.

    ONE aggregation over the whole batch (the panel is immediate): the
    completed matches group by profile, the max completion time rides
    one reply. Zero means no completed match yet. ``matches`` is the
    GAME's collection (rule 5) — the aggregation lives with its owner.
    """
    if not profile_ids:
        return {}
    pipeline: list[dict[str, Any]] = [
        {"$match": {"status": {"$regex": "^completed$", "$options": "i"},
                    "$or": [{"host_profile_id": {"$in": profile_ids}},
                            {"guest_profile_id": {"$in": profile_ids}}]}},
        {"$project": {"host": "$host_profile_id", "guest": "$guest_profile_id",
                      "times": [{"$ifNull": ["$completed_at", 0]},
                                 {"$ifNull": ["$game_completed_at", 0]}]}},
        {"$project": {"sides": ["$host", "$guest"],
                      "time": {"$max": "$times"}}},
        {"$unwind": "$sides"},
        {"$match": {"sides": {"$in": profile_ids}}},
        {"$group": {"_id": {"$toString": "$sides"}, "last": {"$max": "$time"}}},
    ]
    results: dict[str, int] = {}
    try:
        cursor: Any = await database["matches"].aggregate(pipeline)
        async for doc in cursor:
            value = doc.get("last")
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            if isinstance(value, int) and value > 0:
                results[str(doc.get("_id", ""))] = value
    except Exception:
        logger.warning("last-completed aggregation failed", exc_info=True)
    return results


def _map_seed_reply_of(descriptor: dict[str, Any]) -> Any:
    """Serialize one map seed descriptor into the Content wire reply."""
    from kingdoms.rpc_generated.kingdoms.v1 import content_pb2

    return content_pb2.MapSeedReply(
        found=True,
        name=str(descriptor.get("name", "")),
        description=str(descriptor.get("description", "")),
        resource_url=str(descriptor.get("resource_url", "")),
        image_url=str(descriptor.get("image_url", "")),
        icon_url=str(descriptor.get("icon_url", "")),
        terrain=str(descriptor.get("terrain", "")),
        size=str(descriptor.get("size", "")),
        map_type=str(descriptor.get("map_type", "")),
        licence_ok=bool(descriptor.get("licence_ok", True)),
        licence_note=str(descriptor.get("licence_note", "")),
        walls=bool(descriptor.get("walls", False)),
        nomad=bool(descriptor.get("nomad", False)),
        tc=bool(descriptor.get("tc", False)),
        game_versions=[str(v) for v in descriptor.get("game_versions", ()) or ()],
        versions=[
            content_pb2.MapSeedVersion(
                name=str(v.get("name", "")),
                description=str(v.get("description", "")),
                details=[str(d) for d in v.get("details", ()) or ()],
            )
            for v in descriptor.get("versions", ()) or ()
        ],
        tags=[str(t) for t in descriptor.get("tags", ()) or ()],
    )


# The allowlisted catalog ops (the GameDataService surface the bot uses).
# An op outside this table answers INVALID_ARGUMENT — the gateway is a
# door, never an open getattr.
_CATALOG_OPS: frozenset[str] = frozenset(
    {
        "list_factions",
        "list_maps",
        "list_map_pools",
        "list_all_map_pools",
        "list_game_keys",
        "get_map",
        "get_map_pool",
        "get_map_pack",
        "create_map",
        "create_map_pool",
        "create_map_pack",
        "update_map",
        "update_map_pool",
        "transition_map_pool",
        "delete_map",
        "archive_map",
        "archive_map_pool",
        "archive_map_pack",
        "set_map_forum_message",
        "set_pool_forum_thread",
        "hard_delete_map_pool",
        "duplicate_map_pool",
        "resolve_pool_map_ids",
        "send_map_pool_to_guild",
        "content_store",
        "content_get",
    }
)


def _jsonable(value: Any) -> Any:
    """Serialize a service result into JSON-safe data.

    The catalog models are pydantic: ``model_dump`` carries the wire
    shape (aliases included); tuples become lists; the rest passes.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return str(value)
