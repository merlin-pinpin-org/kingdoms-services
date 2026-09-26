# kingdoms-services — developer guide

`kingdoms-services` holds all the Python code of the Kingdoms Discord bot
platform: the platform-agnostic core, the Discord platform implementation
(discord.py), the mods, and the YAML configs.

Read the shared
[conventions](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/CONVENTIONS.md)
and the
[operating model](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
in the `kingdoms` repo. This page covers what is specific to working *in
this repo*.

## Layout

| Path | Content |
| ---- | ------- |
| `AGENTS.md` | Short agent entry point (points here) |
| `src/kingdoms/core/` | Platform-agnostic core — **no discord.py imports** |
| `src/kingdoms/discord/` | Discord platform (implements `IPlatform`) |
| `src/kingdoms/mods/` | Mods (channels categories and roles declared via `ModRegistry`) |
| `config/` | YAML configs, `config/locales/` (i18n), `config/mods/` |
| `docs/DEVELOPMENT/pydoc/` | Generated technical docs — regenerate with `make docs` (freshness-checked on every PR) |
| `tests/` | Unit + integration tests, `tests/mocks/` (MockDiscord) |
| `Makefile` | `make lint`, `make typecheck`, `make test`, `make docs` |

## Toolchain

Python 3.12, type hints everywhere, `ruff` + `mypy` clean. Daily workflow
uses **Makefile tasks** (`make lint`, `make typecheck`, `make test`,
`make docs`), not ad-hoc Python scripts. Anyone can run them with a public
clone — no credentials, no Discord token, no external services; keep it
that way. Run `make lint` and `make test` before pushing; all tests must
pass.

## Architecture rules

- The core (`src/kingdoms/core/`) is **platform-agnostic**: no discord.py
  imports in core. Discord code lives in `src/kingdoms/discord/` and
  implements `IPlatform`.
- All user-facing strings go through the **i18n system**: English default
  (`config/locales/en.yaml`), French available (`config/locales/fr.yaml`).
  Never hardcode user-facing text.
- Mods declare their channel categories and roles via **`ModRegistry`**;
  they never create channels/roles directly. Mods and game providers
  reference **logical role keys**, never hardcoded Discord role IDs.
- Custom IDs follow the convention `<mod>:<component>:<payload>` (see the
  kingdoms Discord components guide).
- Every mod or game provider added here must have its documentation
  updated in `kingdoms` (source of truth), including
  `docs/MODS/<mod-name>/`.

## Bot logs channel

Every guild gets a dedicated **bot logs channel** (`🤖-bot-logs`) where the
bot posts its lifecycle events: startup announcement, status changes,
crashes, start/stop/restart. The design (kingdoms-services#109):

- **Channel naming convention**: channels created by the bot always
  carry an emoji prefix followed by a dash, e.g. `🤖-bot-logs`.
- **Resolution is cache-aside** (`LogService.resolve_channel`):
  Redis → MongoDB (`channels` collection, `_id` is
  `guild_id:category`) → **adoption** (an existing `🤖-bot-logs`
  channel found by name is reused — the CI/CD bot's ephemeral
  database never duplicates channels) → creation. Deleted channels
  are detected and reprovisioned.
- **Admin-only by default**: @everyone is denied view/send at creation,
  the bot self-allows, and guild admins (plus `BOT_ADMINS`) manage access
  through `/admin` — per-guild policies persist in the
  `channel_access_policies` collection, every change is audited as an
  event in the channel.
- **Lifecycle events are best-effort**: a store failure logs a warning,
  never crashes the bot; repeated crashes collapse into a single
  "crash-loop detected" event.
- The startup announcement (kingdoms-services#52) is the `start` event
  of this flow: a Components V2 layout (accent Container, Section with
  the bot avatar as thumbnail accessory, Separator, link buttons) reusing
  the exact `/status` rendering, carrying a machine-readable footer
  (`kingdoms-deploy env=… image=… kind=… ref=… run=…`) in sub-text —
  read back by the kingdoms-infra battery through the REST API.
- `KINGDOMS_ANNOUNCE_ENABLED=0` silences the startup announcement
  entirely — used by the CI/CD smoke bot so CI boots never post in
  the shared guilds.

## UI SDK (`src/kingdoms/discord/ui`)

Every view, embed or Components V2 layout is built through the UI SDK —
never by instantiating `discord.ui` / `discord.Embed` classes directly in
a feature. `factory.py` holds the bricks and builders (`UILayout`,
`UIEmbed`, `Container`, `Section`, `Text`, `Row`, `Button`, `Action`,
`SelectMenu`, `Separator`, `Thumbnail`); `screens.py` holds the archetypes
(`render_ranking`, `build_config_panel`, `build_match_report`,
`PaginatedScreen`).

The full usage rules — navigation in buttons (never links in V2 text
blocks), the `<mod>:<component>:<payload>` custom ID convention,
build-time budgets and guarantees, testing patterns — live in the
kingdoms repo
[discord-ui skill](https://github.com/merlin-pinpin-org/kingdoms/blob/main/.agents/skills/discord-ui/SKILL.md).
Extend the SDK rather than bypassing it.

## Testing rules

Full strategy:
[kingdoms/docs/architecture/testing.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/architecture/testing.md)
(hybrid pyramid). The short version — use the **right double for the right
depth**:

- **No Discord at all** for core tests (`src/kingdoms/core/` is
  platform-agnostic; use plain fixtures, in-memory stores).
- **MockDiscord** (`tests/mocks/discord_mock.py`) for adapter and
  UI-builder unit tests: mock objects subclassing the real discord.py
  classes, recording both UI surfaces per ADR-0009.
- **SimCord** (dev-dependency `simcord[pytest]`, behavioral journeys in
  `tests/integration/test_simcord_journeys.py`) for anything that depends
  on discord.py dispatch: slash commands, buttons, selects, modals,
  permissions, view timeouts, Components V2. The shared `simcord_bot`
  fixture (`tests/conftest.py`) builds the real bot via the production
  factory `create_bot()` — journeys exercise the actual dispatch, tree and
  wiring. Drive the bot as a user (`alice.slash()`, `alice.click()`,
  `alice.submit_modal()`), never call a command callback directly. No
  token, no network, no sleeps — `env.advance_time()` fires timeouts.
- Never use `MagicMock` as a substitute for Discord permissions or cache
  state; never call `bot.run()` in a test.
- Anything that cannot run in the dev sandbox (Docker Compose boot, image
  build, real MongoDB/Redis, entrypoint/preflight paths) is exercised by
  CI workflows instead.

### Runtime-image purity (kingdoms-services#106)

Test code and test dependencies (SimCord, pytest, mocks) **never land on a
run machine**: the runtime image runs on the environment VPSes and is
built `--no-dev` with only `src/`, `config/` and the entrypoint copied in.
This is enforced, not conventional — two fail-closed guards:

- `make check` and the CI `Purity guard` job fail when `src/` references
  test tooling (`scripts/check_image_purity.py --source-only`);
- the Docker workflow fails unless `import simcord` / `import pytest`
  raises `ModuleNotFoundError` inside the built image
  (`scripts/check_image_purity.py --image`).

### The battery (standalone journey suite)

`make battery` runs the full SimCord journey suite from a clean checkout
at any commit: `uv sync --frozen` + `pytest tests/integration`. It is
in-memory and network-free — an autouse fixture fails any test that
attempts a real socket connection. This is the interface consumed by the
kingdoms-infra post-deploy battery (kingdoms-infra#78), which checks out
this repository at the pinned deploy commit on a GitHub-hosted runner —
never on an environment VPS.

## Deploying

- **Pull request**: post `/deploy [env]` as a comment on the PR (defaults
  to `test`; `prod` is rejected — released images only). The workflow
  builds the PR head, pushes the image `pr-<id>-<timestamp>-<sha7>` to
  GHCR, pins it on the kingdoms-infra state branch `deploy/<env>` and
  deploys it on that environment's runner; the tracking comment on the PR
  follows the deployment. Only collaborators with write access; fork PRs
  rejected. Details in the [README](../README.md).
- **Production**: released `vX.Y.Z` images only, pinned on `deploy/prod`
  by the release pipeline — see the README "Releasing and deploying to
  production" section.
- User-facing changes are validated live on `test` before their PR is
  marked ready (shared convention).
