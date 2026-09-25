# Contributing to Kingdoms (kingdoms-services)

This repository holds all the Python code of the platform: the
platform-agnostic core, the Discord implementation, the mods and the YAML
configs. You do not need an AI agent to contribute: everything below runs
with a public clone and no credentials.

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (the only tool you need to install —
  it manages Python and dependencies)
- Docker + Docker Compose (only for the local compose stack and smoke
  tests; unit tests need no Docker, no token, no network)

## Set up

```bash
git clone https://github.com/merlin-pinpin-org/kingdoms-services.git
cd kingdoms-services
make setup        # uv sync — creates the venv and installs dependencies
```

## Your first contribution

1. **Pick or create an issue** (from a
   [template](https://github.com/merlin-pinpin-org/kingdoms-services/issues/new/choose) —
   blank issues are disabled; non-compliant ones are flagged `invalid`).
2. **Create a branch** from `main`:

   ```bash
   git checkout -b feat/my-change main
   ```

3. **Implement** following the architecture rules
   ([docs/DEVELOPER.md](docs/DEVELOPER.md)): platform-agnostic core (no
   discord.py in `src/kingdoms/core/`), i18n for every user-facing string,
   mods declare channels/roles via `ModRegistry` with logical role keys.
4. **Run the checks before pushing** — the same ones CI runs:

   ```bash
   make lint        # ruff
   make typecheck   # mypy strict
   make test        # pytest (MockDiscord + SimCord, no token, no network)
   ```

   If you changed any docstring, regenerate the pydoc so the freshness
   check stays green:

   ```bash
   make docs        # regenerates docs/DEVELOPMENT/pydoc
   ```

5. **Open a pull request.** Conventional Commit title (`feat:`, `fix:`,
   `docs:`, `chore:`, `refactor:`), small focused diff, description
   linking the issue with a closing keyword (`Closes #N`).
6. **Wait for review**; the maintainer merges.

## Running the bot locally (optional)

The repo ships a compose stack for local runs:

```bash
cp .env.example .env      # fill in DISCORD_TOKEN (never commit it)
make dev-up                # builds and starts bot + MongoDB + Redis
make dev-logs              # follow the logs
make dev-down              # stop
```

The bot serves `http://localhost:8000/healthz`. A Discord token is only
needed to connect the bot to a real guild — the test suite never needs
one.

## Testing rules (short version)

Full strategy: [testing.md](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/architecture/testing.md).

- Core tests: no Discord at all (plain fixtures, in-memory stores).
- Adapter/UI unit tests: **MockDiscord** (`tests/mocks/`).
- Dispatch-dependent journeys (slash commands, buttons, modals,
  timeouts): **SimCord** (`tests/integration/test_simcord_journeys.py`);
  drive the bot as a user (`alice.slash()`, `alice.click()`), never call
  a command callback directly, no token, no network, no sleeps.
- Never use `MagicMock` for Discord permissions or cache state.

## Code style

- Python 3.12, type hints everywhere, `ruff` + `mypy` clean.
- Everything in **English**; user-facing strings come from
  `config/locales/` (i18n), never hardcoded.
- Daily commands via the **Makefile** (`make help`-style discovery: read
  the [Makefile](Makefile), every target is documented).
- No secrets in the repo — `.env.example` placeholders only.

## The vibe-coding model

This project is primarily built through an AI-agent workflow
([operating model](https://github.com/merlin-pinpin-org/kingdoms/blob/main/docs/VIBEWORKFLOW.md));
agent sessions run the same checks you just ran. Human contributions are
welcome and follow exactly the path above — no agent required.

## CLA process

- First-time contributors must accept the CLA before their PR can be
  merged.
- Comment `/cla` on your PR or follow the CLA workflow.
- A CLA check runs on every PR from external contributors.

## License

By contributing, you agree that your contributions will be licensed under
the [AGPL-3.0](LICENSE) (see [CLA.md](CLA.md) for the full grant,
including the project's relicensing option).
