"""Ladder rating admin tools: adjust ±N, cancel-match with compensation (kingdoms-services#136).

Reference §3 corrections: an adjustment before application is just a
future-facing tweak; an inconsistency *after* application is repaired
with an inverse ``MANUAL_ADJUSTMENT`` line, counter recomputation and
the match's ``rating_applied`` cleared — the compensation is audited
with its payload diff. Every mutation lands in ``admin_audit``.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kingdoms.mods.ladder.models import (
    MATCH_STATUS_CANCELED,
    MATCH_STATUS_COMPLETED,
    MATCHES_COLLECTION,
    PLAYERS_COLLECTION,
    RATING_HISTORY_COLLECTION,
    RATING_REASON_MANUAL_ADJUSTMENT,
    RATING_REASON_RESET,
    MatchModel,
    PlayerModel,
    RatingHistoryModel,
)
from kingdoms.mods.ladder.service import LadderService

logger = logging.getLogger("kingdoms.mods.ladder.admin_tools")


class AdminAudit(Protocol):
    """Admin-audit seam: every admin mutation is recorded."""

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        """Persist one admin-audit line."""
        ...


class RatingToolError(ValueError):
    """The rating operation is invalid (missing reason, bad amounts...)."""


class LadderRatingTools:
    """Admin rating operations: manual adjust, reset, cancel with compensation."""

    def __init__(self, service: LadderService, audit: AdminAudit | None = None) -> None:
        """Wire the domain service and the audit seam."""
        self._svc = service
        self._audit = audit

    async def adjust_rating(
        self, ladder_id: str, admin_user_id: str, user_id: str, amount: int, reason: str, now: int
    ) -> PlayerModel:
        """Apply a manual ±N adjustment (reason mandatory), history-lined."""
        if not reason.strip():
            raise RatingToolError("an adjustment reason is mandatory")
        if amount == 0:
            raise RatingToolError("amount must be non-zero")
        player = await self._svc.get_player(ladder_id, user_id)
        if player is None:
            raise RatingToolError(f"user {user_id!r} is not registered on ladder {ladder_id!r}")
        ladder = await self._svc.get_ladder(ladder_id)
        if ladder is None:
            raise RatingToolError(f"unknown ladder {ladder_id!r}")
        before = player.rating
        after = max(int(ladder.settings.elo_floor), before + amount)
        await self._write_history(ladder_id, "admin_adjustment", user_id, before, after, admin_user_id, now)
        player = player.model_copy(update={"rating": after, "rating_max": max(player.rating_max, after)})
        await self._svc._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
        await self._audit_record(
            "rating.adjust",
            {
                "ladder_id": ladder_id,
                "user_id": user_id,
                "admin_user_id": admin_user_id,
                "before": before,
                "after": after,
                "reason": reason,
            },
        )
        return player

    async def reset_ratings(self, ladder_id: str, admin_user_id: str, reason: str, now: int) -> list[PlayerModel]:
        """Reset every player's rating to the ladder initial (RESET lines)."""
        if not reason.strip():
            raise RatingToolError("a reset reason is mandatory")
        ladder = await self._svc.get_ladder(ladder_id)
        if ladder is None:
            raise RatingToolError(f"unknown ladder {ladder_id!r}")
        players = await self._svc.leaderboard(ladder_id, limit=10_000)
        out: list[PlayerModel] = []
        for player in players:
            before = player.rating
            after = int(ladder.settings.elo_initial)
            if before == after:
                out.append(player)
                continue
            await self._write_history(
                ladder_id,
                "ratings_reset",
                player.user_id,
                before,
                after,
                admin_user_id,
                now,
                reason_code=RATING_REASON_RESET,
            )
            player = player.model_copy(update={"rating": after})
            await self._svc._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
            out.append(player)
        await self._audit_record(
            "rating.reset",
            {"ladder_id": ladder_id, "admin_user_id": admin_user_id, "reason": reason, "count": len(out)},
        )
        return out

    async def cancel_match_with_compensation(
        self, match_id: str, admin_user_id: str, reason: str, now: int
    ) -> MatchModel:
        """Cancel a COMPLETED match and compensate every applied rating (§3)."""
        if not reason.strip():
            raise RatingToolError("a cancellation reason is mandatory")
        match = await self._svc.get_match(match_id)
        if match is None:
            raise RatingToolError(f"unknown match {match_id!r}")
        if match.status != MATCH_STATUS_COMPLETED or match.rating_applied is None:
            raise RatingToolError(f"match {match_id!r} is not completed")
        compensated: dict[str, Any] = {}
        for user_id, side in match.rating_applied.items():
            player = await self._svc.get_player(match.ladder_id, user_id)
            if player is None:
                continue
            before = player.rating
            after = int(side.before)
            await self._write_history(match.ladder_id, match.id, user_id, before, after, admin_user_id, now)
            wins = player.wins - (1 if user_id == match.winner_user_id else 0)
            losses = player.losses - (1 if user_id == match.loser_user_id else 0)
            streak = 0 if (wins == 0 and losses == 0) else (player.streak if abs(player.streak) > 1 else 0)
            player = player.model_copy(
                update={
                    "rating": after,
                    "wins": wins,
                    "losses": losses,
                    "matches_count": player.matches_count - 1,
                    "streak": streak,
                }
            )
            await self._svc._db.upsert_entry(PLAYERS_COLLECTION, player.to_mongo())
            compensated[user_id] = {"before": before, "after": after}
        match = match.model_copy(
            update={
                "status": MATCH_STATUS_CANCELED,
                "rating_applied": None,
                "cancel_reason": f"admin_compensation: {reason}",
                "canceled_by": admin_user_id,
                "canceled_at": now,
            }
        )
        await self._svc._db.upsert_entry(MATCHES_COLLECTION, match.to_mongo())
        await self._audit_record(
            "rating.cancel_match",
            {"match_id": match_id, "admin_user_id": admin_user_id, "reason": reason, "compensated": compensated},
        )
        return match

    async def _write_history(
        self,
        ladder_id: str,
        match_id: str,
        user_id: str,
        before: float,
        after: float,
        admin_user_id: str,
        now: int,
        reason_code: str = RATING_REASON_MANUAL_ADJUSTMENT,
    ) -> None:
        """Write one MANUAL_ADJUSTMENT/RESET rating-history line."""
        await write_history_line(
            self._svc, ladder_id, match_id, user_id, before, after, admin_user_id, now, reason_code
        )

    async def _audit_record(self, action: str, payload: dict[str, Any]) -> None:
        """Write one audit line (best-effort)."""
        await audit_record(self._audit, action, payload)


async def write_history_line(
    service: LadderService,
    ladder_id: str,
    match_id: str,
    user_id: str,
    before: float,
    after: float,
    admin_user_id: str,
    now: int,
    reason_code: str = RATING_REASON_MANUAL_ADJUSTMENT,
) -> None:
    """Write one rating-history line (shared by the admin tool services)."""
    await service._db.upsert_entry(
        RATING_HISTORY_COLLECTION,
        RatingHistoryModel(
            _id=f"rh:{match_id}:{user_id}:{now}",
            ladder_id=ladder_id,
            match_id=match_id,
            user_id=user_id,
            rating_before=before,
            rating_after=after,
            delta=after - before,
            k_used=0.0,
            reason=reason_code,
            admin_user_id=admin_user_id,
            created_at=now,
        ).to_mongo(),
    )


async def audit_record(audit: AdminAudit | None, action: str, payload: dict[str, Any]) -> None:
    """Write one audit line (best-effort: a store failure logs, never raises)."""
    if audit is None:
        return
    try:
        await audit.record(action, payload)
    except Exception:
        logger.warning("AUDIT WRITE FAILED (%s)", action, exc_info=True)


class RatingSystemSwitchError(ValueError):
    """The rating-system switch is refused (in-flight or incomplete history)."""


class LadderRatingSwitchService:
    """Deterministic history replay when switching rating systems (#142)."""

    def __init__(self, service: LadderService, audit: AdminAudit | None = None) -> None:
        """Wire the domain service and the audit seam."""
        self._svc = service
        self._audit = audit

    async def switch_rating_system(
        self, ladder_id: str, admin_user_id: str, new_system: str, now: int
    ) -> dict[str, Any]:
        """Replay the full match history under the new system (idempotent per state).

        Preconditions: the target system exists, no match is in flight
        (status < COMPLETED) and the match history is complete — every
        COMPLETED match must have its rating applied. The replay resets
        the caches from the replayed history, writes RECALCULATION lines
        and audits with a before/after summary.
        """
        from kingdoms.mods.ladder.models import (
            LIVE_MATCH_STATUSES,
            MATCH_STATUS_COMPLETED,
        )
        from kingdoms.mods.ladder.service import RATING_SYSTEMS

        if new_system not in RATING_SYSTEMS:
            raise RatingSystemSwitchError(f"unknown rating system {new_system!r}")
        ladder = await self._svc.get_ladder(ladder_id)
        if ladder is None:
            raise RatingSystemSwitchError(f"unknown ladder {ladder_id!r}")
        if ladder.settings.rating_system == new_system:
            return {"ladder_id": ladder_id, "system": new_system, "replayed": 0, "changed": False}
        statuses = sorted(LIVE_MATCH_STATUSES | {MATCH_STATUS_COMPLETED})
        matches = await self._svc._db.find_ladder_matches(ladder_id, statuses)
        for doc in matches:
            if doc["status"] != MATCH_STATUS_COMPLETED:
                raise RatingSystemSwitchError("switch forbidden while a match is in flight")
            if not doc.get("rating_applied"):
                raise RatingSystemSwitchError("match history incomplete: a completed match has no rating applied")
        completed = sorted(
            (m for m in matches if m["status"] == MATCH_STATUS_COMPLETED),
            key=lambda m: m["completed_at"] or 0,
        )
        system = RATING_SYSTEMS[new_system]
        settings = ladder.settings.model_copy(update={"rating_system": new_system})
        players = {p.user_id: p for p in await self._svc.leaderboard(ladder_id, limit=10_000)}
        initial = system.initial_rating(settings)
        state: dict[str, dict[str, float]] = {uid: dict(system.initial_state(settings)) for uid in players}
        ratings: dict[str, float] = dict.fromkeys(players, initial)
        counts: dict[str, int] = dict.fromkeys(players, 0)
        replayed = 0
        for doc in completed:
            match = doc
            winner_id = match["winner_user_id"]
            loser_id = match["loser_user_id"]
            if winner_id not in ratings or loser_id not in ratings:
                raise RatingSystemSwitchError("match history incomplete: unknown participant")
            w_delta, _w_k, w_state = system.apply(
                settings,
                ratings[winner_id],
                state[winner_id],
                ratings[loser_id],
                state[loser_id],
                True,
                counts[winner_id],
            )
            l_delta, _l_k, l_state = system.apply(
                settings,
                ratings[loser_id],
                state[loser_id],
                ratings[winner_id],
                state[winner_id],
                False,
                counts[loser_id],
            )
            ratings[winner_id] += w_delta
            ratings[loser_id] += l_delta
            state[winner_id] = w_state
            state[loser_id] = l_state
            counts[winner_id] += 1
            counts[loser_id] += 1
            replayed += 1
        for uid, player in players.items():
            new_rating = ratings[uid]
            await write_history_line(
                self._svc,
                ladder_id,
                f"recalc:{ladder_id}",
                uid,
                player.rating,
                new_rating,
                admin_user_id,
                now,
                reason_code="RECALCULATION",
            )
            updated = player.model_copy(
                update={"rating": int(new_rating), "rating_state": state[uid], "matches_count": counts[uid]}
            )
            await self._svc._db.upsert_entry("players", updated.to_mongo())
        ladder = ladder.model_copy(update={"settings": settings})
        await self._svc._db.upsert_entry("ladders", ladder.to_mongo())
        await audit_record(
            self._audit,
            "rating.system.switch",
            {
                "ladder_id": ladder_id,
                "admin_user_id": admin_user_id,
                "from": ladder.settings.rating_system if replayed == 0 else None,
                "to": new_system,
                "replayed": replayed,
            },
        )
        return {"ladder_id": ladder_id, "system": new_system, "replayed": replayed, "changed": True}
