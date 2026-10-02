.PHONY: dev dev-up dev-down dev-logs test lint format typecheck build clean setup docs docs-check check battery contracts contracts-check

# Dev: compose profiles (ADR-0020) — 'all' = bot + core + providers + data.
# Narrower: docker compose --profile core up -d (no Discord token needed).
dev:
	@docker compose --profile all down && docker compose --profile all up --build

dev-up:
	@docker compose --profile all up -d --build

dev-down:
	@docker compose --profile all down

dev-logs:
	@docker compose --profile all logs -f

# Tests & Quality
test:
	@uv run pytest

# battery: the standalone behavioral journey suite (kingdoms-services#106).
# One command from a clean checkout at any commit, in-memory and
# network-free — the kingdoms-infra post-deploy battery (kingdoms-infra#78)
# runs it on a GitHub-hosted runner at the pinned deploy commit. Test
# tooling never runs on an environment VPS.
battery:
	@uv sync --frozen
	@uv run pytest tests/integration

lint:
	@uv run ruff check src/ tests/

format:
	@uv run ruff format src/ tests/

typecheck:
	@uv run mypy src/

# Fail-closed CI mirror: the workflow-lint checks (actionlint + the
# pin-payload cap guard) run locally too — run this before pushing
# workflow changes.
check:
	@bash -n scripts/*.sh
	@command -v actionlint >/dev/null \
	  && actionlint -color \
	  || echo "check: actionlint not installed — CI runs it; install locally for full coverage"
	@./scripts/check_pin_payloads.sh
	@./scripts/check_image_purity.py --source-only

# Docs (generated into this repo, fail-closed freshness check in CI)
docs:
	@uv run python scripts/generate_pydoc.py --source src/kingdoms --output docs/DEVELOPMENT/pydoc

docs-check:
	@uv run python scripts/generate_pydoc.py --source src/kingdoms --output /tmp/pydoc-fresh
	@diff -rq /tmp/pydoc-fresh docs/DEVELOPMENT/pydoc --exclude=.gitkeep \
	  && echo "Generated docs are fresh" \
	  || (echo "ERROR: docs/DEVELOPMENT/pydoc is stale — run 'make docs' and commit"; exit 1)

# Setup
setup:
	@uv sync

# Build
build:
	@uv build

# Clean
clean:
	@docker compose down -v
	@docker system prune -f

# Release & deploy watching (agent automation)
# release: cut the next version — computed by the conventional changelog
# (feat → minor, fix → patch, BREAKING → major); the tag push triggers the
# Docker workflow (image + pin on deploy/test + GitHub release).
release:
	@./scripts/cut_release.sh

# pre-release: cut the next version with a -rc<n> classifier (deployable on
# test only, never promotable to prod — the Promote workflow refuses -rc).
pre-release:
	@./scripts/cut_release.sh --prerelease

# watch-deploy: follow a deploy/<env> pin and its deploy run (ENV, LABEL)
watch-deploy:
	@./scripts/watch_deploy.sh $(ENV) $(LABEL)

# changelog: regenerate the conventional CHANGELOG.md section for TAG
changelog:
	@uv run python scripts/generate_changelog.py --tag $(TAG) --changelog CHANGELOG.md

# release-notes: print the conventional release notes body for TAG
release-notes:
	@uv run python scripts/generate_changelog.py --tag $(TAG) --notes

# contracts-check: fail closed when committed stubs drift from contracts/
# (wire-compatibility guard, kingdoms-services#128). CI runs it too.
contracts-check:
	@uv run python scripts/check_contracts_fresh.py

# contracts: regenerate the gRPC stubs from contracts/ (ADR-0020).
contracts:
	@uv run python -m grpc_tools.protoc \
		--python_out=src/kingdoms/rpc_generated \
		--grpc_python_out=src/kingdoms/rpc_generated \
		--proto_path=contracts \
		contracts/kingdoms/v1/*.proto
	@uv run python -c "import pathlib; [p.write_text(p.read_text().replace('from kingdoms.v1 import ', 'from kingdoms.rpc_generated.kingdoms.v1 import ')) for p in pathlib.Path('src/kingdoms/rpc_generated/kingdoms/v1').glob('*_pb2_grpc.py')]"
