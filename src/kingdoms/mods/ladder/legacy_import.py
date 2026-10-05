"""Legacy match-history import: CSV extraction + idempotent elo replay (#138).

One of the two independent legacy import processes — the **ladder
perimeter**: the matches CSV (one row per side-profile cartesian
product), deduplicated by ladder_match_id, COMPLETED only, replayed in
chronological order.

The association perimeter (Discord↔AoE2 profile links) is a separate
process: profile_links_import.py.

The rating is the one the players knew on the legacy ladder: each COMPLETED match
replays its recorded elo_before/elo_diff into rating_history (reason
MATCH_RESULT), and the final player rating is the last replayed value.
CANCELED matches are skipped; players without matches start at the
ladder's initial rating. user_id = the Discord id, so imported players
keep their identity when they join the new ladder.

Players absent from any association dump still import from their
match rows (name, rating, stats); their match-seen profile ids are
recorded as legacy_detached_profiles (ghost links kept for
traceability, never active for /register).
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
class LegacyMatch:
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


def load_matches(path: Path) -> list[LegacyMatch]:
    """Load the matches CSV, deduplicated by ladder_match_id.

    The export contains one row per side-profile combination (cartesian
    product of the players' linked AoE2 accounts); only the first row of
    each ladder_match_id carries the match. CANCELED rows and rows
    without a winner are skipped entirely.
    """
    matches: dict[str, LegacyMatch] = {}
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
                match = LegacyMatch(
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
    played: list[LegacyMatch] = []
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


def _skipped(match_key: str) -> LegacyMatch:
    """Skip a row observed but not imported (CANCELED, no winner)."""
    return LegacyMatch(
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


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Counts of the idempotent import, printed by the CLI."""

    players: int
    matches: int
    rating_history_entries: int
    detached_profiles: int


async def import_legacy(
    database: Any,
    ladder_id: str,
    matches_path: Path,
) -> ImportReport:
    """Import the legacy match history into the given ladder (idempotent).

    Re-running the import over the same data is a no-op: documents are
    upserted with deterministic ids (player:{ladder}:{user},
    legacy:{ladder_match_id}, rh:legacy:{ladder_match_id}:{user}).
    """
    matches = load_matches(matches_path)

    imported_matches = 0
    imported_history = 0
    players: dict[str, dict[str, Any]] = {}

    for match in matches:
        match_doc = MatchModel(
            _id=f"legacy:{match.ladder_match_id}",
            ladder_id=ladder_id,
            status=MATCH_STATUS_COMPLETED,
            created_at=match.completed_at,
            host=MatchSideModel(user_id=match.host_discord_id),
            guest=MatchSideModel(user_id=match.guest_discord_id),
            origin="legacy-import",
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
                _id=f"rh:legacy:{match.ladder_match_id}:{user_id}",
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
        await database[PLAYERS_COLLECTION].replace_one({"_id": player.id}, doc, upsert=True)

    return ImportReport(
        players=len(players),
        matches=imported_matches,
        rating_history_entries=imported_history,
        detached_profiles=sum(len(p) for p in (m.host_profiles for m in matches))
        + sum(len(p) for p in (m.guest_profiles for m in matches)),
    )


def _match_name(match: LegacyMatch, user_id: str) -> str:
    """Resolve a side's display name from the match row (ghost players)."""
    if user_id == match.host_discord_id:
        return match.host_name
    if user_id == match.guest_discord_id:
        return match.guest_name
    return ""
