# kingdoms (mod)

The kingdoms mod's **modular home** (kingdoms#178, kingdoms-services#211):
one self-contained directory per mod, so the future extraction to an
external repo is a pure move.

- `mod.yaml` — the mod declaration (schema validated at startup).
- `src/kingdoms_mod/` — the mod package (imported as
  `kingdoms.mods.kingdoms`; the package dir name is decided at the code
  move).
- `locales/` — per-mod locale catalogs.
- `tests/` — mod-owned tests.

⚠️ **The kingdoms code itself still lives on the integration branch
`vibe/drasah/main`** (PR merlin-pinpin-org/kingdoms-services#244), in the
legacy layout (`src/kingdoms/mods/kingdoms/` + `config/mods/kingdoms.yaml`).
The mechanical move of the code into this directory is accompanied with
Drasah — one PR per mod, CI green each time (kingdoms#178 migration
plan). This directory pins the target layout and the real declaration.

Canonical rules and environment documentation live in the `kingdoms` repo:
`docs/MODS/kingdoms/` — never here.
