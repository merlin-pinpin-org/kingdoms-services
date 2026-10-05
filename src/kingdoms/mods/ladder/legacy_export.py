"""Legacy match-history export: CSV dump symmetric to the import (#138).

Counterpart of ``legacy_import.py``: reads the ladder's legacy matches
(``legacy:`` ids), their rating-history entries, and writes one CSV row
per match in the minimal dump format — exactly the columns the import
reads, so the file re-imports as-is (round-trip).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kingdoms.mods.ladder.models import (
    MATCHES_COLLECTION,
    PLAYERS_COLLECTION,
    RATING_HISTORY_COLLECTION,
)

MATCH_COLUMNS = (
    "ladder_match_id",
    "status",
    "match_id",
    "map_name",
    "game_completed_at",
    "host_name",
    "host_discord_id",
    "host_profile_id",
    "host_elo_before",
    "host_elo_diff",
    "guest_name",
    "guest_discord_id",
    "guest_profile_id",
    "guest_elo_before",
    "guest_elo_diff",
    "winner_discord_id",
)


@dataclass(frozen=True, slots=True)
class LegacyExportReport:
    """Counts of the export, printed by the CLI."""

    matches: int


async def export_legacy(
    database: Any,
    ladder_id: str,
    matches_path: Path,
) -> LegacyExportReport:
    """Export the ladder's legacy matches to the minimal dump CSV format."""
    names: dict[str, str] = {}
    cursor = database[PLAYERS_COLLECTION].find({"ladder_id": ladder_id})
    async for doc in cursor:
        names[doc.get("user_id", "")] = str(doc.get("display_name", ""))

    rows: list[dict[str, str]] = []
    cursor = database[MATCHES_COLLECTION].find({"ladder_id": ladder_id})
    async for doc in cursor:
        match_id = doc.get("_id", "")
        if not match_id.startswith("legacy:"):
            continue
        host_id = doc.get("host", {}).get("user_id", "")
        guest_id = doc.get("guest", {}).get("user_id", "")
        winner_id = doc.get("winner_user_id", "") or ""
        host_elo = await _elo(database, match_id, host_id)
        guest_elo = await _elo(database, match_id, guest_id)
        rows.append(
            {
                "ladder_match_id": match_id.removeprefix("legacy:"),
                "status": doc.get("status", ""),
                "match_id": "",
                "map_name": doc.get("map_snapshot", {}).get("name", ""),
                "game_completed_at": str(doc.get("completed_at") or ""),
                "host_name": names.get(host_id, host_id),
                "host_discord_id": host_id,
                "host_profile_id": "",
                "host_elo_before": host_elo[0],
                "host_elo_diff": host_elo[1],
                "guest_name": names.get(guest_id, guest_id),
                "guest_discord_id": guest_id,
                "guest_profile_id": "",
                "guest_elo_before": guest_elo[0],
                "guest_elo_diff": guest_elo[1],
                "winner_discord_id": winner_id,
            }
        )
    rows.sort(key=lambda r: int(r["game_completed_at"] or 0), reverse=True)
    with matches_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return LegacyExportReport(matches=len(rows))


async def _elo(database: Any, match_id: str, user_id: str) -> tuple[str, str]:
    """Fetch the rating-history entry (before, delta) for one side."""
    doc = await database[RATING_HISTORY_COLLECTION].find_one(
        {"_id": f"rh:{match_id}:{user_id}"}
    )
    if doc is None:
        return ("", "")
    return (
        str(int(doc.get("rating_before", 0))),
        str(int(doc.get("delta", 0))),
    )
