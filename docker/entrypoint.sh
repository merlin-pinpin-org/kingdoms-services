#!/usr/bin/env bash
# Entrypoint for the Kingdoms containers (ADR-0020 multi-process).
# KINGDOMS_PROCESS selects the process: "bot" (default, legacy) or "core".
# Extra arguments are forwarded to the process (e.g. --preflight).
set -euo pipefail

export LOG_LEVEL="${LOG_LEVEL:-INFO}"

case "${KINGDOMS_PROCESS:-bot}" in
  bot)
    : "${MONGO_URI:?MONGO_URI is required}"
    : "${REDIS_URI:?REDIS_URI is required}"
    : "${DISCORD_TOKEN:?DISCORD_TOKEN is required}"
    exec python -m kingdoms.discord.bot.main "$@"
    ;;
  core)
    : "${MONGO_URI:?MONGO_URI is required}"
    : "${REDIS_URI:?REDIS_URI is required}"
    exec python -m kingdoms.core_process.server
    ;;
  *)
    echo "KINGDOMS_PROCESS must be 'bot' or 'core' (got: ${KINGDOMS_PROCESS})"
    exit 1
    ;;
esac
