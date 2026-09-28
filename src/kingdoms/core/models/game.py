"""Game domain contracts: capabilities and provider-facing seams.

The core consumes game data exclusively through a provider process
serving the kingdoms.v1.Game contract (ADR-0020). Capabilities are
declared, never assumed: the ladder degrades cleanly on anything absent
(reference: ladder spec 1.2, 9.1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What a game provider actually supports, as declared by the provider.

    The core never guesses these: an absent capability is a first-class
    state the ladder must handle (degraded path, reference 9.1).
    """

    provider_id: str
    game_key: str
    realtime: bool = False
    reliable_results: bool = False
    check_map: bool = False
    player_stats: bool = False

    @classmethod
    def none(cls, provider_id: str, game_key: str) -> ProviderCapabilities:
        """Build the zero-capability declaration (acceptance baseline 9.1)."""
        return cls(provider_id=provider_id, game_key=game_key)
