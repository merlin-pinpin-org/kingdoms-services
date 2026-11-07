"""The remote catalog service: the game service's catalog, proxied.

Rule 5 (ADR-0021): the game's collections (maps, pools, packs,
factions, content) answer from the OWNER's process — the game service.
This proxy exposes the GameDataService's method surface over the
GameData.Call gateway, rebuilding the same pydantic models, so the
bot's views keep their objects and their logic untouched.

A functional error crosses the wire with its kind (NameTakenError,
ValueError...) and re-raises as the SAME exception type — the callers'
except-clauses keep working.
"""

from __future__ import annotations

import json
from typing import Any

from kingdoms.core.models.game_data import MapModel, MapPackModel, MapPoolModel
from kingdoms.core.services.game_data import NameTakenError


def _model(doc: Any) -> MapModel:
    """Rebuild a MapModel from the JSON wire (tuples coerce)."""
    return MapModel.model_validate(doc, strict=False)


def _model_pack(doc: Any) -> MapPackModel:
    """Rebuild a MapPackModel from the JSON wire (tuples coerce)."""
    return MapPackModel.model_validate(doc, strict=False)


def _model_pool(doc: Any) -> MapPoolModel:
    """Rebuild a MapPoolModel from the JSON wire (tuples coerce)."""
    return MapPoolModel.model_validate(doc, strict=False)


class RemoteGameDataService:
    """The GameDataService surface, served by the game service."""

    def __init__(self, uri: str) -> None:
        self._uri = uri

    async def _call(self, op: str, **kwargs: Any) -> Any:
        """Run one catalog op; rebuild the typed result."""
        from kingdoms.core.rpc.client import build_channel
        from kingdoms.rpc_generated.kingdoms.v1 import gamedata_pb2, gamedata_pb2_grpc

        async with build_channel(self._uri) as channel:
            stub = gamedata_pb2_grpc.GameDataStub(channel)
            reply = await stub.Call(
                gamedata_pb2.GameDataCallRequest(
                    op=op,
                    payload_json=json.dumps(
                        {k: list(v) if isinstance(v, tuple) else v for k, v in kwargs.items()}
                    ),
                ),
                timeout=30.0,
            )
        if not reply.ok:
            raise _error_of(reply.error_kind, reply.error)
        return json.loads(reply.result_json) if reply.result_json else None

    # ── maps ────────────────────────────────────────────────────────────

    async def list_maps(self, game_key: str, guild_id: str | None = None) -> list[MapModel]:
        """List the game's maps (guild-scoped when a guild is given)."""
        result = await self._call("list_maps", game_key=game_key, guild_id=guild_id)
        return [_model(doc) for doc in result or []]

    async def get_map(self, entry_id: str) -> MapModel | None:
        """Fetch one map; None when it does not exist."""
        result = await self._call("get_map", entry_id=entry_id)
        return _model(result) if result else None

    async def create_map(
        self,
        game_key: str,
        name: str,
        filename: str,
        description: str = "",
        resource_url: str = "",
        owner_guild_id: str | None = None,
        map_type: str = "",
        filenames: tuple[str, ...] = (),
        is_public: bool = True,
    ) -> MapModel:
        """Create a map; a taken name raises NameTakenError."""
        result = await self._call(
            "create_map",
            game_key=game_key,
            name=name,
            filename=filename,
            description=description,
            resource_url=resource_url,
            owner_guild_id=owner_guild_id,
            map_type=map_type,
            filenames=filenames,
            is_public=is_public,
        )
        return _model(result)

    async def update_map(
        self,
        entry_id: str,
        name: str | None = None,
        filename: str | None = None,
        description: str | None = None,
        resource_url: str | None = None,
        map_type: str | None = None,
        filenames: tuple[str, ...] | None = None,
    ) -> MapModel:
        """Update a map's mutable fields (positional shape, like the real service)."""
        result = await self._call(
            "update_map",
            entry_id=entry_id,
            name=name,
            filename=filename,
            description=description,
            resource_url=resource_url,
            map_type=map_type,
            filenames=filenames,
        )
        return _model(result)

    async def delete_map(self, entry_id: str) -> bool:
        """Hard-delete a map (the import rollback's path)."""
        return bool(await self._call("delete_map", entry_id=entry_id))

    async def archive_map(self, entry_id: str) -> MapModel:
        """Archive a map (the catalog's soft delete)."""
        return _model(await self._call("archive_map", entry_id=entry_id))

    async def set_map_forum_message(self, entry_id: str, forum_message_id: str) -> MapModel:
        """Record the map's forum post id (the provisioning's bookkeeping)."""
        result = await self._call(
            "set_map_forum_message", entry_id=entry_id, forum_message_id=forum_message_id
        )
        return _model(result)

    # ── map packs ───────────────────────────────────────────────────────

    async def create_map_pack(
        self,
        game_key: str,
        name: str,
        map_ids: tuple[str, ...] = (),
        description: str = "",
    ) -> MapPackModel:
        """Create a map pack (positional shape, like the real service)."""
        return _model_pack(
            await self._call(
                "create_map_pack",
                game_key=game_key,
                name=name,
                map_ids=map_ids,
                description=description,
            )
        )

    async def get_map_pack(self, entry_id: str) -> MapPackModel | None:
        """Fetch one map pack; None when it does not exist."""
        result = await self._call("get_map_pack", entry_id=entry_id)
        return _model_pack(result) if result else None

    async def archive_map_pack(self, entry_id: str) -> MapPackModel:
        """Archive a map pack."""
        return _model_pack(await self._call("archive_map_pack", entry_id=entry_id))

    # ── map pools ───────────────────────────────────────────────────────

    async def list_map_pools(
        self, game_key: str, guild_id: str | None = None
    ) -> list[MapPoolModel]:
        """List the active pools (guild-scoped when a guild is given)."""
        result = await self._call("list_map_pools", game_key=game_key, guild_id=guild_id)
        return [_model_pool(doc) for doc in result or []]

    async def list_all_map_pools(
        self, game_key: str, guild_id: str | None = None
    ) -> list[MapPoolModel]:
        """List every pool, archived included (the admin's view)."""
        result = await self._call("list_all_map_pools", game_key=game_key, guild_id=guild_id)
        return [_model_pool(doc) for doc in result or []]

    async def get_map_pool(self, entry_id: str) -> MapPoolModel | None:
        """Fetch one pool; None when it does not exist."""
        result = await self._call("get_map_pool", entry_id=entry_id)
        return _model_pool(result) if result else None

    async def create_map_pool(
        self,
        game_key: str,
        name: str,
        map_ids: tuple[str, ...] = (),
        map_pack_ids: tuple[str, ...] = (),
        description: str = "",
        owner_guild_id: str | None = None,
        is_public: bool = False,
    ) -> MapPoolModel:
        """Create a pool; a taken name raises NameTakenError.

        The signature mirrors the REAL service's positional shape —
        the callers pass positionals (the 2026-11-06 crash: a
        **kwargs-only remote raised 'takes 1 positional argument').
        """
        return _model_pool(
            await self._call(
                "create_map_pool",
                game_key=game_key,
                name=name,
                map_ids=map_ids,
                map_pack_ids=map_pack_ids,
                description=description,
                owner_guild_id=owner_guild_id,
                is_public=is_public,
            )
        )

    async def update_map_pool(
        self,
        entry_id: str,
        name: str | None = None,
        map_ids: tuple[str, ...] | None = None,
        description: str | None = None,
        fav_quota: int | None = None,
        ban_quota: int | None = None,
    ) -> MapPoolModel:
        """Update a pool's mutable fields (positional shape, like the real service)."""
        return _model_pool(
            await self._call(
                "update_map_pool",
                entry_id=entry_id,
                name=name,
                map_ids=map_ids,
                description=description,
                fav_quota=fav_quota,
                ban_quota=ban_quota,
            )
        )

    async def transition_map_pool(self, entry_id: str, new_state: str) -> MapPoolModel:
        """Move a pool along its lifecycle (draft->published->used->closed)."""
        return _model_pool(
            await self._call("transition_map_pool", entry_id=entry_id, new_state=new_state)
        )

    async def archive_map_pool(self, entry_id: str) -> MapPoolModel:
        """Archive a pool."""
        return _model_pool(await self._call("archive_map_pool", entry_id=entry_id))

    async def hard_delete_map_pool(self, entry_id: str) -> bool:
        """Hard-delete a pool (the admin's destructive path)."""
        return bool(await self._call("hard_delete_map_pool", entry_id=entry_id))

    async def duplicate_map_pool(
        self, entry_id: str, new_name: str, owner_guild_id: str | None = None
    ) -> MapPoolModel:
        """Duplicate a pool under a new name."""
        return _model_pool(
            await self._call(
                "duplicate_map_pool",
                entry_id=entry_id,
                new_name=new_name,
                owner_guild_id=owner_guild_id,
            )
        )

    async def send_map_pool_to_guild(
        self, entry_id: str, target_guild_id: str, new_name: str
    ) -> MapPoolModel:
        """Send a pool to another guild (the sharing flow)."""
        return _model_pool(
            await self._call(
                "send_map_pool_to_guild",
                entry_id=entry_id,
                target_guild_id=target_guild_id,
                new_name=new_name,
            )
        )

    async def set_pool_forum_thread(self, entry_id: str, forum_thread_id: str) -> MapPoolModel:
        """Record the pool's forum thread id (the provisioning's bookkeeping)."""
        result = await self._call(
            "set_pool_forum_thread", entry_id=entry_id, forum_thread_id=forum_thread_id
        )
        return _model_pool(result)

    async def resolve_pool_map_ids(self, pool: MapPoolModel) -> tuple[str, ...]:
        """Resolve a pool's composition into its map ids (packs expanded)."""
        result = await self._call("resolve_pool_map_ids", pool=pool.model_dump(by_alias=True))
        return tuple(str(item) for item in result or [])

    # ── factions ────────────────────────────────────────────────────────

    async def list_factions(self, game_key: str, guild_id: str | None = None) -> list[Any]:
        """List the game's factions (the civs' catalog)."""
        result = await self._call("list_factions", game_key=game_key, guild_id=guild_id)
        return list(result or [])

    async def list_game_keys(self) -> list[str]:
        """List the implemented games."""
        return [str(key) for key in await self._call("list_game_keys") or []]

    # ── the localized content docs ─────────────────────────────────────

    async def content_store(self, content: dict[str, Any]) -> None:
        """Store one localized content doc (the enrichment's write)."""
        await self._call("content_store", content=content)

    async def content_get(self, entity_id: str, locale: str) -> dict[str, Any] | None:
        """Read one localized content doc (the enrichment's read)."""
        result: dict[str, Any] | None = await self._call(
            "content_get", entity_id=entity_id, locale=locale
        )
        return result


def _error_of(kind: str, message: str) -> Exception:
    """Re-raise the wire's functional error as its typed exception."""
    if kind == "NameTakenError":
        return NameTakenError(message)
    if kind == "ValueError":
        return ValueError(message)
    if kind == "RuntimeError":
        return RuntimeError(message)
    return RuntimeError(f"{kind}: {message}")
