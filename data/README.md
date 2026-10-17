# Dossiers de data de seed

Chaque dossier porte un type de data, versionnée par horodatage
(`YYYYMMDDTHHMMSSZ-<kind>.<ext>`). Le seed est idempotent : relancer
un import saute ce qui existe déjà (mêmes ids).

- `users/` — associations Discord↔profil de jeu (CSV :
  `discord_id,display_name,profile_id`), importées par
  `import_profile_links`.
- `ladder/` — data générique du ladder (ladders, saisons).
- `ladder/<ladder-slug>/` — un sous-dossier par ladder réel :
  - `<ts>-season.yaml` — le season document : `game_key`, `maps`
    (catalogue), `map_pools` (rotations datées via `activated_on`),
    `ladders` (ladder + saison), `civs`,
    `provider_mappings` (tables nom catalogue → id provider).
  - `<ts>-matches.csv` — l'historique des matchs (dump legacy),
    importé par `import_legacy` (elo replay).
- `core/aoe2techtree/` — dataset des civs (MIT, à ne pas éditer à la
  main) ; le refresh (`content_refresh_cli`) en dérive les posts.
- `core/maps/ATTRIBUTION.md` — attribution Liquipedia (CC-BY-SA 3.0)
  pour les maps enrichies par le provider.

## Rotations de map pools (season YAML)

```yaml
map_pools:
  - name: ladder_cf_s1_r1          # l'id du pool (slug, minuscules)
    description: "S1 rotation 1 — 16 avril"
    activated_on: "2026-04-16"     # date d'activation (UTC)
    maps: [Arabia, ...]            # noms du catalogue `maps:`
ladders:
  - owner_ref: "guild:default"
    name: "Ladder CF"
    map_pool: ladder_cf_s1_r1      # pool actif du ladder
    season:
      name: "Season 1"
      map_pool: ladder_cf_s1_r3    # pool actif de la saison
```

Les rotations sont rejouées à leurs dates déclarées (`activated_on`),
dans l'ordre ; la dernière reste active.

## Imports (CLIs)

```
uv run python -m kingdoms.mods.ladder.season_import_cli <season.yaml> <users.csv> <matches.csv> <guild_id>
uv run python -m kingdoms.core.games.aoe2.content_refresh_cli
```

Le refresh enrichit aussi les maps sans image via l'API Liquipedia
(RENSEIGNER `LIQUIPEDIA_CONTACT` dans l'environnement).
