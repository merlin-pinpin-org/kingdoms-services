"""ext-aoe2lobby — AoE2 realtime provider process (aoe2lobby.com).

Serves the kingdoms.v1.Game contract to svc-core. Backed by the
aoe2lobby.com WebSocket API, which aggregates Worlds Edge Link lobby
data and provides real-time updates without polling. Results still
require the LibreMatch-side data, so reliable_results stays
conservative until the live adapter is wired.
"""

from __future__ import annotations

from kingdoms.core.models.game import ProviderCapabilities

PROVIDER_ID = "ext-aoe2lobby"

DECLARED_CAPABILITIES = ProviderCapabilities(
    provider_id=PROVIDER_ID,
    game_key="aoe2",
    realtime=True,  # aoe2lobby.com WebSocket (real-time lobby data)
    reliable_results=False,  # permissive until live validation (spike)
    check_map=False,
    player_stats=False,
)
