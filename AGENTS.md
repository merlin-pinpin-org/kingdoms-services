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

- **Read first:** [kingdoms/docs/VIBEWORKFLOW.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
  describes the operating model (roles, session loop, approvals).
- **Humans never check out, write code, or run scripts.** This platform is
  a pure vibe-coding test: the agent does 100% of the technical work. Never
  propose a solution that requires a human to run a CLI command, a script,
  or any local tooling — the only manual technical actions are clicks in
  the GitHub web UI (approving PRs, production deployment approvals,
  one-time admin), performed by authorized humans.
- All code, comments, documentation, commit messages, PR titles and PR
  descriptions are written in **English**.
- Python 3.12, type hints everywhere, `ruff` + `mypy` clean.
- Merges to `main`, tags and releases are gated by the repository rulesets
  and required checks (enforced by GitHub, not by this document).
- **Never commit secrets** (tokens, passwords, API keys, private keys,
  `.env` values): real credentials live only in GitHub **environment
  secrets**, injected by the CD runner at deploy time (nothing secret is
  stored on the VPS); repositories carry `.env.example` placeholders only. Before making any repository public, scan the full
  git history for leaked secrets (`git log -p | grep -E "ghp_|github_pat_|AKIA|PRIVATE KEY"`).
- **Anyone can run the tests locally**: `make lint`, `make typecheck` and
  `make test` require only a public clone — no credentials, no Discord
  token, no external services. Keep it that way.
- Authorized contributors (per the GitHub environment protection rules)
  can deploy to the **test** environment; higher environments are gated
  by their own deployment triggers.
- Keep the documentation in `kingdoms` in sync: a code change without its doc
  update is incomplete. Reference issues fully qualified
  (e.g. `kingdoms-services#12`) since cross-repo references are common.
- Link PRs to their issue with a closing keyword in the description
  (`Closes #N`): this populates the GitHub "Development" section and closes
  the issue on merge.
- **PR draft status is the merge-readiness signal** (see
  `kingdoms/docs/VIBEWORKFLOW.md`): always open PRs as drafts; mark a PR
  ready for review only when, from your point of view, it can be merged
  (checks green, implementation complete, self-review done, docs updated);
  keep or return it to draft (`gh pr ready --undo`) while work remains A user-facing change is also validated
  live first: deploy the PR to the test environment (the `/deploy` PR
  comment) and let the human check the behavior in Discord — only then
  mark the PR ready and ask for the merge; if fixes are needed, return
  the PR to draft.
- **The merge is one human click.** The agent never merges: it prepares
  PRs to merge-ready (ready for review, checks green, docs updated, issue
  linked) and reports the PR URL; the developer or ops clicks **Merge**
  in the GitHub web UI. The `main` rulesets enforce the hard gate
  (required checks, squash only). GitHub automerge is intentionally not
  used: it merges as soon as checks land, ignoring the game designer's
  Discord validation.
- **Issue templates are mandatory**: blank issues are disabled on this
  repository. Create every issue from the template matching its kind
  (`gh issue create --template Feature|Bug|Sub-task|Task`) and keep the
  required sections (`## Objective`, `## Context`, `## Specifications`,
  `## Acceptance criteria`, `## Dependencies`). The "Validate issue"
  workflow flags non-compliant issues `invalid` — recreate them properly
  rather than editing around the flag.
- All user-facing strings go through the **i18n system**
  (`kingdoms-services#17`): English default (`config/locales/en.yaml`),
  French available (`config/locales/fr.yaml`). Never hardcode user-facing
  text.
- The core (`src/kingdoms/core/`) is **platform-agnostic**: no discord.py
  imports in core. Discord code lives in `src/kingdoms/discord/` and
  implements `IPlatform`.
- Mods declare their channel categories and roles via `ModRegistry`
  (`kingdoms-services#26`); they never create channels/roles directly.
- Mods and game providers reference **logical role keys**, never hardcoded
  Discord role IDs (`kingdoms-services#26`).
- Every mod or game provider added to this repo must have its documentation
  updated in `kingdoms` (source of truth).
- Daily workflow uses **Makefile tasks** (`make lint`, `make test`,
  `make typecheck`), not ad-hoc Python scripts.
- Custom IDs follow the convention `<mod>:<component>:<payload>`
  (see `kingdoms` docs, Discord components guide).
- Run `make lint` and `make test` before pushing. All tests must pass.
- **Sandbox limits are covered by GitHub Actions**: anything that cannot run
  in the dev sandbox (Docker Compose boot, image build, real MongoDB/Redis,
  entrypoint/preflight paths) must be exercised by a CI workflow instead.
  When a check cannot run locally, add or extend the workflow that validates
  it — never leave it unverified.

## Testing rules

The testing strategy is defined in
[kingdoms/docs/architecture/testing.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/architecture/testing.md)
(hybrid pyramid). The short version — use the **right double for the right
depth**:

- **No Discord at all** for core tests (`src/kingdoms/core/` is
  platform-agnostic; use plain fixtures, in-memory stores).
- **MockDiscord** (`tests/mocks/discord_mock.py`, kingdoms-services#2) for
  adapter and UI-builder unit tests: mock objects subclassing the real
  discord.py classes, recording both UI surfaces per ADR-0009.
- **SimCord** (dev-dependency `simcord[pytest]`, behavioral journeys in
  `tests/integration/test_simcord_journeys.py`) for anything that depends
  on discord.py dispatch: slash commands, buttons, selects, modals,
  permissions, view timeouts, Components V2. Since kingdoms-services#12
  the shared `simcord_bot` fixture (`tests/conftest.py`) builds the real
  bot via the production factory `create_bot()` — journeys exercise the
  actual dispatch, tree and wiring; UI-pattern journeys may register
  ad-hoc commands on the real tree. Drive the bot as a user
  (`alice.slash()`, `alice.click()`, `alice.submit_modal()`), never call a
  command callback directly. No token, no network, no sleeps —
  `env.advance_time()` fires timeouts.
- Never use `MagicMock` as a substitute for Discord permissions or cache
  state; never call `bot.run()` in a test.

## Roadmap

`ROADMAP.md` lives in the `kingdoms` repo and is synced by
`kingdoms/scripts/sync_roadmap.py` (see the
[Update roadmap](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/SKILLS/update-roadmap.md)
skill): the script reads the issue states and the synced `ROADMAP.md` is
committed to the PR branch. Do not edit the roadmap manually for status
changes — change the issue state instead, then run the sync script on an
open `kingdoms` PR.

## Session checklist (do this by default)

At the end of every session:

1. **Docs vs code**: update the docs in `kingdoms` that describe what you
   changed (architecture, ADRs, MODS docs).
2. **Issues**: make sure the issues you touched reflect reality — acceptance
   criteria, state, and the `## Dependencies` checkboxes.
3. **Labels**: every issue you open must carry exactly one `size/*`
   (XS/S/M/L/XL), one `priority/P0-P3` and a `phase-N` label, plus a
   `## Dependencies` section (see
   [kingdoms/docs/SKILLS/update-dependencies.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/SKILLS/update-dependencies.md)).
4. **Dependency graph**: `kingdoms/docs/DEPENDENCIES.md` is regenerated by
   `kingdoms/scripts/sync_dependencies.py` (see the
   [Update dependencies](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/SKILLS/update-dependencies.md)
   skill); after issue edits, run the script on an open `kingdoms` PR and
   verify the graph matches intent.

## Dependencies

`docs/DEPENDENCIES.md` lives in the `kingdoms` repo and is regenerated by
`kingdoms/scripts/sync_dependencies.py` (same mechanism as the roadmap
sync). Priorities (`priority/P0-P3` labels) come from
critical-path analysis and are maintained by that script — do not set them
by hand unless the analysis is wrong; fix the dependencies or sizes
instead.

## See also

- [kingdoms/AGENTS.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/AGENTS.md)
- [kingdoms/docs/ARCHITECTURE.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/ARCHITECTURE.md)
- [kingdoms/ROADMAP.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/ROADMAP.md)
- [kingdoms/docs/DEPENDENCIES.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/DEPENDENCIES.md) — dependency graph, critical path, priorities
