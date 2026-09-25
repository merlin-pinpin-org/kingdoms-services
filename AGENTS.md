# AGENTS.md

`kingdoms-services` — all Python code for the Kingdoms Discord bot
platform: core services, Discord platform implementation (discord.py),
mods, YAML configs.

## Read first

- [kingdoms/docs/CONVENTIONS.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/CONVENTIONS.md)
  — conventions shared by the three repositories (language,
  humans-never-code, secrets, PR lifecycle, issues, checks).
- [kingdoms/docs/VIBEWORKFLOW.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
  — operating model (roles, session loop, approvals).
- [docs/DEVELOPER.md](docs/DEVELOPER.md) — this repo's layout, toolchain,
  architecture rules, testing rules, deploy flows.

## Repositories

- `kingdoms` (documentation, source of truth)
- `kingdoms-services` (this repo): core, Discord platform, mods, configs
- `kingdoms-infra`: Docker, CI/CD, GitOps manifests, deployment scripts

## Local rules

- Python 3.12, type hints everywhere, `ruff` + `mypy` clean; daily workflow
  via **Makefile tasks** (`make lint`, `make typecheck`, `make test`,
  `make docs`) — run `make lint` and `make test` before pushing.
- **Never rename a workflow or a workflow job backing a required status
  check** — GitHub matches check contexts by exact name, so a rename
  silently blocks merges (see CONVENTIONS.md, *Checks must pass everywhere*).
- Core is **platform-agnostic** (no discord.py in `src/kingdoms/core/`);
  user-facing strings go through **i18n** (never hardcoded); mods declare
  channels/roles via **`ModRegistry`** with **logical role keys**.
- **UI SDK mandate:** never build `discord.ui` / `discord.Embed` objects
  directly in a feature — every view, embed or Components V2 layout
  is built through the SDK in `src/kingdoms/discord/ui`. The rules
  (bricks, archetypes, navigation-in-buttons, custom IDs, budgets)
  live in the kingdoms repo
  [discord-ui skill](https://github.com/merlin-pinpin-org/kingdoms/blob/main/.agents/skills/discord-ui/SKILL.md).
- Every mod or game provider added here has its documentation updated in
  `kingdoms` (source of truth).
- Issue templates: `## Objective` / `## Context` / `## Specifications` /
  `## Acceptance criteria` / `## Dependencies` (blank issues disabled).
- **Issue references** are GitHub autolinks: same-repo `#N`, cross-repo
  `owner/repo#N` (e.g. `merlin-pinpin-org/kingdoms-infra#78`) — a bare
  `repo#N` renders as plain text; never write it. See CONVENTIONS.md,
  *Documentation is part of the change*.
- **Enrich the docs and skills proactively** (developer-mandated): when
  the session's work teaches a rule, pitfall or pattern, update the
  matching skill page, convention or AGENTS.md entry as part of the
  change — see the kingdoms CONVENTIONS.md
  (*Documentation is part of the change*).
- **Automation mandate:** no one-off commands, for humans or sessions —
  every recurring operation is a committed Makefile target, script or
  workflow, and a useful improvised command is committed ("learned").
  Humans on GitHub only merge PRs and approve prod deploys
  (+ one-time bootstrapping) — see the kingdoms
  [Automate or learn](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/SKILLS/automate-or-learn.md)
  skill and CONVENTIONS.md (*Human GitHub scope*, *Everything is
  automation*).
