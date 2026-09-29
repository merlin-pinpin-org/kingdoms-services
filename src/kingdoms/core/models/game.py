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


@dataclass(frozen=True, slots=True)
class Slot:
    """One slot of a game match's slotinfo, as exposed by the provider.

    Profile and faction identifiers are provider-side and opaque to the
    core; ``slot_kind`` distinguishes humans, AIs and closed slots.
    """

    slot_index: int
    profile_id: str = ""
    faction_key: str = ""
    team: int = 0
    filled: bool = False
    slot_kind: str = "open"


@dataclass(frozen=True, slots=True)
class MatchDetails:
    """Full match parameters: map, slotinfo and raw game options.

    Everything the provider can expose about a match: the per-slot state
    and the game options as opaque key/value pairs (map size, speed,
    victory condition, ...). The core stores them, never interprets them.
    """

    match_ref: str
    map_name: str = ""
    slots: tuple[Slot, ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    started_at: int = 0
    match_kind: str = "lobby"

    def option(self, key: str) -> str | None:
        """Return one raw game option by key, None when absent."""
        return dict(self.options).get(key)


@dataclass(frozen=True, slots=True)
class GameMap:
    """A map known to the provider for its game (catalog data).

    ``map_key`` is the provider-side identifier; the ladder's own maps
    stay a mod concern, this only feeds the admin-facing catalog.
    """

    map_key: str
    name: str
    map_type: str = ""
    resource_url: str = ""


@dataclass(frozen=True, slots=True)
class MatchEvent:
    """One live match lifecycle event, as pushed by the provider.

    Types: lobby_opened, lobby_closed, game_started, game_ended. Events
    can be received multiple times (idempotent consumers); the AoE2
    lobby-closed grace period is applied by the consumer, not here.
    """

    match_ref: str
    type: str
    occurred_at: int
    profile_ids: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
