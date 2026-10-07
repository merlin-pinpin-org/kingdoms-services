"""Ladder domain core: queue, matches, state machine, map pick, rating (kingdoms-services#134).

Reference §2-§6, §9. The core knows neither Discord nor AoE2: it stores
only internal identities, ``game_key`` and opaque gateway strings; game
capabilities come from the injected GameGateway seam, and a zero-capability
gateway drives the fully degraded path (§9.1) without any special-casing.

Key invariants:
- idempotent transitions: replayed external events have no side effect;
- rating applied exactly once per match (``rating_applied`` guard);
- ``rating_history`` is the source of truth, player fields are caches;
- map pick snapshots (name, filename) on the match, never re-resolved;
- cancelled-before-confirmation matches never touch ratings.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Protocol

from kingdoms.mods.ladder.match_data import MatchDataService
from kingdoms.mods.ladder.matchmaking import Pairing, QueueEntry, matchmaking_pass
from kingdoms.mods.ladder.models import (
    LADDERS_COLLECTION,
    MATCH_STATUS_CANCELED,
    MATCH_STATUS_COMPLETED,
    MATCH_STATUS_CREATED,
    MATCH_STATUS_GAME_ENDED,
    MATCH_STATUS_GAME_LIVE,
    MATCH_STATUS_LOBBY_CLOSED,
    MATCH_STATUS_LOBBY_OPEN,
    MATCH_STATUS_REPORTED,
    MATCH_STATUS_RESULT_PENDING,
    MATCH_STATUS_STARTED,
    MATCHES_COLLECTION,
    ORIGIN_INVITE,
    ORIGIN_MATCHMAKING,
    PLAYERS_COLLECTION,
    RATING_HISTORY_COLLECTION,
    RATING_REASON_MATCH_RESULT,
    LadderModel,
    LadderSettingsModel,
    MatchModel,
    MatchSideModel,
    PlayerModel,
    RatingAppliedSideModel,
    RatingHistoryModel,
)
from kingdoms.mods.ladder.rating import EloRatingSystem, Glicko2RatingSystem, RatingSystem

logger = logging.getLogger("kingdoms.mods.ladder")

RATING_SYSTEMS: dict[str, RatingSystem] = {
    EloRatingSystem().key: EloRatingSystem(),
    Glicko2RatingSystem().key: Glicko2RatingSystem(),
}


class LadderDatabase(Protocol):
    """Narrow async MongoDB seam the LadderService depends on."""

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        """Insert or replace one document by ``_id``."""
        ...

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        """Return one document by ``_id``; None when absent."""
        ...

    async def find_ladder_by_owner(self, owner_ref: str, game_key: str) -> dict[str, Any] | None:
        """Return the ladder of one owner for a game; None when absent."""
        ...

    async def find_player(self, ladder_id: str, user_id: str) -> dict[str, Any] | None:
        """Return one player document; None when absent."""
        ...

    async def find_queued_players(self, ladder_id: str) -> list[dict[str, Any]]:
        """List the players currently queued on a ladder."""
        ...

    async def find_ladder_players(self, ladder_id: str) -> list[dict[str, Any]]:
        """List every registered player of a ladder."""
        ...

    async def find_active_match(self, ladder_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the user's live match on the ladder; None when free."""
        ...

    async def find_ladder_matches(self, ladder_id: str, statuses: list[str]) -> list[dict[str, Any]]:
        """List the ladder's matches in any of the given statuses."""
        ...

    async def find_rating_history(self, ladder_id: str, user_id: str) -> list[dict[str, Any]]:
        """List a player's rating-history lines (ascending)."""
        ...

    async def delete_entry(self, collection: str, entry_id: str) -> bool:
        """Delete one document by ``_id``; True when one was removed."""
        ...


class GameGateway(Protocol):
    """Capabilities the game adapter declares to the ladder core (§1.2)."""

    @property
    def realtime(self) -> bool:
        """Whether the game emits realtime lifecycle events."""
        ...

    @property
    def reliable_results(self) -> bool:
        """Whether gateway results can be auto-confirmed."""
        ...

    @property
    def check_map(self) -> bool:
        """Whether lobby maps can be checked for conformity."""
        ...


class NullGameGateway:
    """Zero-capability gateway: the fully degraded path (§9.1)."""

    @property
    def realtime(self) -> bool:
        """No realtime events."""
        return False

    @property
    def reliable_results(self) -> bool:
        """No auto results: manual report always."""
        return False

    @property
    def check_map(self) -> bool:
        """No map checking."""
        return False


class LadderEvents(Protocol):
    """Notification-intent seam (§7.2) — rendered by the platform adapter."""

    async def emit(self, intent: str, payload: dict[str, Any]) -> None:
        """Emit one notification intent."""
        ...


class LadderAudit(Protocol):
    """Admin-audit seam: every admin mutation is recorded."""

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        """Persist one admin-audit line."""
        ...


class LadderError(ValueError):
    """Base ladder domain error."""


class NotRegisteredError(LadderError):
    """The user is not registered on this ladder."""


class QueueStateError(LadderError):
    """The queue operation conflicts with the player's state."""


class ActiveMatchError(LadderError):
    """The player already has a live match."""


class TransitionError(LadderError):
    """The transition is invalid for the match's current status."""


class NotParticipantError(LadderError):
    """The user is not a participant of this match."""


class MapPoolError(LadderError):
    """The ladder has no active pool to pick from."""


class LadderService:
    """Ladder domain core: registration, queue, matches, rating."""

    def __init__(
        self,
        database: LadderDatabase,
        game_data: Any,
        gateway: GameGateway | None = None,
        events: LadderEvents | None = None,
        audit: LadderAudit | None = None,
        rng: random.Random | None = None,
        match_data: MatchDataService | None = None,
    ) -> None:
        """Wire persistence, game-data (pools), gateway, events and audit seams."""
        self._db = database
        self._game_data = game_data
        self._gateway = gateway or NullGameGateway()
        self._events = events
        self._audit = audit
        self._rng = rng or random.Random()  # noqa: S311 - game map pick, not crypto
        self._match_data = match_data

    # ── Ladders ──────────────────────────────────────────────────────────

    async def create_ladder(
        self, owner_ref: str, name: str, game_key: str, settings: LadderSettingsModel | None = None, now: int = 0
    ) -> LadderModel:
        """Create the ladder of a community for a game (one per owner+game)."""
        existing = await self._db.find_ladder_by_owner(owner_ref, game_key)
        if existing is not None:
            raise LadderError(f"ladder already exists for owner {owner_ref!r} and game {game_key!r}")
        ladder = LadderModel(
            _id=f"ladder:{game_key}:{owner_ref}",
            owner_ref=owner_ref,
            name=name,
            game_key=game_key,
            settings=settings or LadderSettingsModel(),
            started_at=now or None,
        )
        await self._db.upsert_entry(LADDERS_COLLECTION, ladder.to_mongo())
        return ladder

    async def set_enrollments_open(self, ladder_id: str, open_: bool) -> LadderModel:
        """Open or close the enrollments (the admin's season gate)."""
        ladder = await self._require_ladder(ladder_id)
        ladder = ladder.model_copy(update={"enrollments_open": open_})
        await self._db.upsert_entry(LADDERS_COLLECTION, ladder.to_mongo())
        return ladder

    async def set_queue_paused(self, ladder_id: str, paused: bool) -> LadderModel:
        """Pause or resume the queue (an interrupted ladder keeps its data)."""
        ladder = await self._require_ladder(ladder_id)
        ladder = ladder.model_copy(update={"queue_paused": paused})
        await self._db.upsert_entry(LADDERS_COLLECTION, ladder.to_mongo())
        return ladder

    async def set_active_pool(self, ladder_id: str, map_pool_id: str) -> LadderModel:
        """Switch the ladder's active pool (transactional switch, ref §4)."""
        ladder = await self._require_ladder(ladder_id)
        await self._game_data.activate_map_pool(ladder_id, map_pool_id)
        ladder = ladder.model_copy(update={"active_map_pool_id": map_pool_id})
        await self._db.upsert_entry(LADDERS_COLLECTION, ladder.to_mongo())
        await self._audit_record("ladder.pool.activate", {"ladder_id": ladder_id, "map_pool_id": map_pool_id})
        return ladder

    async def get_ladder(self, ladder_id: str) -> LadderModel | None:
        """Return one ladder; None when unknown."""
        doc = await self._db.find_entry(LADDERS_COLLECTION, ladder_id)
        return LadderModel.from_mongo(doc) if doc else None

    # ── Players ──────────────────────────────────────────────────────────

    async def register_player(self, ladder_id: str, user_id: str, display_name: str, now: int = 0) -> PlayerModel:
        """Register a player on the ladder (idempotent per user).

        Refused while the ladder's enrollments are closed (the admin
        opens them per season: the "démarrer les inscriptions" step).
        """
        existing = await self._db.find_player(ladder_id, user_id)
        if existing is not None:
            return PlayerModel.from_mongo(existing)
        ladder = await self._require_ladder(ladder_id)
        if not ladder.enrollments_open:
            raise NotRegisteredError(f"ladder {ladder_id!r} enrollments are closed")
        system = RATING_SYSTEMS[ladder.settings.rating_system]
        player = PlayerModel(
            _id=f"player:{ladder_id}:{user_id}",
            ladder_id=ladder_id,
            user_id=user_id,
            display_name=display_name,
            rating=int(system.initial_rating(ladder.settings)),
            rating_max=int(system.initial_rating(ladder.settings)),
            rating_state=dict(system.initial_state(ladder.settings)),
            registered_at=now,
        )
        await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        return player

    async def remove_player(self, ladder_id: str, user_id: str) -> bool:
        """Remove a registered player; True when one was removed.

        The caller owns the preconditions (leaving the queue first); a
        player with a live match cannot be removed (TransitionError).
        """
        match = await self._db.find_active_match(ladder_id, user_id)
        if match is not None:
            raise ActiveMatchError(f"user {user_id} has a live match on {ladder_id}")
        return await self._db.delete_entry(PLAYERS_COLLECTION, f"player:{ladder_id}:{user_id}")

    async def get_player(self, ladder_id: str, user_id: str) -> PlayerModel | None:
        """Return one player; None when unknown."""
        doc = await self._db.find_player(ladder_id, user_id)
        return PlayerModel.from_mongo(doc) if doc else None

    async def set_preferences(
        self, ladder_id: str, user_id: str, fav_map_ids: tuple[str, ...], ban_map_ids: tuple[str, ...]
    ) -> PlayerModel:
        """Set fav/ban preferences (counts capped, favs and bans disjoint)."""
        ladder = await self._require_ladder(ladder_id)
        player = await self._require_player(ladder_id, user_id)
        favs = tuple(dict.fromkeys(fav_map_ids))[: ladder.settings.player_fav_count]
        bans = tuple(dict.fromkeys(ban_map_ids))[: ladder.settings.player_ban_count]
        overlap = set(favs) & set(bans)
        if overlap:
            raise LadderError(f"favs and bans must be disjoint (overlap: {sorted(overlap)})")
        player = player.model_copy(update={"fav_map_ids": favs, "ban_map_ids": bans})
        await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        return player

    # ── Queue ─────────────────────────────────────────────────────────────

    async def join_queue(self, ladder_id: str, user_id: str, now: int, has_game_profile: bool) -> PlayerModel:
        """Join the queue: registered + resolvable profile + no live match."""
        player = await self._require_player(ladder_id, user_id)
        if not has_game_profile:
            raise QueueStateError(f"user {user_id!r} has no resolvable game profile")
        ladder = await self._require_ladder(ladder_id)
        if ladder.queue_paused:
            raise QueueStateError(f"ladder {ladder_id!r} queue is paused")
        if await self._db.find_active_match(ladder_id, user_id) is not None:
            raise ActiveMatchError(f"user {user_id!r} already has a live match")
        if player.queued_at is not None:
            return player
        player = player.model_copy(update={"queued_at": now})
        await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        await self._emit("queue.joined", {"ladder_id": ladder_id, "user_id": user_id})
        return player

    async def leave_queue(self, ladder_id: str, user_id: str) -> PlayerModel:
        """Leave the queue — forbidden with a CREATED match pending ready."""
        player = await self._require_player(ladder_id, user_id)
        active = await self._db.find_active_match(ladder_id, user_id)
        if active is not None and active["status"] == MATCH_STATUS_CREATED:
            raise QueueStateError("cancel the pending match before leaving the queue")
        player = player.model_copy(update={"queued_at": None})
        await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        await self._emit("queue.left", {"ladder_id": ladder_id, "user_id": user_id})
        return player

    async def run_matchmaking_pass(self, ladder_id: str, now: int) -> list[MatchModel]:
        """One matchmaking pass: expire deadlines, pair, create matches.

        Under the caller-held tick lock (§8). Created matches leave the
        queue immediately; both sides get a ready deadline.
        """
        ladder = await self._require_ladder(ladder_id)
        await self._expire_created_deadlines(ladder, now)
        queued_docs = await self._db.find_queued_players(ladder_id)
        entries = [
            QueueEntry(d["user_id"], float(d["rating"]), int(d["queued_at"]))
            for d in queued_docs
            if d.get("queued_at") is not None
        ]
        matches: list[MatchModel] = []
        for pairing in matchmaking_pass(entries, ladder.settings, now):
            match = await self._create_match(
                ladder, Pairing(pairing.host_user_id, pairing.guest_user_id), ORIGIN_MATCHMAKING, now
            )
            matches.append(match)
        return matches

    async def _expire_created_deadlines(self, ladder: LadderModel, now: int) -> list[MatchModel]:
        """Cancel CREATED matches past their ready deadline (system cancel)."""
        out: list[MatchModel] = []
        docs = await self._db.find_ladder_matches(ladder.id, [MATCH_STATUS_CREATED])
        for doc in docs:
            match = MatchModel.from_mongo(doc)
            if match.ready_deadline_at is not None and match.ready_deadline_at < now:
                out.append(await self._system_cancel(ladder, match, "ready_deadline", now))
        return out

    # ── Invites ──────────────────────────────────────────────────────────

    async def create_invite_match(self, ladder_id: str, host_user_id: str, guest_user_id: str, now: int) -> MatchModel:
        """Create a match from a direct invite (bypasses the queue)."""
        ladder = await self._require_ladder(ladder_id)
        host = await self._require_player(ladder_id, host_user_id)
        guest = await self._require_player(ladder_id, guest_user_id)
        for user_id in (host_user_id, guest_user_id):
            if await self._db.find_active_match(ladder_id, user_id) is not None:
                raise ActiveMatchError(f"user {user_id!r} already has a live match")
        del host, guest
        return await self._create_match(ladder, Pairing(host_user_id, guest_user_id), ORIGIN_INVITE, now)

    # ── Match state machine ───────────────────────────────────────────────

    async def get_match(self, match_id: str) -> MatchModel | None:
        """Return one match; None when unknown."""
        doc = await self._db.find_entry(MATCHES_COLLECTION, match_id)
        return MatchModel.from_mongo(doc) if doc else None

    async def mark_ready(self, match_id: str, user_id: str, now: int) -> MatchModel:
        """Confirm ready; when both sides are ready, pick the map → STARTED."""
        match = await self._require_match(match_id)
        self._require_participant(match, user_id)
        if match.status != MATCH_STATUS_CREATED:
            return match
        side = match.side_of(user_id)
        if side is None or side.ready_at is not None:
            return match
        updates: dict[str, Any] = {}
        if match.host.user_id == user_id:
            updates["host"] = side.model_copy(update={"ready_at": now})
        else:
            updates["guest"] = side.model_copy(update={"ready_at": now})
        match = match.model_copy(update=updates)
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        await self._emit("match.player_ready", {"match_id": match_id, "user_id": user_id})
        if match.host.ready_at is not None and match.guest.ready_at is None:
            return match
        if match.host.ready_at is not None and match.guest.ready_at is not None:
            return await self._start_match(match, now)
        return match

    async def _start_match(self, match: MatchModel, now: int) -> MatchModel:
        """Both ready → pick map, snapshot it, leave the queue for good."""
        ladder = await self._require_ladder(match.ladder_id)
        pool_id = ladder.active_map_pool_id
        if pool_id is None:
            raise MapPoolError(f"ladder {match.ladder_id!r} has no active map pool")
        pool = await self._game_data.get_map_pool(pool_id)
        if pool is None:
            raise MapPoolError(f"active map pool {pool_id!r} not found")
        candidates = [
            mid
            for mid in await self._game_data.resolve_pool_map_ids(pool)
            if mid not in set(match.host.ban_map_ids) | set(match.guest.ban_map_ids)
        ]
        if not candidates:
            raise MapPoolError("every pool map is banned for this match")
        weights = []
        for mid in candidates:
            favs = (match.host.fav_map_ids + match.guest.fav_map_ids).count(mid)
            weights.append(1 + favs)
        map_id = self._rng.choices(candidates, weights=weights, k=1)[0]
        map_entry = await self._game_data.get_map(map_id)
        snapshot = {"name": map_entry.name if map_entry else "", "filename": map_entry.filename if map_entry else ""}
        for user_id in (match.host.user_id, match.guest.user_id):
            player = await self.get_player(match.ladder_id, user_id)
            if player is not None and player.queued_at is not None:
                player = player.model_copy(update={"queued_at": None})
                await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        match = match.model_copy(
            update={
                "status": MATCH_STATUS_STARTED,
                "map_id": map_id,
                "map_snapshot": snapshot,
                "ready_completed_at": now,
                "started_at": now,
            }
        )
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        await self._emit("match.started", {"match_id": match.id, "map_id": map_id})
        return match

    async def apply_game_event(self, match_id: str, event: dict[str, Any]) -> MatchModel:
        """Apply one GameGateway lifecycle event (idempotent; §5.2 diagram)."""
        match = await self._require_match(match_id)
        kind = event.get("kind")
        now = int(event.get("now", 0))
        transitions: dict[tuple[str, str], str] = {
            (MATCH_STATUS_STARTED, "lobby_opened"): MATCH_STATUS_LOBBY_OPEN,
            (MATCH_STATUS_LOBBY_OPEN, "lobby_closed"): MATCH_STATUS_LOBBY_CLOSED,
            (MATCH_STATUS_LOBBY_CLOSED, "game_started"): MATCH_STATUS_GAME_LIVE,
            (MATCH_STATUS_LOBBY_OPEN, "game_started"): MATCH_STATUS_GAME_LIVE,
            (MATCH_STATUS_GAME_LIVE, "game_ended"): MATCH_STATUS_GAME_ENDED,
            (MATCH_STATUS_GAME_ENDED, "result_ready"): MATCH_STATUS_RESULT_PENDING,
        }
        if not self._gateway.realtime:
            return match
        target = transitions.get((match.status, str(kind)))
        if target is None:
            return match
        updates: dict[str, Any] = {"status": target}
        if kind == "lobby_opened":
            game = match.game.model_copy(update={"match_ref": event.get("match_ref")})
            updates["game"] = game
        if kind == "game_started":
            participants = tuple(
                {"user_id": p["user_id"], "faction_key": p.get("faction_key", "")}
                for p in event.get("participants", [])
            )
            missing = {match.host.user_id, match.guest.user_id} - {p["user_id"] for p in participants}
            if missing:
                await self._emit("match.missing_participant", {"match_id": match_id, "missing": sorted(missing)})
                return match
            game = match.game.model_copy(
                update={"participants": participants, "started_at": now, "map_name": event.get("map_name")}
            )
            updates["game"] = game
        if kind == "game_ended":
            game = match.game.model_copy(update={"ended_at": now, "duration": event.get("duration")})
            updates["game"] = game
        match = match.model_copy(update=updates)
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        intent = {
            "lobby_opened": "match.lobby_opened",
            "lobby_closed": "match.lobby_closed",
            "game_started": "match.game_started",
            "game_ended": "match.game_ended",
            "result_ready": "match.result_reported",
        }[str(kind)]
        await self._emit(intent, {"match_id": match_id})
        return match

    async def enter_result_pending(self, match_id: str) -> MatchModel:
        """Without realtime: STARTED → RESULT_PENDING on manual report."""
        match = await self._require_match(match_id)
        if match.status not in (MATCH_STATUS_STARTED, MATCH_STATUS_GAME_ENDED):
            return match
        match = match.model_copy(update={"status": MATCH_STATUS_RESULT_PENDING})
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        return match

    async def report_result(self, match_id: str, user_id: str, winner_user_id: str, now: int) -> MatchModel:
        """Report manually: contradictory reports cancel; both same → REPORTED."""
        match = await self._require_match(match_id)
        self._require_participant(match, user_id)
        if match.status == MATCH_STATUS_RESULT_PENDING:
            match = await self._record_report(match, user_id, winner_user_id, now)
            await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
            return match
        if match.status == MATCH_STATUS_STARTED and not self._gateway.realtime:
            match = await self.enter_result_pending(match_id)
            match = await self._record_report(match, user_id, winner_user_id, now)
            await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
            return match
        return match

    async def _record_report(self, match: MatchModel, user_id: str, winner_user_id: str, now: int) -> MatchModel:
        """Fold one manual report into the match (conflicts reset reports)."""
        if match.winner_user_id is None:
            match = match.model_copy(
                update={"winner_user_id": winner_user_id, "reporter_user_id": user_id, "reported_at": now}
            )
            await self._emit("match.result_reported", {"match_id": match.id, "by": user_id})
            return match
        if match.winner_user_id == winner_user_id:
            match = match.model_copy(update={"status": MATCH_STATUS_REPORTED})
            await self._emit("match.result_reported", {"match_id": match.id, "by": user_id})
            return match
        match = match.model_copy(
            update={
                "winner_user_id": None,
                "reporter_user_id": None,
                "reported_at": None,
                "invalid_report_attempts": match.invalid_report_attempts + 1,
            }
        )
        await self._emit("match.report_conflict", {"match_id": match.id})
        return match

    async def confirm_result(self, match_id: str, user_id: str, now: int) -> MatchModel:
        """Confirm a reported result → COMPLETED, rating applied once."""
        match = await self._require_match(match_id)
        self._require_participant(match, user_id)
        if match.status not in (MATCH_STATUS_REPORTED,):
            return match
        return await self._complete(match, user_id, now)

    async def auto_confirm(self, match_id: str, result: dict[str, Any], now: int) -> MatchModel:
        """Gateway auto-report path: reliable result → straight to COMPLETED."""
        match = await self._require_match(match_id)
        if match.status in (MATCH_STATUS_RESULT_PENDING, MATCH_STATUS_GAME_ENDED) and self._gateway.reliable_results:
            winner = result.get("winner_user_id")
            loser = result.get("loser_user_id")
            match = match.model_copy(
                update={
                    "status": MATCH_STATUS_REPORTED,
                    "winner_user_id": winner,
                    "loser_user_id": loser,
                    "reporter_user_id": "system",
                    "reported_at": now,
                }
            )
            await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
            return await self._complete(match, "system", now)
        return match

    async def _complete(self, match: MatchModel, confirm_user_id: str, now: int) -> MatchModel:
        """Complete a REPORTED match, applying the rating exactly once (§3)."""
        if match.rating_applied is not None:
            return match.model_copy(update={"status": MATCH_STATUS_COMPLETED})
        ladder = await self._require_ladder(match.ladder_id)
        system = RATING_SYSTEMS[ladder.settings.rating_system]
        winner_id, loser_id = match.winner_user_id, match.loser_user_id or match.guest.user_id
        if winner_id is None or (winner_id != match.host.user_id and winner_id != match.guest.user_id):
            raise TransitionError("cannot complete a match without a valid winner")
        if loser_id == winner_id:
            loser_id = match.guest.user_id if winner_id == match.host.user_id else match.host.user_id
        winner = await self._require_player(match.ladder_id, winner_id)
        loser = await self._require_player(match.ladder_id, loser_id)
        w_delta, w_k, w_state = system.apply(
            ladder.settings,
            winner.rating,
            winner.rating_state,
            loser.rating,
            loser.rating_state,
            True,
            winner.matches_count,
        )
        l_delta, l_k, l_state = system.apply(
            ladder.settings,
            loser.rating,
            loser.rating_state,
            winner.rating,
            winner.rating_state,
            False,
            loser.matches_count,
        )
        now_ms = now
        w_after = winner.rating + w_delta
        l_after = (
            max(system.floor(ladder.settings), loser.rating + l_delta)
            if ladder.settings.rating_system == "elo"
            else loser.rating + l_delta
        )
        l_delta = l_after - loser.rating
        rating_applied = {
            winner_id: RatingAppliedSideModel(before=winner.rating, after=w_after, delta=w_delta, k=w_k),
            loser_id: RatingAppliedSideModel(before=loser.rating, after=l_after, delta=l_delta, k=l_k),
        }
        for user_id, entry in ((winner_id, rating_applied[winner_id]), (loser_id, rating_applied[loser_id])):
            await self._db.upsert_entry(
                RATING_HISTORY_COLLECTION,
                RatingHistoryModel(
                    _id=f"rh:{match.id}:{user_id}",
                    ladder_id=match.ladder_id,
                    match_id=match.id,
                    user_id=user_id,
                    rating_before=entry.before,
                    rating_after=entry.after,
                    delta=entry.delta,
                    k_used=entry.k,
                    reason=RATING_REASON_MATCH_RESULT,
                    created_at=now_ms,
                ).to_mongo(),
            )
        winner_new = winner.model_copy(
            update={
                "rating": int(w_after) if ladder.settings.rating_system == "elo" else w_after,
                "rating_state": w_state or winner.rating_state,
                "matches_count": winner.matches_count + 1,
                "wins": winner.wins + 1,
                "streak": max(1, winner.streak + 1),
                "rating_max": int(max(winner.rating_max, w_after)),
            }
        )
        loser_new = loser.model_copy(
            update={
                "rating": int(l_after) if ladder.settings.rating_system == "elo" else l_after,
                "rating_state": l_state or loser.rating_state,
                "matches_count": loser.matches_count + 1,
                "losses": loser.losses + 1,
                "streak": min(-1, loser.streak - 1),
            }
        )
        await self._db.upsert_entry(PLAYERS_COLLECTION, winner_new.to_mongo())
        await self._db.upsert_entry(PLAYERS_COLLECTION, loser_new.to_mongo())
        match = match.model_copy(
            update={
                "status": MATCH_STATUS_COMPLETED,
                "confirm_user_id": confirm_user_id,
                "completed_at": now_ms,
                "rating_applied": rating_applied,
                "winner_user_id": winner_id,
                "loser_user_id": loser_id,
            }
        )
        match = await self._enrich_with_provider_data(match)
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        await self._emit("match.result_confirmed", {"match_id": match.id, "rating": rating_applied})
        return match

    async def _enrich_with_provider_data(self, match: MatchModel) -> MatchModel:
        """Pull map/civs/duration from the provider once per match (#205).

        Degrades silently: no match-data service, no match_ref or a
        provider outage leave the match document untouched — the
        completion is never blocked by enrichment.
        """
        if self._match_data is None:
            return match
        match_ref = (match.game.match_ref or "") if match.game else ""
        if not match_ref:
            return match
        try:
            extracted = await self._match_data.enrich_completed_match(match_ref, match.to_mongo())
        except Exception:
            logger.warning("provider enrichment failed for %s", match_ref, exc_info=True)
            return match
        if not extracted:
            return match
        participants = tuple(
            {"user_id": "", "faction_key": civ["faction_key"], "profile_id": civ["profile_id"]}
            for civ in extracted.get("civs", [])
        )
        game = match.game.model_copy(
            update={
                "map_name": extracted.get("map") or match.game.map_name,
                "duration": extracted.get("duration_s") or match.game.duration,
                "participants": participants or match.game.participants,
            }
        )
        return match.model_copy(update={"game": game})

    async def cancel_match(self, match_id: str, user_id: str, reason: str, now: int) -> MatchModel:
        """Cancel by participant (reason mandatory) — never after COMPLETED."""
        match = await self._require_match(match_id)
        self._require_participant(match, user_id)
        if match.status in (MATCH_STATUS_COMPLETED, MATCH_STATUS_CANCELED):
            raise TransitionError(f"match {match_id!r} is already {match.status}")
        match = match.model_copy(
            update={
                "status": MATCH_STATUS_CANCELED,
                "cancel_reason": reason,
                "canceled_by": user_id,
                "canceled_at": now,
            }
        )
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        player = await self.get_player(match.ladder_id, user_id)
        if player is not None and player.queued_at is not None:
            player = player.model_copy(update={"queued_at": None})
            await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        await self._emit("match.canceled", {"match_id": match_id, "by": user_id, "reason": reason})
        return match

    async def _system_cancel(self, ladder: LadderModel, match: MatchModel, reason: str, now: int) -> MatchModel:
        """Cancel by system (deadline/missing player): non-ready sides unqueued."""
        match = match.model_copy(
            update={
                "status": MATCH_STATUS_CANCELED,
                "cancel_reason": reason,
                "canceled_by": "system",
                "canceled_at": now,
            }
        )
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        for side in (match.host, match.guest):
            if side.ready_at is None:
                player = await self.get_player(ladder.id, side.user_id)
                if player is not None and player.queued_at is not None:
                    player = player.model_copy(update={"queued_at": None})
                    await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        await self._emit("match.ready_timeout", {"match_id": match.id, "reason": reason})
        return match

    async def _create_match(self, ladder: LadderModel, pairing: Pairing, origin: str, now: int) -> MatchModel:
        """Create a CREATED match with a ready deadline and dequeue both."""
        host = await self._require_player(ladder.id, pairing.host_user_id)
        guest = await self._require_player(ladder.id, pairing.guest_user_id)
        match = MatchModel(
            _id=f"match:{ladder.id}:{pairing.host_user_id}:{pairing.guest_user_id}:{now}",
            ladder_id=ladder.id,
            status=MATCH_STATUS_CREATED,
            created_at=now,
            ready_deadline_at=now + ladder.settings.ready_timeout * 1000,
            host=MatchSideModel(
                user_id=pairing.host_user_id, fav_map_ids=host.fav_map_ids, ban_map_ids=host.ban_map_ids
            ),
            guest=MatchSideModel(
                user_id=pairing.guest_user_id, fav_map_ids=guest.fav_map_ids, ban_map_ids=guest.ban_map_ids
            ),
            origin=origin,
        )
        await self._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        for user_id in (pairing.host_user_id, pairing.guest_user_id):
            player = await self.get_player(ladder.id, user_id)
            if player is not None:
                player = player.model_copy(update={"queued_at": None})
                await self._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        await self._emit("match.created", {"match_id": match.id, "origin": origin})
        return match

    # ── Leaderboard ───────────────────────────────────────────────────────

    async def leaderboard(self, ladder_id: str, limit: int = 100) -> list[PlayerModel]:
        """Top players by rating, rank-assigned (ties share order)."""
        docs = await self._db.find_ladder_players(ladder_id)
        players = [PlayerModel.from_mongo(d) for d in docs]
        players.sort(key=lambda p: (-p.rating, p.matches_count))
        return players[:limit]

    # ── helpers ──────────────────────────────────────────────────────────

    async def _require_ladder(self, ladder_id: str) -> LadderModel:
        """Fetch one ladder or fail loudly."""
        doc = await self._db.find_entry(LADDERS_COLLECTION, ladder_id)
        if doc is None:
            raise LadderError(f"unknown ladder {ladder_id!r}")
        return LadderModel.from_mongo(doc)

    async def _require_player(self, ladder_id: str, user_id: str) -> PlayerModel:
        """Fetch one player or fail loudly."""
        doc = await self._db.find_player(ladder_id, user_id)
        if doc is None:
            raise NotRegisteredError(f"user {user_id!r} is not registered on ladder {ladder_id!r}")
        return PlayerModel.from_mongo(doc)

    async def _require_match(self, match_id: str) -> MatchModel:
        """Fetch one match or fail loudly."""
        doc = await self._db.find_entry(MATCHES_COLLECTION, match_id)
        if doc is None:
            raise LadderError(f"unknown match {match_id!r}")
        return MatchModel.from_mongo(doc)

    @staticmethod
    def _require_participant(match: MatchModel, user_id: str) -> None:
        """Fail loudly when the user is not a participant."""
        if match.side_of(user_id) is None:
            raise NotParticipantError(f"user {user_id!r} is not a participant of {match.id!r}")

    async def _emit(self, intent: str, payload: dict[str, Any]) -> None:
        """Emit one notification intent (best-effort, never raises)."""
        if self._events is None:
            return
        try:
            await self._events.emit(intent, payload)
        except Exception:
            logger.warning("LADDER EVENT EMIT FAILED (%s)", intent, exc_info=True)

    async def _audit_record(self, action: str, payload: dict[str, Any]) -> None:
        """Write one audit line (best-effort)."""
        if self._audit is None:
            return
        try:
            await self._audit.record(action, payload)
        except Exception:
            logger.warning("AUDIT WRITE FAILED (%s)", action, exc_info=True)
