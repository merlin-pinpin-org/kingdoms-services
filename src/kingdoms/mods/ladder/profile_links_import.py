"""Profile-links import: the Discord↔AoE2 association dump (kingdoms-services#138).

One of the two independent legacy import processes:

- **this module** — the association dump (users.csv): each row is one
  Discord user with one linked AoE2 profile. The import writes the
  ``legacy_linked_profiles`` roster into the player documents of the
  given ladder (creating minimal players for users who never played).
  These links are the source for the /register validation seam.
- the match-history import (``legacy_import.py``) — the ladder
  perimeter: matches, elo replay, rating_history, players.

The two imports are idempotent and composable: run either, both, in
any order, re-run safely.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kingdoms.mods.ladder.models import (
    PLAYERS_COLLECTION,
    PlayerModel,
)

INITIAL_RATING = 1000


@dataclass(frozen=True, slots=True)
class AssociationUser:
    """One association row: a Discord id with one AoE2 profile link."""

    discord_id: str
    display_name: str
    profile_id: str


@dataclass(frozen=True, slots=True)
class ProfileLinksReport:
    """Counts of the idempotent links import, printed by the CLI."""

    players: int
    linked_profiles: int


def load_association_users(path: Path) -> list[AssociationUser]:
    """Load the association CSV, skipping rows without a Discord id."""
    users: list[AssociationUser] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            discord_id = (row.get("discord_id") or "").strip()
            if not discord_id:
                continue
            users.append(
                AssociationUser(
                    discord_id=discord_id,
                    display_name=(row.get("display_name") or "").strip(),
                    profile_id=(row.get("profile_id") or "").strip(),
                )
            )
    return users


async def import_profile_links(
    database: Any,
    ladder_id: str,
    users_path: Path,
) -> ProfileLinksReport:
    """Import the Discord↔AoE2 associations into the ladder players (idempotent).

    Each distinct Discord id gets (or keeps) a player document carrying
    the deduplicated ``legacy_linked_profiles`` list. Existing player
    documents (e.g. created by the match-history import, with ratings
    and stats) are enriched, never overwritten: only display name (when
    missing) and the profile roster are written.
    """
    users = load_association_users(users_path)
    linked: dict[str, tuple[str, ...]] = {}
    names: dict[str, str] = {}
    for user in users:
        if user.profile_id:
            profiles = linked.get(user.discord_id, ())
            linked[user.discord_id] = tuple(sorted({*profiles, user.profile_id}))
        if user.display_name:
            names.setdefault(user.discord_id, user.display_name)

    players = 0
    links_total = 0
    for discord_id, profiles in linked.items():
        player_id = f"player:{ladder_id}:{discord_id}"
        existing = await database[PLAYERS_COLLECTION].find_one({"_id": player_id})
        if existing is None:
            player = PlayerModel(
                _id=player_id,
                ladder_id=ladder_id,
                user_id=discord_id,
                display_name=names.get(discord_id, discord_id),
                rating=INITIAL_RATING,
                rating_max=INITIAL_RATING,
                matches_count=0,
                wins=0,
                losses=0,
                streak=0,
                registered_at=0,
            )
            doc = player.to_mongo()
        else:
            doc = dict(existing)
            if discord_id not in doc.get("display_name", "") and names.get(discord_id):
                doc["display_name"] = names[discord_id]
        doc["legacy_linked_profiles"] = list(profiles)
        doc.setdefault("legacy_detached_profiles", [])
        await database[PLAYERS_COLLECTION].replace_one({"_id": player_id}, doc, upsert=True)
        players += 1
        links_total += len(profiles)

    return ProfileLinksReport(players=players, linked_profiles=links_total)
