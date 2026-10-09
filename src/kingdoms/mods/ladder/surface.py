"""Ladder platform surface: abstract actions, view models, intent rendering (kingdoms-services#135).

This is the mod's surface layer: it maps the domain service's actions to
**abstract actions** (join_queue, ready, report, ...) with preconditions
(reference §1.1/§7.3) and renders the §7.2 intent catalogue through the
platform delivery seam. No Discord ids ever cross this boundary: view
models carry only mod identities; persistent messages go through the
platform message registry (#130).

Discord-specific rendering (embeds, buttons) lives in bot-discord; this
module is the game- and platform-agnostic surface contract the Discord
adapter implements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from kingdoms.mods.ladder.models import (
    LIVE_MATCH_STATUSES,
    MATCH_STATUS_COMPLETED,
    LadderModel,
    MatchModel,
    PlayerModel,
)
from kingdoms.mods.ladder.service import LadderService

MOD_KEY = "ladder"

ACTION_JOIN_QUEUE = "join_queue"
ACTION_LEAVE_QUEUE = "leave_queue"
ACTION_READY = "ready"
ACTION_REPORT_RESULT = "report_result"
ACTION_CONFIRM_RESULT = "confirm_result"
ACTION_REJECT_RESULT = "reject_result"
ACTION_CANCEL_MATCH = "cancel_match"
ACTION_INVITE_PLAYER = "invite_player"
ACTION_PREFERENCES = "preferences"
ACTION_LEADERBOARD_OPEN = "leaderboard_open"
ACTION_PLAY_OPEN = "play_open"
ACTION_ADMIN_OPEN = "admin_open"


class SurfaceDelivery(Protocol):
    """Platform delivery seam: render intents on named surfaces (§1.1)."""

    async def deliver(self, surface: str, intent: str, payload: dict[str, Any]) -> None:
        """Render one intent on a named surface (play/leaderboard/matches/...)."""
        ...


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of one abstract action: what to render, or why refused."""

    ok: bool
    action: str
    reason: str | None = None
    data: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class QueueRow:
    """One row of the QueueView (§7.3): name, rating, wait, threshold."""

    user_id: str
    display_name: str
    rating: int
    wait_seconds: int
    threshold: int


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    """One row of the LeaderboardView (§7.3)."""

    rank: int
    user_id: str
    display_name: str
    rating: int
    rating_max: int
    matches_count: int
    wins: int
    losses: int
    streak: int


@dataclass(frozen=True, slots=True)
class MatchTimelineEntry:
    """One timeline entry of the MatchView (§7.3)."""

    label: str
    at: int | None
    actor: str | None = None


class LadderSurface:
    """Abstract actions over the domain service + view model builders."""

    def __init__(self, service: LadderService, delivery: SurfaceDelivery | None = None) -> None:
        """Wire the domain service and the platform delivery seam."""
        self._svc = service
        self._delivery = delivery

    # ── Abstract actions (§1.1) ──────────────────────────────────────────

    async def execute(
        self, action: str, ladder_id: str, user_id: str, now: int, has_game_profile: bool = True, **kwargs: Any
    ) -> ActionResult:
        """Run one abstract action with its preconditions (§9.8: admin-gated)."""
        handlers: dict[str, Any] = {
            ACTION_JOIN_QUEUE: self._join_queue,
            ACTION_LEAVE_QUEUE: self._leave_queue,
            ACTION_READY: self._ready,
            ACTION_REPORT_RESULT: self._report_result,
            ACTION_CONFIRM_RESULT: self._confirm_result,
            ACTION_CANCEL_MATCH: self._cancel_match,
            ACTION_INVITE_PLAYER: self._invite,
        }
        handler: Any = handlers.get(action)
        if handler is None:
            return ActionResult(ok=False, action=action, reason="unknown_action")
        result: ActionResult = await handler(ladder_id, user_id, now, has_game_profile, kwargs)
        return result

    async def _join_queue(
        self, ladder_id: str, user_id: str, now: int, has_game_profile: bool, kwargs: dict[str, Any]
    ) -> ActionResult:
        """Join the queue after the §5.1 preconditions."""
        try:
            player = await self._svc.join_queue(ladder_id, user_id, now, has_game_profile)
        except Exception as exc:
            return ActionResult(ok=False, action=ACTION_JOIN_QUEUE, reason=str(exc))
        return ActionResult(ok=True, action=ACTION_JOIN_QUEUE, data={"queued_at": player.queued_at})

    async def _leave_queue(
        self, ladder_id: str, user_id: str, now: int, has_game_profile: bool, kwargs: dict[str, Any]
    ) -> ActionResult:
        """Leave the queue (refused with a CREATED match pending)."""
        del now, has_game_profile
        try:
            await self._svc.leave_queue(ladder_id, user_id)
        except Exception as exc:
            return ActionResult(ok=False, action=ACTION_LEAVE_QUEUE, reason=str(exc))
        return ActionResult(ok=True, action=ACTION_LEAVE_QUEUE)

    async def _ready(
        self, ladder_id: str, user_id: str, now: int, has_game_profile: bool, kwargs: dict[str, Any]
    ) -> ActionResult:
        """Confirm ready on the user's live match."""
        del has_game_profile
        match_id = str(kwargs.get("match_id", ""))
        try:
            match = await self._svc.mark_ready(match_id, user_id, now)
        except Exception as exc:
            return ActionResult(ok=False, action=ACTION_READY, reason=str(exc))
        return ActionResult(ok=True, action=ACTION_READY, data={"status": match.status})

    async def _report_result(
        self, ladder_id: str, user_id: str, now: int, has_game_profile: bool, kwargs: dict[str, Any]
    ) -> ActionResult:
        """Report the winner on the user's live match."""
        del has_game_profile
        match_id = str(kwargs.get("match_id", ""))
        winner = kwargs.get("winner_user_id")
        if not match_id or not winner:
            return ActionResult(ok=False, action=ACTION_REPORT_RESULT, reason="missing_match_or_winner")
        try:
            match = await self._svc.report_result(match_id, user_id, str(winner), now)
        except Exception as exc:
            return ActionResult(ok=False, action=ACTION_REPORT_RESULT, reason=str(exc))
        return ActionResult(ok=True, action=ACTION_REPORT_RESULT, data={"status": match.status})

    async def _confirm_result(
        self, ladder_id: str, user_id: str, now: int, has_game_profile: bool, kwargs: dict[str, Any]
    ) -> ActionResult:
        """Confirm the reported result of the user's live match."""
        del has_game_profile
        match_id = str(kwargs.get("match_id", ""))
        try:
            match = await self._svc.confirm_result(match_id, user_id, now)
        except Exception as exc:
            return ActionResult(ok=False, action=ACTION_CONFIRM_RESULT, reason=str(exc))
        return ActionResult(ok=True, action=ACTION_CONFIRM_RESULT, data={"status": match.status})

    async def _cancel_match(
        self, ladder_id: str, user_id: str, now: int, has_game_profile: bool, kwargs: dict[str, Any]
    ) -> ActionResult:
        """Cancel the user's live match (reason mandatory)."""
        del has_game_profile
        match_id = str(kwargs.get("match_id", ""))
        reason = str(kwargs.get("reason", ""))
        if not reason:
            return ActionResult(ok=False, action=ACTION_CANCEL_MATCH, reason="reason_mandatory")
        try:
            await self._svc.cancel_match(match_id, user_id, reason, now)
        except Exception as exc:
            return ActionResult(ok=False, action=ACTION_CANCEL_MATCH, reason=str(exc))
        return ActionResult(ok=True, action=ACTION_CANCEL_MATCH)

    async def _invite(
        self, ladder_id: str, user_id: str, now: int, has_game_profile: bool, kwargs: dict[str, Any]
    ) -> ActionResult:
        """Create a direct-invite match with a registered opponent."""
        del has_game_profile
        guest = kwargs.get("guest_user_id")
        if not guest:
            return ActionResult(ok=False, action=ACTION_INVITE_PLAYER, reason="missing_guest")
        try:
            match = await self._svc.create_invite_match(ladder_id, user_id, str(guest), now)
        except Exception as exc:
            return ActionResult(ok=False, action=ACTION_INVITE_PLAYER, reason=str(exc))
        return ActionResult(ok=True, action=ACTION_INVITE_PLAYER, data={"match_id": match.id})

    # ── View models (§7.3) ────────────────────────────────────────────────

    async def queue_view(self, ladder_id: str, now: int, page: int = 0, page_size: int = 10) -> list[QueueRow]:
        """Build the queue page rows (wait time + current threshold each)."""
        from kingdoms.mods.ladder.matchmaking import QueueEntry, threshold

        ladder = await self._svc.get_ladder(ladder_id)
        if ladder is None:
            return []
        docs = await self._svc._db.find_queued_players(ladder_id)
        rows = [
            QueueRow(
                user_id=d["user_id"],
                display_name=d["display_name"],
                rating=int(d["rating"]),
                wait_seconds=max(0, (now - int(d["queued_at"])) // 1000),
                threshold=threshold(
                    QueueEntry(d["user_id"], float(d["rating"]), int(d["queued_at"])),
                    ladder.settings,
                    now,
                ),
            )
            for d in docs
        ]
        rows.sort(key=lambda r: r.user_id)
        start = page * page_size
        return rows[start : start + page_size]

    async def leaderboard_view(self, ladder_id: str, page: int = 0, page_size: int = 10) -> list[LeaderboardRow]:
        """Build the leaderboard page rows (rank, rating, W/L, streak)."""
        players = await self._svc.leaderboard(ladder_id, limit=1000)
        rows = [
            LeaderboardRow(
                rank=i + 1,
                user_id=p.user_id,
                display_name=p.display_name,
                rating=p.rating,
                rating_max=p.rating_max,
                matches_count=p.matches_count,
                wins=p.wins,
                losses=p.losses,
                streak=p.streak,
            )
            for i, p in enumerate(players)
        ]
        start = page * page_size
        return rows[start : start + page_size]

    async def match_view(self, match_id: str) -> MatchModel | None:
        """Return the match for the MatchView surface."""
        return await self._svc.get_match(match_id)

    async def player_view(self, ladder_id: str, user_id: str) -> PlayerModel | None:
        """Return the player for the PlayerView surface."""
        return await self._svc.get_player(ladder_id, user_id)

    async def play_banner(self, ladder_id: str) -> LadderModel | None:
        """Return the ladder for the PlayView banner."""
        return await self._svc.get_ladder(ladder_id)

    @staticmethod
    def match_timeline(match: MatchModel) -> list[MatchTimelineEntry]:
        """Build the MatchView timeline (created, ready, started, result...)."""
        entries = [MatchTimelineEntry("created", match.created_at)]
        if match.ready_completed_at is not None:
            entries.append(MatchTimelineEntry("ready", match.ready_completed_at))
        if match.started_at is not None:
            entries.append(MatchTimelineEntry("started", match.started_at))
        if match.game.match_ref is not None:
            entries.append(MatchTimelineEntry("lobby", match.game.started_at))
        if match.game.ended_at is not None:
            entries.append(MatchTimelineEntry("game_ended", match.game.ended_at))
        if match.reported_at is not None:
            entries.append(MatchTimelineEntry("reported", match.reported_at, match.reporter_user_id))
        if match.completed_at is not None:
            entries.append(MatchTimelineEntry("completed", match.completed_at, match.confirm_user_id))
        if match.canceled_at is not None:
            entries.append(MatchTimelineEntry("canceled", match.canceled_at, match.canceled_by))
        return entries

    @staticmethod
    def available_actions(match: MatchModel, user_id: str) -> list[str]:
        """Contextual action bar of the MatchView (§7.3)."""
        if match.side_of(user_id) is None:
            return []
        actions: list[str] = []
        if match.status in LIVE_MATCH_STATUSES and match.status != MATCH_STATUS_COMPLETED:
            actions.append(ACTION_READY)
            actions.append(ACTION_CANCEL_MATCH)
            actions.append(ACTION_REPORT_RESULT)
            actions.append(ACTION_CONFIRM_RESULT)
        return actions
