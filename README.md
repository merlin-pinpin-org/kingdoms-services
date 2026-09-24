# kingdoms-services

Core bot, Discord implementation, shared utilities, and YAML configs. All
Python code for the Kingdoms Discord bot platform.

## Automation

- **Auto-triage** (`.github/workflows/auto-triage.yml`): assigns, labels and
  links every new issue/PR.
- **Docs** (`.github/workflows/docs.yml`): this repo hosts its own generated
  technical documentation (`docs/DEVELOPMENT/pydoc/`, regenerated with
  `make docs`); every PR fails when the committed copy is stale.
- **Generated artifacts in `kingdoms`**: `ROADMAP.md` and
  `docs/DEPENDENCIES.md` are refreshed by the sync skills
  ([Update roadmap](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/SKILLS/update-roadmap.md),
  [Update dependencies](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/SKILLS/update-dependencies.md)):
  the sync scripts run locally and the result is committed to the PR branch.
  The former `roadmap-ping.yml`/`dependencies-ping.yml` dispatch pings and
  the `ROADMAP_DISPATCH_PAT` secret are no longer used and can be deleted.

## Deploying a pull request

Post `/deploy` (or `/deploy <env>`) as a comment on a pull request of this
repository to build the PR head, push the image to GHCR
(`pr-<id>-<timestamp>-<sha7>`), pin it on the kingdoms-infra state branch
`deploy/<env>` and deploy it on that environment's runner — the tracking
comment on the PR follows the deployment through building → deploying →
deployed.

- The environment defaults to `test`; any non-protected environment works
  (validated against the `envs/` directory of kingdoms-infra).
- `prod` never deploys a PR: it pins released `vX.Y.Z` images only.
- Only repository collaborators with write access may use it; fork PRs are
  rejected.

## Rules

See [AGENTS.md](AGENTS.md) (agent rules) and the
[operating model](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
in the `kingdoms` repo.
