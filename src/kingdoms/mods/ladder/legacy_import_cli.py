"""Legacy match-history import CLI: one-shot load (kingdoms-services#138).

Usage: ``python -m kingdoms.mods.ladder.legacy_import_cli <ladder_id> <matches_csv>``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment and prints the
import counts. Idempotent: re-running over the same CSV is a no-op.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Import the legacy match CSV into the given ladder; exit 0 on success."""
    if len(sys.argv) != 3:
        print("usage: legacy_import_cli <ladder_id> <matches_csv>", file=sys.stderr)
        return 2
    ladder_id, matches_csv = sys.argv[1], Path(sys.argv[2])
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.mods.ladder.legacy_import import import_legacy

    async def run() -> None:
        """Run the import/export and close the database connection."""
        database = get_async_database()
        try:
            report = await import_legacy(database, ladder_id, matches_csv)
        finally:
            await close_async_client()
        print(
            f"legacy import: {report.players} players, "
            f"{report.matches} matches, {report.rating_history_entries} rating_history entries, "
            f"{report.detached_profiles} detached profiles"
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
