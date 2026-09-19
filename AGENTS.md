# AGENTS.md

## Project

`kingdoms-services` — all Python code for the Kingdoms Discord bot platform:
core services, Discord platform implementation, mods, YAML configs.

## Repositories

- `kingdoms` (documentation, source of truth): architecture, workflows, ADRs,
  mods documentation, `ROADMAP.md`
- `kingdoms-services` (this repo): core, Discord platform, mods, configs
- `kingdoms-infra`: Docker, CI/CD, GitOps manifests, deployment scripts

## Rules for AI agents

- **Read first:** [kingdoms/docs/VIBEWORKFLOW.md](https://github.com/merlin-pinpin/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
  describes the operating model (roles, session loop, approvals).
- All code and comments are written in **English**.
- Python 3.12, type hints everywhere, `ruff` + `mypy` clean.
- Never merge to `main`, tag, or release without explicit developer approval.
- Keep the documentation in `kingdoms` in sync: a code change without its doc
  update is incomplete. Reference issues fully qualified
  (e.g. `kingdoms-services#12`) since cross-repo references are common.
- Link PRs to their issue with a closing keyword in the description
  (`Closes #N`): this populates the GitHub "Development" section and closes
  the issue on merge.

## Roadmap

`ROADMAP.md` lives in the `kingdoms` repo and is synced **automatically** by
its `Sync roadmap` workflow: whenever an issue in this repo is opened,
reopened or closed, the `Roadmap ping` workflow (`.github/workflows/roadmap-ping.yml`)
notifies `kingdoms` via `repository_dispatch`. Do not edit the roadmap
manually for status changes — change the issue state instead. The ping
requires the `ROADMAP_DISPATCH_PAT` secret (fine-grained PAT, "Contents:
read and write" on `merlin-pinpin/kingdoms`); the workflow reports the HTTP
error explicitly if the token is missing or mis-scoped.

## See also

- [kingdoms/AGENTS.md](https://github.com/merlin-pinpin/kingdoms/blob/main/AGENTS.md)
- [kingdoms/docs/ARCHITECTURE.md](https://github.com/merlin-pinpin/kingdoms/blob/main/docs/ARCHITECTURE.md)
- [kingdoms/ROADMAP.md](https://github.com/merlin-pinpin/kingdoms/blob/main/ROADMAP.md)
