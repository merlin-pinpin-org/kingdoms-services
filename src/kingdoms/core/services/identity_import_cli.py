"""Identity import CLI: one-shot Discord↔AoE2 association load (kingdoms-services#138).

Usage: ``python -m kingdoms.core.services.identity_import_cli <users_csv>``

The import writes the core ``profile_bindings`` (identities), never
ladder collections — ladder membership is the ladder mod's business.

Reads ``MONGO_URI``/``MONGO_DB`` from the environment and prints the
import counts. Idempotent: re-running over the same CSV is a no-op.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Import the association CSV into the core bindings; exit 0 on success."""
    if len(sys.argv) != 2:
        print("usage: identity_import_cli <users_csv>", file=sys.stderr)
        return 2
    users_csv = Path(sys.argv[1])
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.core.services.identity_import import import_identity_links

    async def run() -> None:
        """Run the identity import and close the database connection."""
        database = get_async_database()
        try:
            report = await import_identity_links(database, users_csv)
        finally:
            await close_async_client()
        print(
            f"identity import: {report.users} users, "
            f"{report.bindings} profile bindings, {report.conflicts} conflicts"
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
