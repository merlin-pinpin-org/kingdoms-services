# Multi-stage build: deps -> build -> final runtime image.

FROM python:3.12-slim AS deps
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim AS build
WORKDIR /app
COPY --from=deps /app/.venv ./.venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN VIRTUAL_ENV=/app/.venv uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=build /app/.venv ./.venv
# pyproject.toml is the repo-root MARKER the mod loader resolves
# (botforge.core.services.mod_layout.default_repo_root) — without it
# the modular mods are invisible in the container
COPY pyproject.toml ./pyproject.toml
COPY src/ ./src/
COPY config/ ./config/
COPY data/ ./data/
# the modular mods (modules/mods/<mod>/) are discovered through the
# repo-root marker above — without them the mod catalog is empty and
# no mod ever loads in the container
COPY modules/ ./modules/
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
USER 65534:65534
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["curl", "-fsS", "http://localhost:8000/healthz"] || exit 1
ENTRYPOINT ["./entrypoint.sh"]
