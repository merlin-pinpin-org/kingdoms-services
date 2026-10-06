"""Identity export CLI: one-shot association dump (kingdoms-services#138).

Usage: ``python -m kingdoms.core.services.identity_export_cli <users_csv>``

Reads ``MONGO_URI``/``MONGO_DB`` from the environment and prints the
export counts.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    """Export the association CSV from the core bindings; exit 0 on success."""
    if len(sys.argv) != 2:
        print("usage: identity_export_cli <users_csv>", file=sys.stderr)
        return 2
    users_csv = Path(sys.argv[1])
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.core.services.identity_export import export_identity_links

    async def run() -> None:
        """Run the export and close the database connection."""
        database = get_async_database()
        try:
            users_csv.parent.mkdir(parents=True, exist_ok=True)
            report = await export_identity_links(database, users_csv)
        finally:
            await close_async_client()
        print(f"identity export: {report.users} users, {report.rows} rows -> {users_csv}")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
