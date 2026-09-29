"""Game provider RPC seam — client side (kingdoms.v1.Game).

svc-core consumes game providers through these typed helpers. Every call
degrades cleanly: an unreachable provider declares zero capabilities and
the core keeps running on the manual path; an explicitly unsupported
capability surfaces as ``UnsupportedCapabilityError`` for the caller to catch.
"""

from __future__ import annotations

import grpc

from kingdoms.core.models.game import (
    GameMap,
    MatchDetails,
    ProviderCapabilities,
)
from kingdoms.core.rpc.client import build_channel, call_with_retry
from kingdoms.core.rpc.game import (
    game_map_from_wire,
    match_details_from_wire,
)
from kingdoms.rpc_generated.kingdoms.v1 import game_pb2, game_pb2_grpc

_RETRYABLE_CODES: tuple[grpc.StatusCode, ...] = (
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
)


class UnsupportedCapabilityError(RuntimeError):
    """The provider does not support this capability (clean degradation)."""


class GameProviderClient:
    """Typed client for a kingdoms.v1.Game provider process."""

    def __init__(self, provider_uri: str, provider_id: str, game_key: str) -> None:
        self._provider_uri = provider_uri
        self._provider_id = provider_id
        self._game_key = game_key

    @property
    def provider_id(self) -> str:
        """Provider identity used in logs and audit trails."""
        return self._provider_id

    async def get_capabilities(self) -> ProviderCapabilities:
        """Fetch the provider's declared capabilities, degrading to zero."""
        async with build_channel(self._provider_uri) as channel:
            stub = game_pb2_grpc.GameStub(channel)
            try:
                reply = await call_with_retry(
                    stub.GetCapabilities,
                    game_pb2.CapabilitiesRequest(),
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
                    raise UnsupportedCapabilityError(self._provider_id) from exc
                # An unreachable provider declares nothing: the core keeps
                # running on the degraded (manual) path.
                return ProviderCapabilities.none(self._provider_id, self._game_key)
        return ProviderCapabilities(
            provider_id=reply.provider_id,
            game_key=reply.game_key,
            realtime=reply.realtime,
            reliable_results=reply.reliable_results,
            check_map=reply.check_map,
            player_stats=reply.player_stats,
        )

    async def get_match_details(self, match_ref: str) -> MatchDetails | None:
        """Fetch a match's slotinfo and raw options, degrading to None.

        A provider that cannot serve match details (unsupported or
        unknown match) yields None: callers fall back to what the match
        already knows instead of failing the workflow.
        """
        async with build_channel(self._provider_uri) as channel:
            stub = game_pb2_grpc.GameStub(channel)
            try:
                reply = await call_with_retry(
                    stub.GetMatchDetails,
                    game_pb2.GetMatchDetailsRequest(match_ref=match_ref),
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() in (
                    grpc.StatusCode.UNIMPLEMENTED,
                    grpc.StatusCode.NOT_FOUND,
                ):
                    return None
                raise
        return match_details_from_wire(reply)

    async def list_maps(self) -> list[GameMap]:
        """Fetch the provider's map catalog, degrading to an empty list."""
        async with build_channel(self._provider_uri) as channel:
            stub = game_pb2_grpc.GameStub(channel)
            try:
                reply = await call_with_retry(
                    stub.ListMaps,
                    game_pb2.ListMapsRequest(),
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
                    return []
                raise
        return [game_map_from_wire(m) for m in reply.maps]
