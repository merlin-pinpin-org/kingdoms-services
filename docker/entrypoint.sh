#!/usr/bin/env bash
# Entrypoint for the kingdoms bot container.
# Validates the environment, then starts the bot.

set -euo pipefail

: "${MONGO_URI:?MONGO_URI is required}"
: "${REDIS_URI:?REDIS_URI is required}"
: "${DISCORD_TOKEN:?DISCORD_TOKEN is required}"

export LOG_LEVEL="${LOG_LEVEL:-INFO}"

exec python -m kingdoms.discord.bot.main
