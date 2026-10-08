"""Kingdoms mod seed CLI — season data load, by visible ids.

Usage::

    python -m kingdoms.mods.kingdoms.kingdoms_seed_cli <seed_yaml> <guild_id>

The seed file is a designer-authored YAML document keyed by the
mod's visible ids (the ones the panels' footers show):

.. code-block:: yaml

    season_id: kingdoms-aoe2-<guild id>-<index>   # the footer's id
    map_pool_id: map_pool:aoe2:<pool name>        # optional, the core pool
    kingdoms:                                     # optional, imposed names
      - Aquitaine
      - Wessex
    territories:                                  # optional initial draw
      - map_key: arabia
        owner: gaia
      - map_key: kawasan
        owner: Aquitaine

The command is idempotent: re-running over the same file is a no-op
(same territory ids ``territory:<season id>:<map key>`` skip). It
never creates core catalog entries — the maps and pools come from the
core AoE2 seed (``seed_aoe2``); this CLI only writes the mod's own
season data. Reads ``MONGO_URI``/``MONGO_DB`` from the environment.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    """Seed the kingdoms mod's season data; exit 0 on success."""
    if len(sys.argv) != 3:
        print("usage: kingdoms_seed_cli <seed_yaml> <guild_id>", file=sys.stderr)
        return 2
    seed_yaml, guild_id = Path(sys.argv[1]), sys.argv[2]
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
    import yaml

    from kingdoms.core.models.db import close_async_client, get_async_database
    from kingdoms.mods.kingdoms.kingdoms_seed import seed_kingdoms_mod

    data: Any = yaml.safe_load(seed_yaml.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        print("kingdoms seed: the seed file must be a YAML mapping", file=sys.stderr)
        return 2

    async def run() -> None:
        """Run the mod seed and close the database connection."""
        database = get_async_database()
        try:
            report = await seed_kingdoms_mod(database, data, guild_id)
        finally:
            await close_async_client()
        print(
            f"kingdoms seed: {report.kingdoms} kingdoms, "
            f"{report.territories} territories, {report.lords} lords (season {report.season_id})"
        )

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
