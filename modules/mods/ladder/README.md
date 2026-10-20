# ladder (mod)

The ladder mod's **modular home** (kingdoms#178, kingdoms-services#211):
one self-contained directory per mod, so the future extraction to an
external repo is a pure move.

- `mod.yaml` — the mod declaration (schema validated at startup).
- `src/ladder/` — the mod package, imported as `kingdoms.mods.ladder`.
- `locales/` — per-mod locale catalogs.
- `tests/` — mod-owned tests.

⚠️ **The ladder code itself still lives on the integration branch
`vibe/merlin/main`** (PR merlin-pinpin-org/kingdoms-services#245), in the
legacy layout (`src/kingdoms/mods/ladder/` + `config/mods/ladder.yaml`).
The mechanical move of the code into this directory happens in that PR's
follow-up, one PR per mod, CI green each time (kingdoms#178 migration
plan). This directory pins the target layout and the real declaration.

Canonical rules and environment documentation live in the `kingdoms` repo:
`docs/MODS/ladder/` — never here.
