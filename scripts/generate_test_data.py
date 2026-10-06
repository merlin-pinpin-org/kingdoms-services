"""Generate synthetic test data for a full season import (core + mods).

Produces three files, directly consumable by the season import CLI
(``season_import_cli.py <season.yaml> <users.csv> <matches.csv>``):

- a season YAML (game catalog, map pools with rotations, ladder, season);
- a users CSV (discord <-> AoE2 profile associations, minimal columns);
- a matches CSV (1v1 host vs guest, with map, civs, duration, elo).

The data is deterministic (seeded RNG): same arguments, same bytes —
two environments fed from the same generator get identical datasets,
which makes diffs between them meaningful.

Usage:
    python scripts/generate_test_data.py <output_dir> [--players N]
                                        [--matches M] [--seed S]
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

MAPS = [
    "Arabia",
    "Fish n Fish",
    "Kawasan",
    "Hideout",
    "Frozen River",
    "Sacred Springs",
    "Canberra",
    "Haboob",
    "Coast to Mountain",
    "Baltic",
    "Fortified Clearing",
    "Monocle",
    "Dry river",
    "Immersion",
    "Islands",
    "Big Freeze",
    "Dunespring",
    "Land Nomad",
]

CIVS = [
    "incas",
    "dravidians",
    "malay",
    "bengalis",
    "teutons",
    "koreans",
    "ethiopians",
    "vietnamese",
    "franks",
    "lithuanians",
    "japanese",
    "shu",
    "huns",
    "portuguese",
    "goths",
    "magyars",
    "khmer",
    "bulgarians",
    "saracens",
    "spanish",
    "bohemians",
    "romans",
    "cumans",
    "malians",
]

DISPLAY_NAMES = [
    "TestUzikoti",
    "TestNopantsday",
    "TestDrasah",
    "TestGrunthor",
    "TestLeonidas",
    "TestRakussy",
    "TestBabiFort",
    "TestLylzz",
    "TestAubin",
    "TestGaeLife",
    "TestSylvatica",
    "TestLeureduthe",
    "TestAntoine",
    "TestShirar",
    "TestAuriko",
    "TestZer",
    "TestSupaSaymon",
    "TestTointoin",
    "TestCrestfallen",
    "TestEratuss",
]


def _season_yaml(players_count: int, matches_count: int) -> str:
    """Render the season YAML for the synthetic dataset."""
    pool_1 = "\n".join(f'      - "{name}"' for name in MAPS[:6])
    pool_2 = "\n".join(f'      - "{name}"' for name in MAPS[6:12])
    pool_3 = "\n".join(f'      - "{name}"' for name in MAPS[12:])
    maps = "\n".join(
        f'  - name: "{name}"\n    filename: "{name.lower().replace(" ", "_")}"\n'
        f'    description: "Synthetic test map {name}"'
        for name in MAPS
    )
    civs = "\n".join(f'  - name: "{civ.capitalize()}"\n    faction_key: "{civ}"' for civ in CIVS)
    return f"""# Synthetic test season (generated) — {players_count} players, {matches_count} matches.
# Deterministic: regenerate with the same seed to get identical bytes.
game_key: aoe2

maps:
{maps}

civs:
{civs}

map_pools:
  - name: "Test Season — Rotation 1"
    maps:
{pool_1}
  - name: "Test Season — Rotation 2"
    maps:
{pool_2}
  - name: "Test Season — Rotation 3"
    maps:
{pool_3}

ladders:
  - owner_ref: "guild:default"
    name: "AoE2 Test Ladder"
    map_pool: "Test Season — Rotation 1"
    season:
      name: "Test Season"
      start_in_days: 0
      duration_days: 180
      reset_ratings: false
"""


def _make_players(rng: random.Random, players_count: int) -> list[dict[str, str]]:
    """Generate the roster once; users and matches share the same ids."""
    names = list(DISPLAY_NAMES)
    rng.shuffle(names)
    players = []
    for i in range(players_count):
        name = names[i % len(names)]
        if i >= len(names):
            name = f"{name}{i // len(names)}"
        players.append(
            {
                "discord_id": str(100000000000000000 + rng.randrange(10**15)),
                "display_name": name if i % 2 == 0 else "",
                "profile_id": str(100000 + rng.randrange(10**6)),
            }
        )
    return players


def _users_csv(players: list[dict[str, str]]) -> str:
    """Render the users CSV (discord <-> AoE2 profile associations)."""
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["discord_id", "display_name", "profile_id"])
    writer.writeheader()
    writer.writerows(players)
    return buffer.getvalue()


def _matches_csv(rng: random.Random, players: list[dict[str, str]], matches_count: int) -> str:
    """Render the matches CSV (1v1, chronological, with map/civs/duration)."""
    players_count = len(players)
    now = 1790262333
    rows = []
    for i in range(matches_count):
        host_idx = rng.randrange(players_count)
        guest_idx = rng.randrange(players_count - 1)
        if guest_idx >= host_idx:
            guest_idx += 1
        host = players[host_idx]
        guest = players[guest_idx]
        started = now - (matches_count - i) * 3600
        duration = rng.randrange(400, 2600)
        completed = started + duration
        host_wins = rng.random() < 0.5
        host_elo = 1000 + rng.randrange(-80, 80)
        elo_diff = rng.randrange(8, 30)
        row = {
            "ladder_match_id": str(1000 + i),
            "status": "COMPLETED",
            "match_id": str(500000000 + i),
            "map_name": rng.choice(MAPS),
            "game_started_at": str(started),
            "game_completed_at": str(completed),
            "game_duration": str(duration),
            "host_name": host["display_name"] or f"Player{host_idx}",
            "host_discord_id": host["discord_id"],
            "host_profile_id": host["profile_id"],
            "host_elo_before": str(host_elo),
            "host_elo_diff": str(elo_diff if host_wins else -elo_diff),
            "host_faction_key": rng.choice(CIVS),
            "guest_name": guest["display_name"] or f"Player{guest_idx}",
            "guest_discord_id": guest["discord_id"],
            "guest_profile_id": guest["profile_id"],
            "guest_elo_before": str(host_elo + rng.randrange(-60, 60)),
            "guest_elo_diff": str(-elo_diff if host_wins else elo_diff),
            "guest_faction_key": rng.choice(CIVS),
            "winner_discord_id": host["discord_id"] if host_wins else guest["discord_id"],
        }
        rows.append(row)
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "ladder_match_id",
            "status",
            "match_id",
            "map_name",
            "game_started_at",
            "game_completed_at",
            "game_duration",
            "host_name",
            "host_discord_id",
            "host_profile_id",
            "host_elo_before",
            "host_elo_diff",
            "host_faction_key",
            "guest_name",
            "guest_discord_id",
            "guest_profile_id",
            "guest_elo_before",
            "guest_elo_diff",
            "guest_faction_key",
            "winner_discord_id",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def main() -> int:
    """Generate the test dataset into the output directory; exit 0 on success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--players", type=int, default=12)
    parser.add_argument("--matches", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    players = _make_players(rng, args.players)
    (args.output_dir / "test-season.yaml").write_text(_season_yaml(args.players, args.matches), encoding="utf-8")
    (args.output_dir / "test-users.csv").write_text(_users_csv(players), encoding="utf-8")
    (args.output_dir / "test-matches.csv").write_text(_matches_csv(rng, players, args.matches), encoding="utf-8")
    print(f"test data: {args.players} players, {args.matches} matches (seed {args.seed}) -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
