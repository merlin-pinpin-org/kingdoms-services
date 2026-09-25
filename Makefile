.PHONY: dev dev-up dev-down dev-logs test lint format typecheck build clean setup docs docs-check check

# Dev
dev:
	@docker compose down && docker compose up --build

dev-up:
	@docker compose up -d --build

dev-down:
	@docker compose down

dev-logs:
	@docker compose logs -f

# Tests & Quality
test:
	@uv run pytest

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
# release: cut vX.Y.Z (tag + GitHub release; pre-flight: main clean, green)
release:
	@./scripts/release.sh $(TAG)

# watch-deploy: follow a deploy/<env> pin and its deploy run (ENV, LABEL)
watch-deploy:
	@./scripts/watch_deploy.sh $(ENV) $(LABEL)
