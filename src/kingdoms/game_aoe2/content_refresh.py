"""Dataset refresh: keep the AoE2 catalog and content in sync with updates.

Game updates evolve the data: new civs (The Viking Sagas), changed
unique units, tech trees. The vendored aoe2techtree dataset is the
source of truth; this module reconciles the platform with it:

1. **Catalog** — every civ in the dataset that the ``factions``
   collection does not know yet is created (global, admin-owned scope).
2. **Content** — every per-locale content document is upserted
   (localized name + the game's own civ help text), so changed help
   texts propagate.

Both steps are idempotent; the bot-admin DM panel's refresh button and
a CLI both land here. Only dataset-known civs are touched: guild-local
or manual catalog additions are never archived by a refresh.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kingdoms.core.ids import slug_id

logger = logging.getLogger("kingdoms.core.content_refresh")



async def refresh_aoe2_content() -> dict[str, int | list[str]]:
    """Reconcile the catalog and per-locale content with the dataset.

    Returns the per-section counts (``factions`` created, ``content_docs``
    upserted). Raises when the dataset or Mongo is unreachable — the
    caller (DM panel, CLI) reports the failure. Maps are NOT touched
    here: their Liquipedia enrichment is on demand only
    (:func:`refresh_aoe2_maps`).
    """
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.faction_content import FactionContentService
    from kingdoms.core.services.game_data import GameDataService
    from kingdoms.game_aoe2.content_source import resolve_aoe2_content_source
    from kingdoms.game_aoe2.faction_content import SUPPORTED_LOCALES

    from .seed import MongoAoE2Database

    # NO vendored data (owner direction 2026-11-05): the civs come from
    # the aoe2techtree provider process, over the Content contract. The
    # dataset files are gone; a missing provider is a FUNCTIONAL error.
    source = resolve_aoe2_content_source(None)
    database = get_async_database()
    game_data = GameDataService(MongoAoE2Database(database))
    content = FactionContentService(database)
    await content.ensure_indexes()
    from kingdoms.core.services.provider_mapping import ProviderMappingService
    from kingdoms.core.services.provider_mapping_mongo import MongoProviderMappingDatabase

    mapping_service = ProviderMappingService(MongoProviderMappingDatabase(database))
    mapping_doc = await mapping_service.get("aoe2techtree")
    mapping = mapping_doc.get("factions") or {}
    await _migrate_entity_ids(database, game_data)

    factions = 0
    content_docs = 0
    new_factions: list[str] = []
    known = await source.list_factions()
    for name in known:
        existing = await game_data.get_faction(f"faction:aoe2:{slug_id(name)}")
        if existing is None:
            await game_data.create_faction("aoe2", name, faction_key=name.lower())
            factions += 1
            new_factions.append(name)
        for locale in SUPPORTED_LOCALES:
            # The provider mapping (catalog name -> provider id) survives the
            # seam: refresh resolves it first, then asks the source (dataset
            # or ext process) for the provider-side key.
            descriptor = await source.faction_content(mapping.get(name, name), locale)
            if descriptor is None:
                continue
            await content.store(
                {
                    # The catalog's stable id always wins over the provider's.
                    "entity_id": f"faction:aoe2:{slug_id(name)}",
                    "locale": descriptor.locale,
                    "name": descriptor.name,
                    "summary": descriptor.summary,
                    "source_url": descriptor.source_url,
                    "image_url": descriptor.image_url,
                    "provider": "aoe2techtree",
                }
            )
            content_docs += 1
    logger.info(
        "CONTENT REFRESH: %d factions created, %d content docs upserted (maps enrichment is on demand)",
        factions,
        content_docs,
    )
    return {
        "factions": factions,
        "new_factions": new_factions,
        "total_factions": len(known),
        "content_docs": content_docs,
    }


async def refresh_aoe2_maps() -> dict[str, int]:
    """Enrich the maps' content docs from Liquipedia — on demand only.

    Never runs at boot: the Liquipedia seeding is slow by design (the
    API terms pace one parse every 60 s, and a cold catalog fires one
    request per map — boot-time runs hammered the API into 429s).
    The DM panel's maps sync button calls this; the boot refresh and
    the civs refresh stay Liquipedia-free. Returns the enrichment
    counts (``maps_enriched`` upserted docs, ``maps_checked`` entries
    that qualified).
    """
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.faction_content import FactionContentService
    from kingdoms.core.services.game_data import GameDataService

    from .seed import MongoAoE2Database

    database = get_async_database()
    game_data = GameDataService(MongoAoE2Database(database))
    content = FactionContentService(database)
    await content.ensure_indexes()
    maps_total = 0
    maps_complete = 0
    maps_checked = 0
    maps_enriched = 0
    try:
        entries = await game_data.list_maps("aoe2")
        pending: list[tuple[Any, str]] = []
        for entry in entries:
            maps_total += 1
            already = await content.get(entry.id, "en")
            # the skip requires the LABEL generation: a doc without
            # label_gen was stored by the bool-coercion era (walls/nomad/
            # tc as "True") — it re-enriches with the true labels
            if (
                already is not None
                and already.get("icon_url")
                and already.get("walls")
                and already.get("label_gen") == 2
            ):
                maps_complete += 1
                continue
            maps_checked += 1
            pending.append((entry, entry.name))
        seeds = await _resolve_map_seeds([n for _, n in pending])
        for entry, name in pending:
            seed = seeds.get(_title_variants_key(name))
            if seed is None:
                seed = seeds.get(name)
            if seed is None or not seed.image_url:
                continue
            await content.store(
                {
                    "entity_id": entry.id,
                    "locale": "en",
                    "name": entry.name,
                    "summary": seed.description,
                    "source_url": seed.resource_url,
                    "image_url": seed.image_url,
                    "icon_url": seed.icon_url,
                    "walls": seed.walls,
                    "nomad": seed.nomad,
                    "tc": seed.tc,
                    "player_capacity": seed.player_capacity,
                    "creator": seed.creator,
                    "label_gen": 2,
                    "provider": "liquipedia",
                    "game_versions": list(seed.game_versions),
                    "versions": [dict(v) for v in seed.versions],
                    "tags": list(seed.tags),
                }
            )
            maps_enriched += 1
    except Exception:
        logger.warning("CONTENT REFRESH: map image enrichment failed — best-effort", exc_info=True)
    logger.info(
        "CONTENT REFRESH (maps, on demand): %d/%d maps enriched, %d complete skipped, %d checked",
        maps_enriched,
        maps_total,
        maps_complete,
        maps_checked,
    )
    return {
        "maps_enriched": maps_enriched,
        "maps_checked": maps_checked,
        "maps_complete": maps_complete,
        "maps_total": maps_total,
    }


def _title_variants_key(name: str) -> str:
    """Return the canonical cache key of a map name (spaces -> underscores)."""
    return name.strip().replace(" ", "_")


async def _resolve_map_seeds(names: list[str]) -> dict[str, Any]:
    """Resolve many map seeds — through the ext-liquipedia provider.

    The provider owns the Liquipedia access (ADR-0020): ONE client per
    environment, no in-process fallback (owner direction 2026-11-05:
    no external call outside the providers). Its seed worker COALESCES
    every concurrent request — this refresh's whole batch lands in a
    few batched ``action=query`` calls at the light pacing, behind the
    circuit breaker. A missing provider is a FUNCTIONAL error.
    Returns {cache_key: seed}; a rate-limited upstream raises the
    circuit error to the caller.
    """
    import os

    from kingdoms.aoe2.map_seed import MapSeed
    from kingdoms.game_aoe2.content_source import ProviderUnavailableError

    provider_uri = os.environ.get("EXT_LIQUIPEDIA_URI", "")
    if not provider_uri:
        raise ProviderUnavailableError(
            "the liquipedia provider is not configured "
            "(EXT_LIQUIPEDIA_URI) — the maps enrichment cannot run"
        )

    from kingdoms.core.rpc.content_client import ContentProviderClient

    client: Any = ContentProviderClient(provider_uri)

    async def _one(name: str) -> tuple[str, Any] | None:
        try:
            descriptor = await client.get_map_seed("aoe2", name)
        except Exception:
            return None  # a missing page degrades to no seed, per map
        if descriptor is None:
            return None
        return (
            name,
            MapSeed(
                name=str(descriptor.get("name", name)),
                description=str(descriptor.get("description", "")),
                resource_url=str(descriptor.get("resource_url", "")),
                image_url=str(descriptor.get("image_url", "")),
                icon_url=str(descriptor.get("icon_url", "")),
                terrain=str(descriptor.get("terrain", "")),
                size=str(descriptor.get("size", "")),
                map_type=str(descriptor.get("map_type", "")),
                walls=str(descriptor.get("walls_label", "") or ""),
                nomad=str(descriptor.get("nomad_label", "") or ""),
                tc=str(descriptor.get("tc_label", "") or ""),
                player_capacity=str(descriptor.get("player_capacity", "") or ""),
                creator=str(descriptor.get("creator", "") or ""),
                public=True,
                game_versions=tuple(str(v) for v in descriptor.get("game_versions", ()) or ()),
                versions=tuple(
                    {"name": v.get("name", ""), "description": v.get("description", ""),
                     "details": list(v.get("details", ()) or ())}
                    for v in descriptor.get("versions", ()) or ()
                ),
                tags=tuple(str(t) for t in descriptor.get("tags", ()) or ()),
            ),
        )

    results = await asyncio.gather(*[_one(n) for n in names])
    out: dict[str, Any] = {}
    for result in results:
        if result is None:
            continue
        name, seed = result
        out[_title_variants_key(name)] = seed
    return out

async def _legacy_renames(database: Any, collections: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Scan the collections for legacy ids worth renaming.

    Maps carry two legacy shapes: non-slug tails and the game-key
    middle segment (``map:aoe2:arabia``). The current scheme scopes by
    ownership instead: ``map:public:<slug>`` (global) or
    ``map:<guild_id>:<slug>`` (guild-local) — the doc's
    ``owner_guild_id`` decides.
    """
    renames: dict[str, str] = {}
    for collection, prefix in collections:
        try:
            cursor = database[collection].find({})
            async for doc in cursor:
                entry_id = str(doc.get("_id", ""))
                parts = entry_id.split(":")
                if len(parts) != 3 or parts[0] != prefix:
                    continue
                if prefix == "map_pool":
                    scope = str(doc.get("owner_guild_id") or "public")
                    wanted = f"pool:{scope}:{slug_id(parts[2])}"
                elif prefix == "map":
                    scope = str(doc.get("owner_guild_id") or "public")
                    wanted = f"map:{scope}:{slug_id(parts[2])}"
                else:
                    wanted = f"{prefix}:{parts[1]}:{slug_id(parts[2])}"
                if wanted != entry_id and await database[collection].find_one({"_id": wanted}) is None:
                    renames[entry_id] = wanted
        except Exception:
            logger.warning("ID MIGRATION: %s scan failed — best-effort", collection, exc_info=True)
    return renames


async def _rename_documents(database: Any, renames: dict[str, str], collections: dict[str, str]) -> None:
    """Rename the entity documents themselves (old id -> slug id)."""
    for old_id, new_id in renames.items():
        try:
            prefix = old_id.split(":")[0]
            collection = collections.get(prefix)
            if collection is None:
                continue
            doc = await database[collection].find_one({"_id": old_id})
            if doc is None:
                continue
            doc["_id"] = new_id
            await database[collection].replace_one({"_id": new_id}, doc, upsert=True)
            await database[collection].delete_one({"_id": old_id})
        except Exception:
            logger.warning("ID MIGRATION: rename %s failed — best-effort", old_id, exc_info=True)


async def _rewrite_id_references(
    database: Any, renames: dict[str, str], ref_collections: tuple[tuple[str, str], ...]
) -> None:
    """Rewrite the embedded entity ids in referencing documents."""
    for ref_collection, field in ref_collections:
        try:
            cursor = database[ref_collection].find({})
            async for doc in cursor:
                ids = doc.get(field) or []
                if not any(i in renames for i in ids):
                    continue
                updated = [renames.get(i, i) for i in ids]
                await database[ref_collection].update_one({"_id": doc["_id"]}, {"$set": {field: updated}})
        except Exception:
            logger.warning("ID MIGRATION: %s refs failed — best-effort", ref_collection, exc_info=True)


async def _rewrite_content_ids(database: Any, renames: dict[str, str], content_collection: str) -> None:
    """Rewrite the content docs' entity ids (and their composite _id)."""
    try:
        cursor = database[content_collection].find({})
        async for doc in cursor:
            entity_id = str(doc.get("entity_id", ""))
            if entity_id not in renames:
                continue
            new_doc = dict(doc)
            new_doc["entity_id"] = renames[entity_id]
            new_doc["_id"] = f"{renames[entity_id]}:{doc.get('locale', '')}"
            await database[content_collection].replace_one({"_id": doc["_id"]}, new_doc, upsert=True)
            await database[content_collection].delete_one({"_id": doc["_id"]})
    except Exception:
        logger.warning("ID MIGRATION: content refs failed — best-effort", exc_info=True)


async def _migrate_entity_ids(database: Any, game_data: Any) -> None:
    """Rename legacy entity ids to their slug form (idempotent).

    Entries created before the slug convention carry ids like
    ``map:aoe2:Arabia``; every lookup now uses ``map:aoe2:arabia``. The
    migration renames the documents and rewrites the references that
    embed entity ids: pools' and packs' ``map_ids``, content docs'
    ``entity_id``. Best-effort: a failure logs and leaves the legacy
    ids in place (the refresh then adopts them by name).
    """
    del game_data
    from kingdoms.core.services.faction_content import CONTENT_COLLECTION
    from kingdoms.core.services.game_data import (
        FACTIONS_COLLECTION,
        MAP_PACKS_COLLECTION,
        MAP_POOLS_COLLECTION,
        MAPS_COLLECTION,
    )

    renames = await _legacy_renames(
        database,
        (
            (MAPS_COLLECTION, "map"),
            (FACTIONS_COLLECTION, "faction"),
            (MAP_POOLS_COLLECTION, "map_pool"),
        ),
    )
    if not renames:
        return
    await _rename_documents(database, renames, {"map": MAPS_COLLECTION, "faction": FACTIONS_COLLECTION})
    await _rewrite_id_references(
        database,
        renames,
        (
            (MAP_POOLS_COLLECTION, "map_ids"),
            (MAP_PACKS_COLLECTION, "map_ids"),
            ("map_pool_history", "map_pool_id"),
        ),
    )
    await _rewrite_content_ids(database, renames, CONTENT_COLLECTION)
    logger.info("ID MIGRATION: %d entity ids renamed to their slug form", len(renames))
