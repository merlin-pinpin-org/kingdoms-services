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

## GitHub identity

- **The session acts with the authenticated maintainer's identity, never a
  bot identity:** all GitHub operations (commits, merges, comments, ops
  commands) run through the maintainer's credentials, so gates that read
  `comment.user.login` (roster, capability matrix) resolve to the
  maintainer's roster entry. The session therefore has exactly the rights
  the roster grants that maintainer — no more, no less — and must never
  assume a separate `xxx[bot]` identity or try to bypass the capability
  matrix.

## Local rules

- Python 3.12, type hints everywhere, `ruff` + `mypy` clean; daily workflow
  via **Makefile tasks** (`make lint`, `make typecheck`, `make test`,
  `make docs`) — run `make lint` and `make test` before pushing.
- **Never rename a workflow or a workflow job backing a required status
  check** — GitHub matches check contexts by exact name, so a rename
  silently blocks merges (see CONVENTIONS.md, *Checks must pass everywhere*).
- **PR merges, cowboy mode, branch cleanup and user-branch rules are
  defined once in the kingdoms CONVENTIONS.md** (single source of
  truth, all three repos): see
  [kingdoms/docs/CONVENTIONS.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/CONVENTIONS.md)
  — *Cowboy mode* (API-blocked merges, maintainer-only direct pushes
  with per-push confirmation, never force-push `main`) and
  *Integration branches* (never delete `vibe/<alias>/main`).
- **No mod, no game, without a rostered owner (non-overridable):** a
  session never creates `src/kingdoms/mods/<mod>/`, `config/mods/<mod>.yaml`,
  `src/kingdoms/core/games/<game>/` or mod docs unless a `CONTRIBUTORS.md`
  entry (kingdoms repo) claims it in its `owns` column — the roster entry
  and its CODEOWNERS delegation come first. The contributors-sync workflow
  flags orphan mods/games and CODEOWNERS drift automatically
  (kingdoms repo, `check_ownership.py` under the protected `workflows/scripts/` path).
- **UI SDK mandate:** never build `discord.ui` / `discord.Embed` objects
  directly in a feature — every view, embed or Components V2 layout
  is built through the SDK in `src/kingdoms/discord/ui`. The rules
  (bricks, archetypes, navigation-in-buttons, custom IDs, budgets)
  live in the kingdoms repo
  [discord-ui skill](https://github.com/merlin-pinpin-org/kingdoms/blob/main/.agents/skills/discord-ui/SKILL.md).
- **Runtime permission mandate:** seeing a button never implies being
  allowed to click it — every privileged interactive item validates the
  interaction at click time through the shared guards
  (`src/kingdoms/discord/guards.py`: `require_admin`/`is_admin`),
  against the live guild state (BOT_ADMINS, guild-admin permissions,
  or the `bot-admins` role via the RolesService). `default_permissions`
  only hides the command entry; it never replaces the check.
- **Guild-level settings & admin surface:** generic, mod-independent
  settings (guild locale, reference timezone, managed channels) are
  **core guild settings** — a mod never re-declares them or ships its
  own locale/timezone pickers; a mod's admin needs extend the pinned
  bot-admins panel through the core runtime seam, never a forked panel;
  user-facing times are Discord `<t:…>` timestamps, never formatted
  strings — see kingdoms CONVENTIONS.md (*Guild-level settings and the
  admin surface*) and the discord-ui skill (*Times are Discord
  timestamps*).
- **Admin surface (transparency rule):** admin messages with actions
  live in the guild's `🛡-bot-admins` channel (AdminChannelService,
  cache-aside provisioning); the BOT_ADMINS are synced into the
  `bot-admins` role — the visible operator roster — while their
  privileges never depend on it (the guards read BOT_ADMINS first);
  the sync is one-way (add, never remove) and re-applied at every
  channel resolution.
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
- **CLI-first mandate:** the `kingdoms` CLI
  (`src/kingdoms/cli/`, [docs/CLI.md](docs/CLI.md)) is the front door
  for environments and ops — doctor (local env checks), local stack,
  remote SSH, runner setup. New environment/ops surface goes in the
  CLI (a module per domain, `main(argv) -> int`), not in ad-hoc
  commands; sessions use `uv run kingdoms ...` and teach it to
  designers instead of improvising shell commands.
