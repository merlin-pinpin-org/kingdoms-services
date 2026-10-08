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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kingdoms.core.games.aoe2.seed import MongoAoE2Database, seed_aoe2
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


def _ms(timestamp: int) -> int:
    """Normalize a dump timestamp to milliseconds (dumps carry seconds)."""
    return timestamp * 1000 if 0 < timestamp < 10**12 else timestamp


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
    owner_ref: str = "guild:default",
) -> SeasonImportReport:
    """Import a full season (catalog, ladder, season, users, matches)."""
    import yaml

    data = yaml.safe_load(season_yaml.read_text(encoding="utf-8"))
    game_key = data["game_key"]
    ladder_spec = {**data["ladders"][0], "owner_ref": owner_ref}
    data["ladders"] = [ladder_spec]

    seed_result = await seed_aoe2(database, data, ladder_seeder=seed_ladders)

    ladder_id = f"ladder:{game_key}:{owner_ref}"
    adapter = MongoAoE2Database(database)
    game_data = GameDataService(adapter)
    ladder_service = LadderService(adapter, game_data)
    season_service = SeasonService(adapter, game_data)

    pool_names = [spec["name"] for spec in data.get("map_pools", []) or []]
    pool_ids = [f"map_pool:{game_key}:{name}" for name in pool_names]
    boundaries = [_ms(b) for b in _rotation_boundaries(matches_csv, len(pool_ids))]
    season_name = ladder_spec.get("season", {}).get("name", "Season 1")
    season_id = f"season:{ladder_id}:{season_name}"
    season = await season_service.get_season(season_id)
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

    return SeasonImportReport(
        seed=seed_result,
        players=legacy_report.players,
        linked_profiles=links_report.bindings,
        matches=legacy_report.matches,
        rating_history_entries=legacy_report.rating_history_entries,
        rotations=rotations,
    )
