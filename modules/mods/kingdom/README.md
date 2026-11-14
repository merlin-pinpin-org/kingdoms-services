# kingdom (mod)

Scaffolded by `botforge mod init` — see the guide:
<https://github.com/jj-guild/botforge/blob/main/docs/GUIDES/mod-init.md>

- Declaration: `conf/mod.yaml` (schema validated at startup)
- Service: `grpc/kingdom/` (its data, its collections, its contract)
- Cog: `cogs/kingdom/` (the Discord layer — calls the service through
  the contract)
- Locales: `conf/locales/en.yaml` + `conf/locales/fr.yaml`
- Tests: `tests/`

Canonical rules and environment documentation live in the `botforge`
repo: `docs/MODS/kingdom/` (README, RULES, ENVIRONMENT) — never here.
