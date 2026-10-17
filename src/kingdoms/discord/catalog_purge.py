"""One-shot catalog purge: drop the polluted pre-convention data.

The id convention moved twice on the virgin base (game-scoped ids
``map:aoe2:public:<slug>``, then the stored-id channel resolution):
the catalog collections carry documents under both old shapes. This
boot pass drops the catalog collections **once** — guarded by a
marker document in the ``channels`` registry — so the next seed/reimport
rebuilds them under the current convention. Best-effort: a failure
logs and the boot continues (the next boot retries).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("kingdoms.catalog_purge")

PURGE_MARKER_KEY = "catalog-purge-2-game-scoped-ids"
PURGE_COLLECTIONS = (
    "maps",
    "map_pools",
    "map_packs",
    "map_pool_history",
    "factions",
    "faction_content",
)


async def purge_polluted_catalog() -> bool:
    """Drop the catalog collections once; True when the purge ran.

    The guard marker lives in the ``channels`` registry (the same
    collection the tree stores its channel ids under) — idempotent
    across reboots and redeploys.
    """
    import os

    if not os.environ.get("MONGO_URI"):
        return False  # no Mongo wired (local/test run): nothing to purge

    try:
        from kingdoms.core.models.db import get_async_database

        database = get_async_database()
        markers = database["channels"]
        marker = await markers.find_one({"_id": PURGE_MARKER_KEY})
        if marker is not None:
            return False
        dropped: list[str] = []
        for name in PURGE_COLLECTIONS:
            collection = database[name]
            result = await collection.delete_many({})
            dropped.append(f"{name}:{result.deleted_count}")
        await markers.replace_one(
            {"_id": PURGE_MARKER_KEY},
            {"_id": PURGE_MARKER_KEY, "purged": dropped},
            upsert=True,
        )
        logger.info("CATALOG PURGE done (virgin base): %s", ", ".join(dropped))
        return True
    except Exception:
        logger.warning("CATALOG PURGE failed — retried at the next boot", exc_info=True)
        return False


__all__ = ["purge_polluted_catalog"]
