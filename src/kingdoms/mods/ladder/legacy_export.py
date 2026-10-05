"""Legacy match-history export: CSV dump symmetric to the import (#138).

Counterpart of ``legacy_import.py``: reads the ladder's legacy matches
(``legacy:`` ids), their rating-history entries and the players' profile
rosters, and writes the matches CSV in the legacy dump format (one row
per side-profile combination, like the original cartesian-product
export), so the file can be re-imported as-is.

Detached (ghost) profiles are exported too, matching the original dump
where dissociated accounts still appeared.
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
    "game_map_name",
    "game_started_at",
    "game_completed_at",
    "game_duration",
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
    "host_faction_key",
    "guest_faction_key",
    "winner_name",
    "winner_discord_id",
    "loser_name",
    "loser_discord_id",
)


@dataclass(frozen=True, slots=True)
class LegacyExportReport:
    """Counts of the export, printed by the CLI."""

    matches: int
    rows: int


async def export_legacy(
    database: Any,
    ladder_id: str,
    matches_path: Path,
) -> LegacyExportReport:
    """Export the ladder's legacy matches to the legacy dump CSV format."""
    players: dict[str, dict[str, Any]] = {}
    cursor = database[PLAYERS_COLLECTION].find({"ladder_id": ladder_id})
    async for doc in cursor:
        players[doc.get("user_id", "")] = doc

    def name_of(user_id: str) -> str:
        return str(players.get(user_id, {}).get("display_name", user_id))

    def profiles_of(user_id: str) -> list[str]:
        doc = players.get(user_id, {})
        linked = list(doc.get("legacy_linked_profiles") or [])
        detached = doc.get("legacy_detached_profiles") or []
        return sorted(set(linked) | set(detached))

    rows: list[dict[str, str]] = []
    matches = 0
    cursor = database[MATCHES_COLLECTION].find({"ladder_id": ladder_id})
    async for doc in cursor:
        match_id = doc.get("_id", "")
        if not match_id.startswith("legacy:"):
            continue
        matches += 1
        host_id = doc.get("host", {}).get("user_id", "")
        guest_id = doc.get("guest", {}).get("user_id", "")
        winner_id = doc.get("winner_user_id", "") or ""
        loser_id = doc.get("loser_user_id", "") or ""
        host_elo = await _elo(database, match_id, host_id)
        guest_elo = await _elo(database, match_id, guest_id)
        host_profiles = profiles_of(host_id) or [""]
        guest_profiles = profiles_of(guest_id) or [""]
        for host_profile in host_profiles:
            for guest_profile in guest_profiles:
                rows.append(
                    {
                        "ladder_match_id": match_id.removeprefix("legacy:"),
                        "status": doc.get("status", ""),
                        "match_id": "",
                        "map_name": doc.get("map_snapshot", {}).get("name", ""),
                        "game_map_name": "",
                        "game_started_at": "",
                        "game_completed_at": str(doc.get("completed_at") or ""),
                        "game_duration": "",
                        "host_name": name_of(host_id),
                        "host_discord_id": host_id,
                        "host_profile_id": host_profile,
                        "host_elo_before": host_elo[0],
                        "host_elo_diff": host_elo[1],
                        "guest_name": name_of(guest_id),
                        "guest_discord_id": guest_id,
                        "guest_profile_id": guest_profile,
                        "guest_elo_before": guest_elo[0],
                        "guest_elo_diff": guest_elo[1],
                        "host_faction_key": "",
                        "guest_faction_key": "",
                        "winner_name": name_of(winner_id) if winner_id else "",
                        "winner_discord_id": winner_id,
                        "loser_name": name_of(loser_id) if loser_id else "",
                        "loser_discord_id": loser_id,
                    }
                )
    rows.sort(key=lambda r: int(r["game_completed_at"] or 0), reverse=True)
    with matches_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return LegacyExportReport(matches=matches, rows=len(rows))


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
