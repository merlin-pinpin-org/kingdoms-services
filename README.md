# kingdoms-services

Core bot, Discord implementation, shared utilities, and YAML configs. All
Python code for the Kingdoms Discord bot platform.

## Automation

- **Auto-triage** (`.github/workflows/auto-triage.yml`): assigns, labels and
  links every new issue/PR.
- **Docs** (`.github/workflows/docs.yml`): this repo hosts its own generated
  technical documentation (`docs/DEVELOPMENT/pydoc/`, regenerated with
  `make docs`); every PR fails when the committed copy is stale.
- **PR commands**: generated artifacts in `merlin-pinpin/kingdoms`
  (`ROADMAP.md`, `docs/DEPENDENCIES.md`, generated dev docs) are refreshed
  by PR comment commands (`/roadmap`, `/dependencies`, `/generate-docs`)
  documented in
  [docs/SKILLS/pr-commands.md](https://github.com/merlin-pinpin/kingdoms/blob/main/docs/SKILLS/pr-commands.md).
  The former `roadmap-ping.yml`/`dependencies-ping.yml` dispatch pings and
  the `ROADMAP_DISPATCH_PAT` secret are no longer used and can be deleted.

## Rules

See [AGENTS.md](AGENTS.md) (agent rules) and the
[operating model](https://github.com/merlin-pinpin/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
in the `kingdoms` repo.
