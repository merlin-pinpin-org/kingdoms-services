"""Ladder mod data model: ladders, players, matches, rating history (kingdoms-services#134).

Strictly mod-internal collections (reference §2): no platform ids, no
game ids — only internal identities, ``game_key`` and opaque gateway
strings (``match_ref``, ``filename``, ``faction_key``). Persistent
message ids live in the platform registry, never here.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

LADDERS_COLLECTION = "ladders"
PLAYERS_COLLECTION = "players"
MATCHES_COLLECTION = "matches"
RATING_HISTORY_COLLECTION = "rating_history"
ADMIN_AUDIT_COLLECTION = "admin_audit"

MATCH_STATUS_CREATED = "CREATED"
MATCH_STATUS_READY = "READY"
MATCH_STATUS_STARTED = "STARTED"
MATCH_STATUS_LOBBY_OPEN = "LOBBY_OPEN"
MATCH_STATUS_LOBBY_CLOSED = "LOBBY_CLOSED"
MATCH_STATUS_GAME_LIVE = "GAME_LIVE"
MATCH_STATUS_GAME_ENDED = "GAME_ENDED"
MATCH_STATUS_RESULT_PENDING = "RESULT_PENDING"
MATCH_STATUS_REPORTED = "REPORTED"
MATCH_STATUS_COMPLETED = "COMPLETED"
MATCH_STATUS_CANCELED = "CANCELED"

ORIGIN_MATCHMAKING = "matchmaking"
ORIGIN_INVITE = "invite"

RATING_REASON_MATCH_RESULT = "MATCH_RESULT"
RATING_REASON_MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
RATING_REASON_RESET = "RESET"

LIVE_MATCH_STATUSES = frozenset(
    {
        MATCH_STATUS_CREATED,
        MATCH_STATUS_READY,
        MATCH_STATUS_STARTED,
        MATCH_STATUS_LOBBY_OPEN,
        MATCH_STATUS_LOBBY_CLOSED,
        MATCH_STATUS_GAME_LIVE,
        MATCH_STATUS_GAME_ENDED,
        MATCH_STATUS_RESULT_PENDING,
        MATCH_STATUS_REPORTED,
    }
)


class LadderSettingsModel(BaseModel):
    """Admin-tunable settings of one ladder (reference §2 defaults)."""

    model_config = ConfigDict(strict=True)

    base_elo_threshold: int = 60
    elo_threshold_increment: int = 20
    increment_interval: int = 15
    elo_threshold_max: int = 400
    matchmaking_tick_interval: int = 5
    instant_match_on_join: bool = True
    ready_timeout: int = 180
    rating_system: str = "elo"
    elo_initial: int = 1000
    elo_floor: int = 800
    elo_k_provision_match_count: int = 10
    elo_k_newbie: int = 60
    elo_k_standard: int = 32
    elo_max_gain: int = 40
    elo_max_loss: int = 40
    player_fav_count: int = 3
    player_ban_count: int = 2
    pick_strategy: str = "weighted"
    random_ban_count: int = 2
    match_surface_cleanup_delay: int = 300
    auto_confirm_system_report: bool = True


class LadderModel(BaseModel):
    """One ladder configuration per community (owner_ref is opaque)."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    owner_ref: str
    name: str
    game_key: str
    settings: LadderSettingsModel = Field(default_factory=LadderSettingsModel)
    active_map_pool_id: str | None = None
    started_at: int | None = None
    ended_at: int | None = None
    enrollments_open: bool = True
    queue_paused: bool = False

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document (``_id`` is the document key)."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> Self:
        """Build from a MongoDB document."""
        return cls.model_validate(data)


class PlayerModel(BaseModel):
    """One ladder player: caches derived from matches and rating history."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    ladder_id: str
    user_id: str
    display_name: str
    rating: int = 1000
    rating_max: int = 1000
    matches_count: int = 0
    wins: int = 0
    losses: int = 0
    streak: int = 0
    rank: int | None = None
    fav_map_ids: tuple[str, ...] = ()
    ban_map_ids: tuple[str, ...] = ()
    queued_at: int | None = None
    registered_at: int | None = None
    rating_state: dict[str, float] = Field(default_factory=dict)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> Self:
        """Build from a MongoDB document."""
        return cls.model_validate(data)


class MatchSideModel(BaseModel):
    """One side of a 1v1 match."""

    model_config = ConfigDict(strict=True)

    user_id: str
    ready_at: int | None = None
    fav_map_ids: tuple[str, ...] = ()
    ban_map_ids: tuple[str, ...] = ()


class MatchGameModel(BaseModel):
    """Gateway-filled game block — opaque for the core."""

    model_config = ConfigDict(strict=True)

    match_ref: str | None = None
    started_at: int | None = None
    ended_at: int | None = None
    duration: int | None = None
    map_name: str | None = None
    participants: tuple[dict[str, str], ...] = ()


class RatingAppliedSideModel(BaseModel):
    """Rating outcome for one side, snapshotted at application time."""

    model_config = ConfigDict(strict=True)

    before: float
    after: float
    delta: float
    k: float


class MatchModel(BaseModel):
    """One 1v1 match, driven by the reference §5 state machine."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    ladder_id: str
    status: str = MATCH_STATUS_CREATED
    created_at: int
    ready_deadline_at: int | None = None
    host: MatchSideModel
    guest: MatchSideModel
    origin: str = ORIGIN_MATCHMAKING
    map_id: str | None = None
    map_snapshot: dict[str, str] = Field(default_factory=dict)
    started_at: int | None = None
    ready_completed_at: int | None = None
    game: MatchGameModel = Field(default_factory=MatchGameModel)
    winner_user_id: str | None = None
    loser_user_id: str | None = None
    reporter_user_id: str | None = None
    reported_at: int | None = None
    confirm_user_id: str | None = None
    completed_at: int | None = None
    rating_applied: dict[str, RatingAppliedSideModel] | None = None
    cancel_reason: str | None = None
    canceled_by: str | None = None
    canceled_at: int | None = None
    invalid_report_attempts: int = 0

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> Self:
        """Build from a MongoDB document."""
        return cls.model_validate(data)

    def side_of(self, user_id: str) -> MatchSideModel | None:
        """Return the side played by a user; None when not a participant."""
        if self.host.user_id == user_id:
            return self.host
        if self.guest.user_id == user_id:
            return self.guest
        return None

    def other_side(self, user_id: str) -> MatchSideModel | None:
        """Return the opponent side of a user; None when not a participant."""
        if self.host.user_id == user_id:
            return self.guest
        if self.guest.user_id == user_id:
            return self.host
        return None


class RatingHistoryModel(BaseModel):
    """One rating-history line — the source of truth for ratings (§3)."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    ladder_id: str
    match_id: str
    user_id: str
    rating_before: float
    rating_after: float
    delta: float
    k_used: float
    reason: str
    admin_user_id: str | None = None
    created_at: int

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> Self:
        """Build from a MongoDB document."""
        return cls.model_validate(data)
