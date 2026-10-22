"""Live dashboard seam — server side and client (kingdoms.v1.Live, #147).

svc-core serves WatchDashboard (server-streaming snapshots) and
WatchPlayerEvents (the normalized player status transitions); the bot
consumes them through ``LiveClient`` to render /live and to dispatch
transitions to the enrolled mods. The seam only translates between
the wire contract and the aggregator's snapshots.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import grpc

from kingdoms.rpc_generated.kingdoms.v1 import live_pb2, live_pb2_grpc


def state_to_wire(snapshot: dict[str, Any]) -> live_pb2.DashboardState:
    """Translate one aggregator snapshot into the wire message."""
    players = [
        live_pb2.PlayerState(
            user_id=p["user_id"],
            profile_id=p["profile_id"],
            state=p["state"],
            match_ref=p["match_ref"],
            since=p["since"],
        )
        for p in snapshot["players"]
    ]
    return live_pb2.DashboardState(
        players=players,
        generated_at=snapshot["generated_at"],
        degraded=snapshot["degraded"],
    )


def transition_to_wire(transition: dict[str, Any]) -> live_pb2.PlayerStatusWireEvent:
    """Translate one normalized player transition into the wire message."""
    return live_pb2.PlayerStatusWireEvent(
        game_key=str(transition.get("game_key", "")),
        profile_id=str(transition.get("profile_id", "")),
        user_id=str(transition.get("user_id", "")),
        old_status=str(transition.get("old_status", "")),
        new_status=str(transition.get("new_status", "")),
        match_ref=str(transition.get("match_ref", "")),
        match_url=str(transition.get("match_url", "")),
        occurred_at=int(transition.get("occurred_at", 0)),
    )


def transition_from_wire(wire: live_pb2.PlayerStatusWireEvent) -> dict[str, Any]:
    """Translate a wire transition back into the plain dict shape."""
    return {
        "game_key": wire.game_key,
        "profile_id": wire.profile_id,
        "user_id": wire.user_id,
        "old_status": wire.old_status,
        "new_status": wire.new_status,
        "match_ref": wire.match_ref,
        "match_url": wire.match_url,
        "occurred_at": wire.occurred_at,
    }


class LiveServicer(live_pb2_grpc.LiveServicer):
    """Serve WatchDashboard from the live aggregator."""

    def __init__(self, aggregator: Any, poll_interval_s: float = 2.0) -> None:
        """Wire the aggregator and the snapshot refresh interval."""
        self._aggregator = aggregator
        self._poll_interval_s = poll_interval_s

    async def WatchPlayerEvents(
        self,
        request: live_pb2.WatchPlayerEventsRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[live_pb2.PlayerStatusWireEvent]:
        """Stream normalized player transitions as the aggregator queues them.

        The consumer (the bot process) dispatches each transition to the
        mods where the player is enrolled — the core's generic player
        events mechanism, served across the process seam.
        """
        while True:
            transition = await self._aggregator.next_transition()
            if request.game_key and transition.get("game_key") != request.game_key:
                continue
            if request.since and transition.get("occurred_at", 0) < request.since:
                continue
            yield transition_to_wire(transition)

    async def WatchDashboard(
        self,
        request: live_pb2.WatchDashboardRequest,
        context: grpc.aio.ServicerContext,
    ):
        """Stream snapshots: one immediately, then on every poll tick.

        The aggregator folds provider events in the background loop of
        the core process; this stream polls its snapshot, emitting only
        when the payload changed so the channel never spams.
        """
        import asyncio

        last: bytes | None = None
        while True:
            snapshot = await self._aggregator.snapshot(request.game_key)
            wire = state_to_wire(snapshot)
            payload = wire.SerializeToString()
            if payload != last:
                yield wire
                last = payload
            await asyncio.sleep(self._poll_interval_s)


def add_live_servicer(server: grpc.aio.Server, servicer: LiveServicer) -> None:
    """Bind the Live servicer onto the server."""
    live_pb2_grpc.add_LiveServicer_to_server(servicer, server)


class LiveClient:
    """Typed client for the kingdoms.v1.Live service (bot side)."""

    def __init__(self, core_uri: str) -> None:
        """Keep the core address; channels are per-call (ADR-0020)."""
        self._core_uri = core_uri

    async def watch(self, game_key: str, timeout_s: float = 5.0) -> dict[str, Any]:
        """Fetch the latest dashboard snapshot (first stream frame)."""
        from kingdoms.core.rpc.client import build_channel

        async with build_channel(self._core_uri) as channel:
            stub = live_pb2_grpc.LiveStub(channel)
            call = stub.WatchDashboard(
                live_pb2.WatchDashboardRequest(game_key=game_key),
                timeout=timeout_s,
            )
            async for frame in call:
                return {
                    "players": [
                        {
                            "user_id": p.user_id,
                            "profile_id": p.profile_id,
                            "state": p.state,
                            "match_ref": p.match_ref,
                            "since": p.since,
                        }
                        for p in frame.players
                    ],
                    "generated_at": frame.generated_at,
                    "degraded": frame.degraded,
                }
        return {"players": [], "generated_at": 0, "degraded": True}

    async def stream_snapshots(self, game_key: str):
        """Yield dashboard snapshots continuously: one per player-state change."""
        from kingdoms.core.rpc.client import build_channel

        async with build_channel(self._core_uri) as channel:
            stub = live_pb2_grpc.LiveStub(channel)
            call = stub.WatchDashboard(live_pb2.WatchDashboardRequest(game_key=game_key))
            async for frame in call:
                yield {
                    "players": [
                        {
                            "user_id": p.user_id,
                            "profile_id": p.profile_id,
                            "state": p.state,
                            "match_ref": p.match_ref,
                            "since": p.since,
                        }
                        for p in frame.players
                    ],
                    "generated_at": frame.generated_at,
                    "degraded": frame.degraded,
                }

    async def stream_player_events(self, game_key: str = ""):
        """Yield normalized player transitions continuously, forever.

        The stream reconnects silently on core hiccups (the caller's
        loop retries); each transition is the plain dict shape of the
        player_events mechanism, ready for mod dispatch.
        """
        from kingdoms.core.rpc.client import build_channel

        while True:
            try:
                async with build_channel(self._core_uri) as channel:
                    stub = live_pb2_grpc.LiveStub(channel)
                    call = stub.WatchPlayerEvents(
                        live_pb2.WatchPlayerEventsRequest(game_key=game_key)
                    )
                    async for wire in call:
                        yield transition_from_wire(wire)
            except grpc.aio.AioRpcError:
                import asyncio

                await asyncio.sleep(5)
