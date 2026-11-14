"""The kingdom mod's package (§7): the season-state core.

P1 of the reimplementation (#368): the models, the game clock, the
territories, the civ crests, the config, the storage seam and the
service — ported from the reference branch (vibe/drasah/main, PR
#244), adapted to the platform seams. No Discord imports here (the
cog is the mod's only Discord layer — R6); the collections ride the
ModStorage facade (R8).
"""
