# kingdoms-services

Core bot, Discord implementation, shared utilities, and YAML configs. All
Python code for the Kingdoms Discord bot platform.

## Quickstart (no AI agent needed)

Prerequisites: Python 3.12, [`uv`](https://docs.astral.sh/uv/), Docker
(optionally, for the local compose stack).

```bash
git clone https://github.com/merlin-pinpin-org/kingdoms-services.git
cd kingdoms-services
make setup      # uv sync — venv + dependencies
make lint       # ruff
make typecheck  # mypy strict
make test       # pytest — no token, no network, no Docker needed
```

To run the bot locally with MongoDB and Redis:

```bash
cp .env.example .env   # fill in DISCORD_TOKEN (never commit it)
make dev-up             # bot + MongoDB + Redis via docker compose
make dev-logs           # follow the logs
make dev-down           # stop
```

The bot serves `http://localhost:8000/healthz`. Unit and integration
tests (MockDiscord, SimCord) never need a Discord token or network —
see [docs/DEVELOPER.md](docs/DEVELOPER.md) and
[CONTRIBUTING.md](CONTRIBUTING.md) for the full guides.

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

## Releasing and deploying to production

Cut a release with `make release TAG=vX.Y.Z` (`scripts/release.sh`: fails
 closed unless `main` is clean, up to date and all checks green; creates the
tag and the GitHub release, which triggers the image build and the
`deploy/test` pin). Follow a deployment with `make watch-deploy ENV=test
LABEL=<label>` (`scripts/watch_deploy.sh`: watches the pin commit land on
`deploy/<env>`, then the Deploy environment run — the same script the
deploy and release workflows use).


Production never deploys a pull request: it runs released `vX.Y.Z`
images only. The flow is:

1. Tag `vX.Y.Z` on `main` (`make release TAG=vX.Y.Z`, or by hand — the
   `release-tags` ruleset authorizes the classifiers, e.g. `v0.1.0-rc1`).
   The [docker](.github/workflows/docker.yml) workflow builds and publishes
   `ghcr.io/merlin-pinpin-org/kingdoms-services:vX.Y.Z` (the image carries
   the raw tag name — the state branches pin that exact tag), creates the
   GitHub release with generated notes, and pins the image on `deploy/test`.
2. The same workflow then pins the released image on the kingdoms-infra state
   branch `deploy/test` (dispatch to the infra `Pin state` workflow,
   kingdoms-deployer App): the release runs on the test environment, where
   the humans validate it in Discord — same flow as a PR deploy, but with
   the released image and `/status` showing the Release line.
3. Once validated, an authorized collaborator runs the
   [Promote release](.github/workflows/release.yml) workflow
   (`workflow_dispatch`, tag input): it dispatches the `Pin state`
   workflow for `deploy/prod`. The pin push triggers the Deploy environment
   workflow, which waits for the `prod` environment reviewers, then deploys
   on the `env-prod` runner with the pre-deploy backup and health gate.
4. `/status` on Discord shows the Release line (GitHub release link +
   tree) and the prod Deployment run.

## Rules

See [docs/DEVELOPER.md](docs/DEVELOPER.md) (developer guide: toolchain,
architecture rules, testing), [AGENTS.md](AGENTS.md) (agent entry
points) and the
[operating model](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
in the `kingdoms` repo.
