"""Profile-links export CLI: one-shot association dump (kingdoms-services#138).

Usage: ``python -m kingdoms.mods.ladder.profile_links_export_cli <ladder_id> <users_csv>``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment and prints the
export counts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Export the association CSV from the given ladder; exit 0 on success."""
    if len(sys.argv) != 3:
        print("usage: profile_links_export_cli <ladder_id> <users_csv>", file=sys.stderr)
        return 2
    ladder_id, users_csv = sys.argv[1], Path(sys.argv[2])
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.mods.ladder.profile_links_export import export_profile_links

    async def run() -> None:
        database = get_async_database()
        try:
            users_csv.parent.mkdir(parents=True, exist_ok=True)
            report = await export_profile_links(database, ladder_id, users_csv)
        finally:
            await close_async_client()
        print(
            f"profile-links export: {report.players} players, "
            f"{report.rows} rows -> {users_csv}"
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
