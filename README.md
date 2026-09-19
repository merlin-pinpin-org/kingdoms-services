# kingdoms-services

Core bot, Discord implementation, shared utilities, and YAML configs. All
Python code for the Kingdoms Discord bot platform.

## Automation

- **Auto-triage** (`.github/workflows/auto-triage.yml`): assigns, labels and
  links every new issue/PR.
- **Roadmap ping** (`.github/workflows/roadmap-ping.yml`): notifies
  `merlin-pinpin/kingdoms` (via `repository_dispatch`) whenever an issue
  changes state here, so `ROADMAP.md` stays in sync automatically. Requires
  the `ROADMAP_DISPATCH_PAT` repository secret: a fine-grained PAT with
  **"Contents: read and write"** on `merlin-pinpin/kingdoms`.

## Rules

See [AGENTS.md](AGENTS.md) (agent rules) and the
[operating model](https://github.com/merlin-pinpin/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
in the `kingdoms` repo.
