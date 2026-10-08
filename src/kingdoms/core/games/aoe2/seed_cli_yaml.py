"""Season catalog CLI: seed a season YAML into a database (#205).

Usage: ``python -m kingdoms.core.games.aoe2.seed_cli_yaml <season_yaml> [owner_ref]``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment. Unlike
``seed_cli`` (which loads the config embedded in the image), this takes
an explicit YAML path — the data-transfer workflow's ``season``
perimeter: import the real season catalog (maps, pools, ladder,
season) from a dump in the sources. Idempotent: same ids skip.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml


def main() -> int:
    """Seed the season YAML; exit 0 on success."""
    if len(sys.argv) not in (2, 3):
        print("usage: seed_cli_yaml <season_yaml> [owner_ref]", file=sys.stderr)
        return 2
    season_yaml = Path(sys.argv[1])
    owner_ref = sys.argv[2] if len(sys.argv) == 3 else None
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
    from kingdoms.core.games.aoe2.seed import seed_aoe2
    from kingdoms.core.models.db import close_async_client, get_async_database

    data = yaml.safe_load(season_yaml.read_text(encoding="utf-8"))
    if owner_ref:
        data["ladders"] = [{**spec, "owner_ref": owner_ref} for spec in data.get("ladders", [])]

    async def run() -> dict[str, int]:
        """Seed the parsed catalog and return the counts."""
        database = get_async_database()
        try:
            try:
                from kingdoms.mods.ladder.seeder import seed_ladders
            except ImportError:
                seed_ladders = None
            return await seed_aoe2(database, data, ladder_seeder=seed_ladders)
        finally:
            await close_async_client()

    counts = asyncio.run(run())
    print(
        f"season catalog: {counts.get('maps', 0)} maps, {counts.get('map_pools', 0)} pools, "
        f"{counts.get('ladders', 0)} ladders, {counts.get('seasons', 0)} seasons"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
