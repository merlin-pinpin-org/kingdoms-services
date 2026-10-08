"""Season import CLI: one-shot full season load (kingdoms-services#205).

Usage: ``python -m kingdoms.mods.ladder.season_import_cli <season_yaml> <users_csv> <matches_csv> <guild_id>``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment and prints the
import counts. Idempotent: re-running over the same inputs is a no-op.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Import a full season; exit 0 on success."""
    if len(sys.argv) != 5:
        print(
            "usage: season_import_cli <season_yaml> <users_csv> <matches_csv> <guild_id>",
            file=sys.stderr,
        )
        return 2
    season_yaml = Path(sys.argv[1])
    users_csv = Path(sys.argv[2])
    matches_csv = Path(sys.argv[3])
    guild_id = sys.argv[4]
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.mods.ladder.season_import import import_season

    async def run() -> None:
        """Run the import/export and close the database connection."""
        database = get_async_database()
        try:
            report = await import_season(database, season_yaml, users_csv, matches_csv, guild_id)
        finally:
            await close_async_client()
        seed = report.seed
        print(
            f"season import: {seed.get('maps', 0)} maps, "
            f"{seed.get('map_pools', 0)} pools, {seed.get('ladders', 0)} ladders, "
            f"{seed.get('seasons', 0)} seasons | {report.players} players, "
            f"{report.linked_profiles} linked profiles, {report.matches} matches, "
            f"{report.rating_history_entries} rating_history entries, "
            f"{report.rotations} pool rotations"
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
