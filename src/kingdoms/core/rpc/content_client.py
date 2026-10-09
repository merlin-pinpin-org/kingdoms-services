"""Game content RPC seam — client side (kingdoms.v1.Content).

svc-core consumes a content provider process (ext-aoe2techtree)
through these typed helpers. Every call degrades cleanly: an
unreachable provider or an unknown key yields None/empty so the
caller (content refresh) falls back to the local dataset source.
"""

from __future__ import annotations

import grpc

from kingdoms.core.rpc.client import build_channel
from kingdoms.rpc_generated.kingdoms.v1 import content_pb2, content_pb2_grpc

_NOT_FOUND_CODES: tuple[grpc.StatusCode, ...] = (
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.UNIMPLEMENTED,
    grpc.StatusCode.UNAVAILABLE,
)


class ContentProviderClient:
    """Typed client for a kingdoms.v1.Content provider process."""

    def __init__(self, provider_uri: str) -> None:
        self._provider_uri = provider_uri

    async def list_factions(self) -> list[str]:
        """Fetch the provider's faction index, degrading to an empty list."""
        async with build_channel(self._provider_uri) as channel:
            stub = content_pb2_grpc.ContentStub(channel)
            try:
                reply = await stub.ListFactions(content_pb2.ListFactionsRequest())
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.UNAVAILABLE:
                    return []
                raise
        return list(reply.faction_keys)

    async def get_faction_content(self, faction_key: str, locale: str) -> dict[str, str] | None:
        """Fetch one faction's localized descriptor, degrading to None."""
        async with build_channel(self._provider_uri) as channel:
            stub = content_pb2_grpc.ContentStub(channel)
            try:
                reply = await stub.GetFactionContent(
                    content_pb2.FactionContentRequest(faction_key=faction_key, locale=locale)
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() in _NOT_FOUND_CODES:
                    return None
                raise
        return {
            "entity_id": reply.entity_id,
            "locale": reply.locale,
            "name": reply.name,
            "summary": reply.summary,
            "source_url": reply.source_url,
            "image_url": reply.image_url,
        }

    async def get_map_content(self, map_key: str, locale: str) -> dict[str, str] | None:
        """Fetch one map's localized descriptor, degrading to None."""
        async with build_channel(self._provider_uri) as channel:
            stub = content_pb2_grpc.ContentStub(channel)
            try:
                reply = await stub.GetMapContent(content_pb2.MapContentRequest(map_key=map_key, locale=locale))
            except grpc.aio.AioRpcError as exc:
                if exc.code() in _NOT_FOUND_CODES:
                    return None
                raise
        return {
            "entity_id": reply.entity_id,
            "locale": reply.locale,
            "name": reply.name,
            "summary": reply.summary,
            "source_url": reply.source_url,
            "image_url": reply.image_url,
        }
