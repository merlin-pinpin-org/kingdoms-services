"""Identity export: the Discord↔AoE2 association dump from core bindings.

Symmetric counterpart of ``identity_import.py``: reads the core
``profile_bindings`` and writes the association CSV (one row per
binding). The ladder players are never read — identities live in the
core, not in ladder collections.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

ASSOCIATION_COLUMNS = (
    "discord_id",
    "display_name",
    "profile_id",
)

DEFAULT_GAME_KEY = "aoe2"


@dataclass(frozen=True, slots=True)
class IdentityExportReport:
    """Counts of the export, printed by the CLI."""

    users: int
    rows: int


async def export_identity_links(
    database: Any,
    users_path: Path,
    game_key: str = DEFAULT_GAME_KEY,
) -> IdentityExportReport:
    """Export the game's Discord↔profile associations to CSV (one row per binding)."""
    rows: list[dict[str, str]] = []
    users: set[str] = set()
    cursor = database[PROFILE_BINDINGS_COLLECTION].find({"game_key": game_key})
    async for doc in cursor:
        user_id = doc.get("user_id", "")
        profile_id = doc.get("profile_id", "")
        if not user_id or not profile_id:
            continue
        users.add(user_id)
        display_name = (doc.get("profile") or {}).get("display_name", "")
        rows.append(
            {
                "discord_id": user_id,
                "display_name": display_name,
                "profile_id": profile_id,
            }
        )
    rows.sort(key=lambda r: (r["discord_id"], r["profile_id"]))
    with users_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSOCIATION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return IdentityExportReport(users=len(users), rows=len(rows))
