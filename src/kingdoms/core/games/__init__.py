"""games/aoe2 — the AoE2 game module (first game domain implementation).

The game module owns nothing provider-specific: it declares the game
identity and consumes providers via the kingdoms.v1.Game contract. All
AoE2-specific data flows through the provider processes
(ext-librematch, ext-aoe2lobby); nothing leaks into core collections
beyond the opaque ``game_key`` (reference: ladder spec 6).
"""

GAME_KEY = "aoe2"
