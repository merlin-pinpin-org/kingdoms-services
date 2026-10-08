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
  ext-librematch)
    exec python -m kingdoms.ext_librematch.server
    ;;
  ext-aoe2lobby)
    exec python -m kingdoms.ext_aoe2lobby.server
    ;;
  ext-aoe2techtree)
    exec python -m kingdoms.ext_aoe2techtree.server
    ;;
  seed)
    : "${MONGO_URI:?MONGO_URI is required}"
    exec python -m kingdoms.core.games.aoe2.seed_cli
    ;;
  *)
    echo "KINGDOMS_PROCESS must be 'bot', 'core', 'ext-librematch', 'ext-aoe2lobby', 'ext-aoe2techtree' or 'seed' (got: ${KINGDOMS_PROCESS})"
    exit 1
    ;;
esac
