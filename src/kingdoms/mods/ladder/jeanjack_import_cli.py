"""JeanJack import CLI: one-shot legacy ladder load (kingdoms-services#138).

Usage: ``python -m kingdoms.mods.ladder.jeanjack_import_cli <ladder_id> <users_csv> <matches_csv>``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment and prints the
import counts. Idempotent: re-running over the same CSVs is a no-op.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Import the JeanJack CSVs into the given ladder; exit 0 on success."""
    if len(sys.argv) != 4:
        print("usage: jeanjack_import_cli <ladder_id> <users_csv> <matches_csv>", file=sys.stderr)
        return 2
    ladder_id, users_csv, matches_csv = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.mods.ladder.jeanjack_import import import_jeanjack

    async def run() -> None:
        database = get_async_database()
        try:
            report = await import_jeanjack(database, ladder_id, users_csv, matches_csv)
        finally:
            await close_async_client()
        print(
            f"jeanjack import: {report.players} players, "
            f"{report.matches} matches, {report.rating_history_entries} rating_history entries"
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
