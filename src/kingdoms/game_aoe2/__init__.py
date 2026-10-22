"""game_aoe2 — the AoE2 game data service process.

Direction développeur (architecture cible) : one game service per game,
the ONLY consumer of the external providers for that game. It owns the
AoE2 domain tables (players, matches, stats), exploits the raw provider
data, and serves the consolidated, enriched state over
``kingdoms.v1.GameData``. svc-core never talks to the providers, and
vice versa.

This process consumes:
- ext-aoe2lobby (realtime lobby/game states, match summaries, links)
- ext-librematch (stats, results — the source of truth)

and serves the bot's live dashboard and the generic player-events
mod feed, with the full match summary (map, civs, format, links).
"""

from __future__ import annotations

GAME_KEY = "aoe2"
PROCESS_LABEL = "game-aoe2"
