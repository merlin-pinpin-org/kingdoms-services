.PHONY: dev dev-up dev-down dev-logs test lint format typecheck build clean setup

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

# Docs (generated into this repo, fail-closed freshness check in CI)
docs:
	@python3 scripts/generate_pydoc.py --source src/kingdoms --output docs/DEVELOPMENT/pydoc

docs-check:
	@python3 scripts/generate_pydoc.py --source src/kingdoms --output /tmp/pydoc-fresh
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
