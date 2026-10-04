"""games/aoe2 — the AoE2 game module (first game domain implementation).

Declares the game identity; all AoE2-specific decoding lives here
(payload blobs), while provider transport stays in the ext-*
processes. Nothing leaks into core collections beyond the opaque
``game_key`` (reference: ladder spec 6).
"""

GAME_KEY = "aoe2"
