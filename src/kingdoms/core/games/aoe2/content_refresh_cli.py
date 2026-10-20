"""Content refresh CLI: reconcile the catalog with the vendored dataset.

Usage: ``python -m kingdoms.core.games.aoe2.content_refresh_cli``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment, reconciles the
factions catalog with the dataset (creates missing civs), and upserts
the per-locale content documents. Idempotent.
"""

from __future__ import annotations

import asyncio
import sys


def main() -> int:
    """Run the refresh; exit 0 on success."""
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
    from kingdoms.core.games.aoe2.content_refresh import refresh_aoe2_content
    from kingdoms.core.models.db import close_async_client

    async def run() -> dict[str, int | list[str]]:
        """Run the refresh and close the database connection."""
        try:
            return await refresh_aoe2_content()
        finally:
            await close_async_client()

    counts = asyncio.run(run())
    print(f"refresh: {counts['factions']} factions created, {counts['content_docs']} content docs upserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
