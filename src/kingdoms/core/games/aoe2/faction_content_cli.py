"""Faction content seed CLI: aoe2techtree dataset → Mongo (per locale).

Usage::

    python -m kingdoms.core.games.aoe2.faction_content_cli [dataset_dir]

Loads the vendored aoe2techtree dataset (``data/core/aoe2techtree`` —
MIT licence, extracted game data under Microsoft's Game Content Usage
Rules) and upserts one ``faction_content`` document per civ per
platform locale (``en``, ``fr``), addressed by the catalog's stable ids
(``faction:aoe2:Franks``). Idempotent: re-running overwrites the same
documents. Reads ``MONGO_URI``/``MONGO_DB`` from the environment.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Seed the per-locale faction content into Mongo; exit 0 on success."""
    if len(sys.argv) > 2:
        print("usage: faction_content_cli [dataset_dir]", file=sys.stderr)
        return 2
    default_dir = Path(__file__).resolve().parents[4] / "data" / "core" / "aoe2techtree"
    dataset_dir = Path(sys.argv[1]) if len(sys.argv) == 2 else default_dir
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

    from kingdoms.core.games.aoe2.faction_content import SUPPORTED_LOCALES
    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.core.services.faction_content import FactionContentService

    async def run() -> int:
        """Run the content seed and close the database connection."""
        from kingdoms.core.games.aoe2.faction_content import TechtreeContentProvider

        provider = TechtreeContentProvider.from_files(
            (dataset_dir / "data.json").read_text(encoding="utf-8"),
            {lng: (dataset_dir / f"strings-{lng}.json").read_text(encoding="utf-8") for lng in SUPPORTED_LOCALES},
        )
        database = get_async_database()
        service = FactionContentService(database)
        stored = 0
        try:
            for civ_name in provider.faction_names():
                for locale in SUPPORTED_LOCALES:
                    content = provider.faction_content(civ_name, locale)
                    if content is None:
                        continue
                    await service.store(
                        {
                            "entity_id": content.entity_id,
                            "locale": content.locale,
                            "name": content.name,
                            "summary": content.summary,
                            "source_url": content.source_url,
                            "provider": "aoe2techtree",
                        }
                    )
                    stored += 1
        finally:
            await close_async_client()
        print(f"faction content seed: {stored} documents ({', '.join(SUPPORTED_LOCALES)})")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
