# Data dumps (kingdoms-services#138)

CSV dumps loaded into (and exported from) live environments by the
**legacy-data workflow** (`.github/workflows/legacy-data.yml`,
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
