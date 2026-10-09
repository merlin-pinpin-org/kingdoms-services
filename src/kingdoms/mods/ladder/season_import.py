"""Season import: replay a full season dataset into a ladder (#205).

Orchestrates the three idempotent steps of a season load:

1. **catalog + ladder + season** — the season YAML (``seed_aoe2``):
   maps actually played, the pools of each rotation, the ladder, the
   season entity;
2. **associations** — the users dump (``import_profile_links``):
   Discord↔AoE2 profile links on the ladder players;
3. **match history** — the matches dump (``import_legacy``): matches,
   elo replay, players.

Pool rotations: the season YAML's pools are activated on the ladder
in declaration order, at timestamps split over the replayed match
timeline — the real rotation cadence without hand-typed dates. The
final rotation stays active; the season is activated.

All three steps are individually idempotent; the orchestration is
too (same ids skip).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from kingdoms.core.games.aoe2.seed import MongoAoE2Database, seed_aoe2
from kingdoms.core.ids import ladder_id as ladder_id_for
from kingdoms.core.ids import slug_id
from kingdoms.core.services.game_data import GameDataService
from kingdoms.core.services.identity_import import import_identity_links
from kingdoms.core.services.season import SeasonService
from kingdoms.mods.ladder.legacy_import import import_legacy, load_matches
from kingdoms.mods.ladder.seeder import seed_ladders
from kingdoms.mods.ladder.service import LadderService

SEASON_STATE_ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class SeasonImportReport:
    """Counts of the season import, printed by the CLI."""

    seed: dict[str, Any]
    players: int
    linked_profiles: int
    matches: int
    rating_history_entries: int
    rotations: int
    enrolled: int = 0
    provider_enriched: int = 0


def _ms(timestamp: int) -> int:
    """Normalize a dump timestamp to milliseconds (dumps carry seconds)."""
    return timestamp * 1000 if 0 < timestamp < 10**12 else timestamp


def _dated_boundaries(pool_specs: list[dict[str, Any]]) -> list[int]:
    """Resolve the pools' declared activation dates (``activated_on``).

    A dated rotation file beats the timeline-split fallback: the first
    pool starts with the season, each next pool at its declared date
    (naive UTC midnight — the dump's dates are day-granular anyway).
    """
    from datetime import datetime

    boundaries: list[int] = []
    for spec in pool_specs:
        raw = str(spec.get("activated_on", "") or "").strip()
        if not raw:
            return []
        day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
        boundaries.append(int(day.timestamp() * 1000))
    return boundaries


def _rotation_boundaries(matches_path: Path, rotation_count: int) -> list[int]:
    """Split the replayed match timeline into rotation windows.

    Matches replay chronologically; rotation i starts at the timestamp
    of the match that opens its window (even split over the match
    index — the dump carries no per-match rotation metadata).
    """
    matches = load_matches(matches_path)
    if not matches or rotation_count <= 1:
        return []
    n = len(matches)
    boundaries = []
    for i in range(1, rotation_count):
        index = min(max(round(i * n / rotation_count), 1), n - 1)
        boundaries.append(matches[index].completed_at)
    return boundaries


async def import_season(
    database: Any,
    season_yaml: Path,
    users_csv: Path,
    matches_csv: Path,
    guild_id: str,
) -> SeasonImportReport:
    """Import a full season (catalog, ladder, season, users, matches)."""
    import yaml

    data = yaml.safe_load(season_yaml.read_text(encoding="utf-8"))
    game_key = data["game_key"]
    ladder_spec = {**data["ladders"][0], "owner_ref": guild_id}
    data["ladders"] = [ladder_spec]

    seed_result = await seed_aoe2(database, data, ladder_seeder=seed_ladders)

    ladder_id = ladder_id_for(game_key, guild_id)
    adapter = MongoAoE2Database(database)
    game_data = GameDataService(adapter)
    ladder_service = LadderService(adapter, game_data)
    season_service = SeasonService(adapter, game_data)

    pool_specs = data.get("map_pools", []) or []
    pool_ids = [f"map_pool:{game_key}:{slug_id(spec['name'])}" for spec in pool_specs]
    boundaries = _dated_boundaries(pool_specs) or [_ms(b) for b in _rotation_boundaries(matches_csv, len(pool_ids))]
    seasons = await season_service.list_seasons(ladder_id)
    season = seasons[0] if seasons else None
    season_id = season.id if season is not None else ""
    if season is not None and season.state != SEASON_STATE_ACTIVE:
        start = boundaries[0] if boundaries else season.start_at
        await season_service.activate_season(season_id, start)
    rotations = 0
    existing_activations = await adapter.find_ladder_activations(ladder_id)
    already_replayed = bool(existing_activations) and (
        existing_activations[-1]["map_pool_id"] == pool_ids[-1] if pool_ids else False
    )
    if not already_replayed:
        for i, pool_id in enumerate(pool_ids):
            if i == 0:
                continue
            if i - 1 >= len(boundaries) or boundaries[i - 1] <= 0:
                continue
            await ladder_service.set_active_pool(ladder_id, pool_id)
            rotations += 1

    links_report = await import_identity_links(database, users_csv, game_key)
    legacy_report = await import_legacy(database, ladder_id, matches_csv)
    enrolled = await _enroll_players(database, ladder_id, season_id)
    enriched = await _enrich_matches(database, matches_csv)
    return SeasonImportReport(
        seed=seed_result,
        players=legacy_report.players,
        linked_profiles=links_report.bindings,
        matches=legacy_report.matches,
        rating_history_entries=legacy_report.rating_history_entries,
        rotations=rotations,
        enrolled=enrolled,
        provider_enriched=enriched,
    )


async def _enroll_players(database: Any, ladder_id: str, season_id: str) -> int:
    """Enroll every imported player into the season (idempotent).

    The players are created by the match import; enrollment is the
    season's membership. The Discord role sync is bot-side: the
    season-role sweep grants the player role to enrolled members
    once the bot connects.
    """
    if not season_id:
        return 0
    from kingdoms.mods.ladder.models import PLAYERS_COLLECTION

    enrolled = 0
    cursor = database[PLAYERS_COLLECTION].find({"ladder_id": ladder_id})
    async for doc in cursor:
        user_id = str(doc.get("user_id", ""))
        if not user_id:
            continue
        entry_id = f"season_enrollment:{season_id}:{user_id}"
        await database["season_enrollments"].replace_one(
            {"_id": entry_id},
            {"_id": entry_id, "season_id": season_id, "ladder_id": ladder_id, "user_id": user_id},
            upsert=True,
        )
        enrolled += 1
    return enrolled


def _provider_bridge() -> Any | None:
    """Build the cold provider bridge from the environment, or None."""
    uri = os.environ.get("EXT_LIBREMATCH_URI", "").strip()
    if not uri:
        return None
    from kingdoms.ext_librematch.adapter import LibrematchAdapter
    from kingdoms.mods.ladder.provider_bridge import LibrematchProviderBridge

    return LibrematchProviderBridge(LibrematchAdapter(base_url=uri, api_key=os.environ.get("AOE2_API_KEY", "")))


async def _enrich_matches(database: Any, matches_csv: Path) -> int:
    """Best-effort provider enrichment of the imported matches (by match id).

    Matches without a provider id, or behind an unreachable provider,
    keep their CSV data — the CSV already carries the winner, duration,
    map and civs, so this pass only deepens the record.
    """
    bridge = _provider_bridge()
    if bridge is None:
        return 0
    from kingdoms.mods.ladder.models import MATCHES_COLLECTION

    enriched = 0
    for match in load_matches(matches_csv):
        if not match.match_id:
            continue
        details = await bridge.fetch_match(match.match_id)
        if details is None:
            continue
        await database[MATCHES_COLLECTION].update_one(
            {"_id": f"legacy:{match.ladder_match_id}"},
            {"$set": {"provider_details": details, "provider_enriched": True}},
        )
        enriched += 1
    return enriched
