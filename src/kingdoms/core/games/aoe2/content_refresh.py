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

import logging
from pathlib import Path
from typing import Any

from kingdoms.core.ids import slug_id

logger = logging.getLogger("kingdoms.core.content_refresh")

DATASET_DIR = Path(__file__).resolve().parents[5] / "data" / "core" / "aoe2techtree"


async def refresh_aoe2_content(dataset_dir: Path = DATASET_DIR) -> dict[str, int | list[str]]:
    """Reconcile the catalog and per-locale content with the dataset.

    Returns the per-section counts (``factions`` created, ``content_docs``
    upserted). Raises when the dataset or Mongo is unreachable — the
    caller (DM panel, CLI) reports the failure.
    """
    from kingdoms.core.games.aoe2.content_source import resolve_aoe2_content_source
    from kingdoms.core.games.aoe2.faction_content import (
        SUPPORTED_LOCALES,
        TechtreeContentProvider,
    )
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.faction_content import FactionContentService
    from kingdoms.core.services.game_data import GameDataService

    from .seed import MongoAoE2Database

    provider = TechtreeContentProvider.from_files(
        (dataset_dir / "data.json").read_text(encoding="utf-8"),
        {lng: (dataset_dir / f"strings-{lng}.json").read_text(encoding="utf-8") for lng in SUPPORTED_LOCALES},
    )
    source = resolve_aoe2_content_source(provider)
    database = get_async_database()
    game_data = GameDataService(MongoAoE2Database(database))
    content = FactionContentService(database)
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
    maps_enriched = 0
    try:
        from kingdoms.mapsdata.seed import fetch_map_seed

        for entry in await game_data.list_maps("aoe2"):
            already = await content.get(entry.id, "en")
            if already is not None and already.get("image_url"):
                continue
            seed = await fetch_map_seed(entry.name)
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
                    "provider": "liquipedia",
                }
            )
            maps_enriched += 1
    except Exception:
        logger.warning("CONTENT REFRESH: map image enrichment failed — best-effort", exc_info=True)
    logger.info(
        "CONTENT REFRESH: %d factions created, %d content docs upserted, %d maps enriched",
        factions,
        content_docs,
        maps_enriched,
    )
    return {
        "factions": factions,
        "new_factions": new_factions,
        "total_factions": len(known),
        "content_docs": content_docs,
        "maps_enriched": maps_enriched,
    }


async def _legacy_renames(database: Any, collections: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Scan the collections for legacy (non-slug) ids worth renaming."""
    renames: dict[str, str] = {}
    for collection, prefix in collections:
        try:
            cursor = database[collection].find({})
            async for doc in cursor:
                entry_id = str(doc.get("_id", ""))
                parts = entry_id.split(":")
                if len(parts) != 3 or parts[0] != prefix:
                    continue
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

    renames = await _legacy_renames(database, ((MAPS_COLLECTION, "map"), (FACTIONS_COLLECTION, "faction")))
    if not renames:
        return
    await _rename_documents(database, renames, {"map": MAPS_COLLECTION, "faction": FACTIONS_COLLECTION})
    await _rewrite_id_references(
        database, renames, ((MAP_POOLS_COLLECTION, "map_ids"), (MAP_PACKS_COLLECTION, "map_ids"))
    )
    await _rewrite_content_ids(database, renames, CONTENT_COLLECTION)
    logger.info("ID MIGRATION: %d entity ids renamed to their slug form", len(renames))
