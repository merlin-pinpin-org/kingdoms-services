"""ext-librematch — AoE2 data provider process (Worlds Edge Link APIs).

Serves the kingdoms.v1.Game contract to svc-core. Backed by the
community-documented Worlds Edge Link APIs (wiki.librematch.org):
the public Community API for lobby listings and the authenticated
Game API for match data. Poll-only (no WebSocket), so the declared
capabilities are conservative until the live adapter is wired.
"""

from __future__ import annotations

from kingdoms.core.models.game import ProviderCapabilities

PROVIDER_ID = "ext-librematch"

DECLARED_CAPABILITIES = ProviderCapabilities(
    provider_id=PROVIDER_ID,
    game_key="aoe2",
    realtime=False,          # official API is poll-only; no push channel
    reliable_results=False,  # permissive until live validation (spike)
    check_map=True,          # map metadata available via mods endpoints
    player_stats=True,       # leaderboard/profile stats available
)
