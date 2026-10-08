"""AoE2 seeding CLI: idempotent initial data load (#137).

Usage: ``python -m kingdoms.core.games.aoe2.seed_cli``
Reads ``MONGO_URI``/``MONGO_DB`` from the environment, loads
``config/games/aoe2/seed.yaml`` and prints the created counts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Seed the AoE2 game data; exit 0 on success."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
    from kingdoms.core.games.aoe2.seed import load_seed_data, seed_aoe2
    from kingdoms.core.models.db import close_async_client, get_async_database

    async def run() -> dict[str, int]:
        """Seed the AoE2 collections and return the inserted counts."""
        database = get_async_database()
        try:
            return await seed_aoe2(database, load_seed_data())
        finally:
            await close_async_client()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
