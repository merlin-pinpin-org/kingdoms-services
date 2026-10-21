"""ext-aoe2lobby — AoE2 realtime provider process (aoe2lobby.com).

Serves the kingdoms.v1.Game contract to svc-core. Backed by the
aoe2lobby.com WebSocket API (``wss://data.aoe2lobby.com/ws/``), which
aggregates Worlds Edge Link lobby data and pushes global snapshots and
deltas; the adapter reduces them client-side into per-player lifecycle
events (lobby_opened / game_started / lobby_closed / game_ended) with
the 15 s lobby-closed grace period. Results still require the
LibreMatch-side data, so reliable_results stays conservative.
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
