"""Profile-links export: the Discord↔AoE2 association dump (kingdoms-services#138).

Symmetric counterpart of ``profile_links_import.py``: reads the ladder
players and writes the association CSV (one row per linked profile).

"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kingdoms.mods.ladder.models import PLAYERS_COLLECTION

ASSOCIATION_COLUMNS = (
    "discord_id",
    "ladder_name",
    "profile_id",
)


@dataclass(frozen=True, slots=True)
class ProfileLinksExportReport:
    """Counts of the export, printed by the CLI."""

    players: int
    rows: int


async def export_profile_links(
    database: Any,
    ladder_id: str,
    users_path: Path,
) -> ProfileLinksExportReport:
    """Export the ladder's Discord↔AoE2 associations to CSV.

    One row per (player, linked profile), like the legacy dump: players
    without linked profiles are skipped. Detached (ghost) profiles are
    exported with an empty ``ladder_name`` so a re-import keeps them
    detached — they never become active links.
    """
    rows: list[dict[str, str]] = []
    players = 0
    cursor = database[PLAYERS_COLLECTION].find({"ladder_id": ladder_id})
    async for doc in cursor:
        linked = sorted(doc.get("legacy_linked_profiles") or [])
        if not linked:
            continue
        players += 1
        for profile_id in linked:
            rows.append(
                {
                    "discord_id": doc.get("user_id", ""),
                    "ladder_name": doc.get("display_name", ""),
                    "profile_id": profile_id,
                }
            )
    rows.sort(key=lambda r: (r["discord_id"], r["profile_id"]))
    with users_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSOCIATION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return ProfileLinksExportReport(players=players, rows=len(rows))
