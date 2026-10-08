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

logger = logging.getLogger("kingdoms.core.content_refresh")

DATASET_DIR = Path(__file__).resolve().parents[4] / "data" / "core" / "aoe2techtree"


async def refresh_aoe2_content(dataset_dir: Path = DATASET_DIR) -> dict[str, int]:
    """Reconcile the catalog and per-locale content with the dataset.

    Returns the per-section counts (``factions`` created, ``content_docs``
    upserted). Raises when the dataset or Mongo is unreachable — the
    caller (DM panel, CLI) reports the failure.
    """
    from kingdoms.core.games.aoe2.faction_content import SUPPORTED_LOCALES, TechtreeContentProvider
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.faction_content import FactionContentService
    from kingdoms.core.services.game_data import GameDataService

    from .seed import MongoAoE2Database

    provider = TechtreeContentProvider.from_files(
        (dataset_dir / "data.json").read_text(encoding="utf-8"),
        {lng: (dataset_dir / f"strings-{lng}.json").read_text(encoding="utf-8") for lng in SUPPORTED_LOCALES},
    )
    database = get_async_database()
    game_data = GameDataService(MongoAoE2Database(database))
    content = FactionContentService(database)
    factions = 0
    content_docs = 0
    for name in provider.faction_names():
        existing = await game_data.get_faction(f"faction:aoe2:{name}")
        if existing is None:
            await game_data.create_faction("aoe2", name, faction_key=name.lower())
            factions += 1
        for locale in SUPPORTED_LOCALES:
            descriptor = provider.faction_content(name, locale)
            if descriptor is None:
                continue
            await content.store(
                {
                    "entity_id": descriptor.entity_id,
                    "locale": descriptor.locale,
                    "name": descriptor.name,
                    "summary": descriptor.summary,
                    "source_url": descriptor.source_url,
                    "provider": "aoe2techtree",
                }
            )
            content_docs += 1
    logger.info("CONTENT REFRESH: %d factions created, %d content docs upserted", factions, content_docs)
    return {"factions": factions, "content_docs": content_docs}
