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
COPY --from=deps /app/.venv/bin/uv /usr/local/bin/uv || true
COPY src/ ./src/
COPY config/ ./config/
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
USER 65534:65534
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["curl", "-fsS", "http://localhost:8000/healthz"] || exit 1
ENTRYPOINT ["./entrypoint.sh"]
