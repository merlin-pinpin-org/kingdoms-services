# Contributing to Kingdoms

Thank you for considering contributing!

## Before you start

- Read [AGENTS.md](AGENTS.md) (repo rules) and the [Kingdoms vibe workflow](https://github.com/merlin-pinpin/kingdoms/blob/main/docs/VIBEWORKFLOW.md)
  (how the project is built).
- External contributors must sign the CLA (see "CLA process" below).

## How to contribute

1. Fork the repo (external) or create a branch `vibe/<short-slug>` (internal)
2. Create or pick a GitHub issue describing the change
   - Issues must use a template from `.github/ISSUE_TEMPLATE/` — blank issues
     are disabled and non-compliant issues are flagged `invalid`
3. Implement following the repo rules (English code/comments, Makefile tasks)
4. Run `make lint` and `make test` — everything must pass
5. Open a PR:
   - Conventional Commit title: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`
   - Small, focused diff
   - Description linking the issue (`Closes #N`)
6. Wait for review from the maintainer (the developer approves all merges)

## Code style

- Python 3.12, type hints everywhere, ruff + mypy clean
- Daily commands via Makefile
- No secrets in the repo (use `.env.example`)

## CLA process

- First-time contributors must accept the CLA before their PR can be merged
- Comment `/cla` on your PR or follow the CLA workflow
- A CLA check runs on every PR from external contributors

## License

By contributing, you agree that your contributions will be licensed under the
[AGPL-3.0](LICENSE) (see [CLA.md](CLA.md) for the full grant, including the
project's relicensing option).
