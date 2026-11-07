"""kingdoms.aoe2 — the AoE2 game's shared formats (owner direction 2026-11-05).

The slotinfo/options blob decoding and the lobby-closed grace are
AoE2's OWN raw formats and rules — shared by the game's providers
(ext-librematch, ext-aoe2lobby) and the game service alike, but they
are game data, not platform plumbing: they live HERE, never in core.
"""

from __future__ import annotations

GAME_KEY = "aoe2"
