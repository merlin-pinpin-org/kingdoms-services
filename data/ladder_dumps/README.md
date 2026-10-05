# Ladder data dumps (kingdoms-services#138)

One directory per dump: the CSV exports of a legacy ladder, loaded into
a live environment by the **ladder-init workflow** (kingdoms-infra
`.github/workflows/ladder-init.yml`, `workflow_dispatch` with
`environment` + `dump` + `ladder_id`). The dump lives in the sources —
**never in the Docker image**: the workflow mounts it into the one-shot
import container at run time.

- `users.csv` — discord_id, ladder_name, profile_id, profile_created_at
  (one row per profile link; duplicate rows exist)
- `matches.csv` — one row per side-profile cartesian product; the
  import deduplicates by ladder_match_id and skips CANCELED rows

Known quirk covered by the import: players who unlinked their AoE2
profiles are absent from users.csv but appear in matches.csv — they are
imported as players with `legacy_detached_profiles` (ghost links kept
for traceability, never active for /register).
