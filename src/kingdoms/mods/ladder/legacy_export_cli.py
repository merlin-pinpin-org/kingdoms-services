"""Legacy match-history export CLI: one-shot dump (kingdoms-services#138).

Usage: ``python -m kingdoms.mods.ladder.legacy_export_cli <ladder_id> <matches_csv>``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment and prints the
export counts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Export the legacy match CSV from the given ladder; exit 0 on success."""
    if len(sys.argv) != 3:
        print("usage: legacy_export_cli <ladder_id> <matches_csv>", file=sys.stderr)
        return 2
    ladder_id, matches_csv = sys.argv[1], Path(sys.argv[2])
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.mods.ladder.legacy_export import export_legacy

    async def run() -> None:
        """Run the import/export and close the database connection."""
        database = get_async_database()
        try:
            matches_csv.parent.mkdir(parents=True, exist_ok=True)
            report = await export_legacy(database, ladder_id, matches_csv)
        finally:
            await close_async_client()
        print(f"legacy export: {report.matches} matches -> {matches_csv}")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
