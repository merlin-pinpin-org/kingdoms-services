# Data dumps (kingdoms-services#138)

CSV dumps loaded into (and exported from) live environments by the
**data-transfer workflow** (`.github/workflows/data-transfer.yml`,
`workflow_dispatch` with `environment` + `operation` + `perimeter` +
`ladder_id`). Dumps live in the sources — **never in the Docker
image**: the workflow docker-cp's them into the env's kingdoms-core
container at run time.

Layout — one directory per perimeter, timestamp prefix so dumps sort
chronologically:

- `data/core/<timestamp>-users.csv` — the association dumps
  (discord_id, display_name, profile_id; one row per profile link,
  duplicate rows exist)
- `data/mods/ladder/<timestamp>-matches.csv` — the match-history dumps
  (one row per match, minimal columns; the import deduplicates by
  ladder_match_id and skips CANCELED rows)

Imports always take the **latest** dump of their perimeter; exports
always create a new timestamped one — history is kept, nothing is
overwritten. The two perimeters are independent and idempotent.

Known quirk covered by the import: players who unlinked their AoE2
profiles are absent from users.csv but appear in matches.csv — they are
imported as players with `legacy_detached_profiles` (ghost links kept
for traceability, never active for /register).

## Season tools (kingdoms-services#205)

- **Full season import** — seed + associations + matches, idempotent:

  ```
  PYTHONPATH=src python -m kingdoms.mods.ladder.season_import_cli \
      config/games/aoe2/season1.yaml data/core/<ts>-users.csv data/mods/ladder/<ts>-matches.csv [owner_ref]
  ```

- **Season report** — replay a matches dump chronologically and print
  every standings table (Elo legacy replay, wins, winrate, activity,
  streaks, Glicko-2) plus the condensed match list:

  ```
  PYTHONPATH=src python -m kingdoms.mods.ladder.season_report_cli \
      data/mods/ladder/<ts>-matches.csv [--top N]
  ```

  Elo replays the legacy deltas verbatim (the ratings the players
  knew); Glicko-2 replays the same results through the repo system for
  a deviation-aware view. Civs and durations are not in the minimal
  dump (columns dropped in #138) so they are not shown.
