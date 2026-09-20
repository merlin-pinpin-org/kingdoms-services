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
