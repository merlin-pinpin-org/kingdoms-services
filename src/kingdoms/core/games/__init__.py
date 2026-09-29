"""Game modules — one package per supported game.

A game module declares the game identity and game-specific decoding
helpers; provider-specific logic stays in the ext-<provider> processes
(ADR-0020). Adding a game = a new package here + a provider process.
"""
