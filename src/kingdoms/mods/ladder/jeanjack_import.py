"""JeanJack data import: CSV extraction + idempotent replay pipeline (#138).

Imports the legacy JeanJack V2.0 ladder into the Kingdoms ladder mod:
- users CSV (discord_id, ladder_name, profile_id, profile_created_at) —
  each Discord user becomes a ladder player; the AoE2 profile_ids become
  game-profile links for the /register validation seam;
- matches CSV (one row per side-profile cartesian product) — deduplicated
  by ladder_match_id, COMPLETED only, replayed in chronological order.

The rating is the one the players knew on JeanJack: each COMPLETED match
replays its recorded elo_before/elo_diff into rating_history (reason
MATCH_RESULT), and the final player rating is the last replayed value.
CANCELED matches are skipped; players without matches start at the
ladder's initial rating. user_id = the Discord id, so imported players
keep their identity when they join the new ladder.

**Detached profiles.** A player can appear in matches with AoE2 profiles
he later unlinked (the profile link is gone from the users export). The
import still registers the player (name and rating come from the match
rows) and records every profile id seen in his match rows, split in two
sets: the *linked* ones (present in the users export, active for the
/register validation seam) and the *detached* ones (ghost links kept for
traceability, never active). A detached profile is never treated as a
current link: the player must /register a live profile to join a queue.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from kingdoms.mods.ladder.models import (
    MATCH_STATUS_COMPLETED,
    MATCHES_COLLECTION,
    PLAYERS_COLLECTION,
    RATING_HISTORY_COLLECTION,
    RATING_REASON_MATCH_RESULT,
    MatchModel,
    MatchSideModel,
    PlayerModel,
    RatingHistoryModel,
)

INITIAL_RATING = 1000


@dataclass(frozen=True, slots=True)
class JeanJackUser:
    """One legacy user row: a Discord id with one AoE2 profile link."""

    discord_id: str
    ladder_name: str
    profile_id: str
    profile_created_at: str


@dataclass(frozen=True, slots=True)
class JeanJackProfileLinks:
    """A player's AoE2 profiles, split linked vs detached."""

    linked: tuple[str, ...] = ()
    detached: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JeanJackMatch:
    """One deduplicated legacy match (COMPLETED, with a winner)."""

    ladder_match_id: str
    match_id: str
    map_name: str
    completed_at: int
    host_discord_id: str
    guest_discord_id: str
    host_rating_before: int
    host_delta: int
    guest_rating_before: int
    guest_delta: int
    winner_discord_id: str
    host_name: str
    guest_name: str
    host_profiles: tuple[str, ...] = ()
    guest_profiles: tuple[str, ...] = ()


def load_users(path: Path) -> list[JeanJackUser]:
    """Load the users CSV, skipping rows without a Discord id."""
    users: list[JeanJackUser] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            discord_id = (row.get("discord_id") or "").strip()
            if not discord_id:
                continue
            users.append(
                JeanJackUser(
                    discord_id=discord_id,
                    ladder_name=(row.get("ladder_name") or "").strip(),
                    profile_id=(row.get("profile_id") or "").strip(),
                    profile_created_at=(row.get("profile_created_at") or "").strip(),
                )
            )
    return users


def load_matches(path: Path) -> list[JeanJackMatch]:
    """Load the matches CSV, deduplicated by ladder_match_id.

    The export contains one row per side-profile combination (cartesian
    product of the players' linked AoE2 accounts); only the first row of
    each ladder_match_id carries the match. CANCELED rows and rows
    without a winner are skipped entirely.
    """
    matches: dict[str, JeanJackMatch] = {}
    profiles_seen: dict[str, dict[str, set[str]]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            match_key = (row.get("ladder_match_id") or "").strip()
            row_profiles = (
                (row.get("host_discord_id") or "").strip(),
                (row.get("guest_discord_id") or "").strip(),
                (row.get("host_profile_id") or "").strip(),
                (row.get("guest_profile_id") or "").strip(),
            )
            if match_key and row_profiles[2] and row_profiles[3]:
                sides = profiles_seen.setdefault(match_key, {})
                sides.setdefault(row_profiles[0], set()).add(row_profiles[2])
                sides.setdefault(row_profiles[1], set()).add(row_profiles[3])
            if not match_key or match_key in matches:
                continue
            status = (row.get("status") or "").strip()
            winner = (row.get("winner_discord_id") or "").strip()
            if status != MATCH_STATUS_COMPLETED or not winner:
                matches[match_key] = _skipped(match_key)
                continue
            host = (row.get("host_discord_id") or "").strip()
            guest = (row.get("guest_discord_id") or "").strip()
            if not host or not guest:
                matches[match_key] = _skipped(match_key)
                continue
            try:
                match = JeanJackMatch(
                    ladder_match_id=match_key,
                    match_id=(row.get("match_id") or "").strip(),
                    map_name=(row.get("map_name") or "").strip(),
                    completed_at=int(row.get("game_completed_at") or 0),
                    host_discord_id=host,
                    guest_discord_id=guest,
                    host_rating_before=int(row.get("host_elo_before") or 0),
                    host_delta=int(row.get("host_elo_diff") or 0),
                    guest_rating_before=int(row.get("guest_elo_before") or 0),
                    guest_delta=int(row.get("guest_elo_diff") or 0),
                    winner_discord_id=winner,
                    host_name=(row.get("host_name") or "").strip(),
                    guest_name=(row.get("guest_name") or "").strip(),
                )
            except ValueError:
                matches[match_key] = _skipped(match_key)
                continue
            matches[match_key] = match
    played: list[JeanJackMatch] = []
    for match in matches.values():
        if not match.winner_discord_id:
            continue
        sides = profiles_seen.get(match.ladder_match_id, {})
        played.append(
            replace(
                match,
                host_profiles=tuple(sorted(sides.get(match.host_discord_id, set()))),
                guest_profiles=tuple(sorted(sides.get(match.guest_discord_id, set()))),
            )
        )
    played.sort(key=lambda m: (m.completed_at, m.ladder_match_id))
    return played


def _skipped(match_key: str) -> JeanJackMatch:
    """Skip a row observed but not imported (CANCELED, no winner)."""
    return JeanJackMatch(
        ladder_match_id=match_key,
        match_id="",
        map_name="",
        completed_at=0,
        host_discord_id="",
        guest_discord_id="",
        host_rating_before=0,
        host_delta=0,
        guest_rating_before=0,
        guest_delta=0,
        winner_discord_id="",
        host_name="",
        guest_name="",
    )


def resolve_profiles(
    users: list[JeanJackUser], matches: list[JeanJackMatch]
) -> dict[str, JeanJackProfileLinks]:
    """Split each player's seen profiles into linked vs detached.

    Linked: present in the users export (active). Detached: seen in match
    rows but absent from the users export (ghost links, traceability
    only). Display names also surface here for players who only appear
    in matches (their user rows are gone with the unlinked profiles).
    """
    linked: dict[str, set[str]] = {}
    seen: dict[str, set[str]] = {}
    for user in users:
        if user.profile_id:
            linked.setdefault(user.discord_id, set()).add(user.profile_id)
    for match in matches:
        for discord_id, profile_ids in (
            (match.host_discord_id, match.host_profiles),
            (match.guest_discord_id, match.guest_profiles),
        ):
            for profile_id in profile_ids:
                seen.setdefault(discord_id, set()).add(profile_id)
    return {
        discord_id: JeanJackProfileLinks(
            linked=tuple(sorted(linked.get(discord_id, set()))),
            detached=tuple(sorted(seen.get(discord_id, set()) - linked.get(discord_id, set()))),
        )
        for discord_id in set(linked) | set(seen)
    }


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Counts of the idempotent import, printed by the CLI."""

    players: int
    matches: int
    rating_history_entries: int
    detached_profiles: int


async def import_jeanjack(
    database: Any,
    ladder_id: str,
    users_path: Path,
    matches_path: Path,
) -> ImportReport:
    """Import the JeanJack ladder data into the given ladder (idempotent).

    Re-running the import over the same data is a no-op: documents are
    upserted with deterministic ids (player:{ladder}:{user},
    jj:{ladder_match_id}, rh:jj:{ladder_match_id}:{user}).
    """
    users = load_users(users_path)
    matches = load_matches(matches_path)
    profiles = resolve_profiles(users, matches)

    imported_matches = 0
    imported_history = 0
    players: dict[str, dict[str, Any]] = {}
    for user in users:
        if user.discord_id not in players:
            players[user.discord_id] = {
                "user_id": user.discord_id,
                "display_name": user.ladder_name or user.discord_id,
                "rating": INITIAL_RATING,
                "wins": 0,
                "losses": 0,
            }

    for match in matches:
        match_doc = MatchModel(
            _id=f"jj:{match.ladder_match_id}",
            ladder_id=ladder_id,
            status=MATCH_STATUS_COMPLETED,
            created_at=match.completed_at,
            host=MatchSideModel(user_id=match.host_discord_id),
            guest=MatchSideModel(user_id=match.guest_discord_id),
            origin="jeanjack-import",
            map_snapshot={"name": match.map_name} if match.map_name else {},
            winner_user_id=match.winner_discord_id,
            loser_user_id=match.guest_discord_id
            if match.winner_discord_id == match.host_discord_id
            else match.host_discord_id,
            confirm_user_id="import",
            completed_at=match.completed_at,
        )
        await database[MATCHES_COLLECTION].replace_one(
            {"_id": match_doc.id}, match_doc.to_mongo(), upsert=True
        )
        imported_matches += 1

        for user_id, before, delta in (
            (match.host_discord_id, match.host_rating_before, match.host_delta),
            (match.guest_discord_id, match.guest_rating_before, match.guest_delta),
        ):
            entry = RatingHistoryModel(
                _id=f"rh:jj:{match.ladder_match_id}:{user_id}",
                ladder_id=ladder_id,
                match_id=match_doc.id,
                user_id=user_id,
                rating_before=float(before),
                rating_after=float(before + delta),
                delta=float(delta),
                k_used=0.0,
                reason=RATING_REASON_MATCH_RESULT,
                created_at=match.completed_at,
            )
            await database[RATING_HISTORY_COLLECTION].replace_one(
                {"_id": entry.id}, entry.to_mongo(), upsert=True
            )
            imported_history += 1

            stats = players.setdefault(
                user_id,
                {"user_id": user_id, "display_name": user_id, "rating": INITIAL_RATING, "wins": 0, "losses": 0},
            )
            match_name = _match_name(match, user_id)
            if match_name:
                stats["display_name"] = match_name
            stats["rating"] = before + delta
            if user_id == match.winner_discord_id:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

    for user_id, stats in players.items():
        player = PlayerModel(
            _id=f"player:{ladder_id}:{user_id}",
            ladder_id=ladder_id,
            user_id=user_id,
            display_name=stats["display_name"],
            rating=stats["rating"],
            rating_max=stats["rating"],
            matches_count=stats["wins"] + stats["losses"],
            wins=stats["wins"],
            losses=stats["losses"],
            streak=0,
            registered_at=0,
        )
        doc = player.to_mongo()
        links = profiles.get(user_id)
        if links is not None:
            doc["jeanjack_linked_profiles"] = list(links.linked)
            doc["jeanjack_detached_profiles"] = list(links.detached)
        await database[PLAYERS_COLLECTION].replace_one({"_id": player.id}, doc, upsert=True)

    detached = sum(len(links.detached) for links in profiles.values())
    return ImportReport(
        players=len(players),
        matches=imported_matches,
        rating_history_entries=imported_history,
        detached_profiles=detached,
    )


def _match_name(match: JeanJackMatch, user_id: str) -> str:
    """Resolve a side's display name from the match row (ghost players)."""
    if user_id == match.host_discord_id:
        return match.host_name
    if user_id == match.guest_discord_id:
        return match.guest_name
    return ""
