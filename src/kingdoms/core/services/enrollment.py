"""Generic enrollment: apply → criteria → pending → accept/refuse (mods-init).

One core service every mod reuses — a mod declares its enrollment in
``settings.enrollment`` and gets the full parcours: application,
automatic criteria (min_elo, min_matches, requires_profile), a pending
queue when validation is needed, admin accept/refuse with reasons, and
the membership/role sync through the existing ``MembershipService``.

Design (ported from the kingdoms mod apply flow, conventions of Drasah):

- ONE application per player per guild per season (re-applying is
  idempotent while pending);
- states are the Drasah trio: pending ⏳ / accepted ✅ / refused ❌;
- automatic criteria run at apply time; all-pass + no manual flag →
  auto-accept; any failure → refused with the failing criteria listed;
  otherwise the application waits in the queue for an admin;
- accepting a candidate delegates to ``MembershipService.register``
  (season roles sync included); refusing records the reason.

Reference: kingdoms-services#267 (spec), Drasah's apply flow (vibe/drasah/main).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

STATE_PENDING = "pending"
STATE_ACCEPTED = "accepted"
STATE_REFUSED = "refused"

CRITERION_MIN_ELO = "min_elo"
CRITERION_MIN_MATCHES = "min_matches"
CRITERION_REQUIRES_PROFILE = "requires_profile"
CRITERION_MANUAL = "manual"


class EnrollmentDatabase(Protocol):
    """The persistence seam for applications."""

    async def upsert_application(self, document: dict[str, Any]) -> None:
        """Insert or replace one application document."""
        ...

    async def find_application(self, application_id: str) -> dict[str, Any] | None:
        """Return one application document; None when unknown."""
        ...

    async def find_active_application(self, mod: str, guild_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the player's pending application for this mod+guild."""
        ...

    async def list_applications(self, mod: str, guild_id: str, state: str | None = None) -> list[dict[str, Any]]:
        """List the guild's applications, optionally filtered by state."""
        ...

    async def count_members(self, mod: str, guild_id: str) -> int:
        """Count accepted applications (the enrolled members)."""
        ...


class EnrollmentStats(Protocol):
    """The per-player facts the automatic criteria evaluate."""

    async def player_elo(self, user_id: str, game_key: str) -> int | None:
        """Return the player's rating for one game; None when unrated."""
        ...

    async def matches_in_window(self, user_id: str, game_key: str, days: int) -> int | None:
        """Return matches played in the last N days; None when unknown."""
        ...

    async def has_profile(self, user_id: str, game_key: str) -> bool:
        """Whether the player has a bound, validated game profile."""
        ...


class EnrollmentMembership(Protocol):
    """The membership seam — accept delegates to ``MembershipService.register``."""

    async def register(self, guild_id: str, user_id: str, display_name: str) -> Any:
        """Register one member (roles sync inside)."""
        ...


@dataclass(frozen=True, slots=True)
class CriterionResult:
    """One criterion's evaluation: passed, and a human reason."""

    key: str
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Application:
    """The enrollment state machine's state, as data."""

    mod: str
    guild_id: str
    user_id: str
    display_name: str
    state: str
    season: str | None = None
    applied_at: int = 0
    decided_at: int | None = None
    reasons: tuple[str, ...] = ()
    criteria_results: tuple[CriterionResult, ...] = ()

    @property
    def application_id(self) -> str:
        """The stable document id: mod:guild:user(+season when seasonal)."""
        scope = f"{self.mod}:{self.guild_id}:{self.user_id}"
        return f"{scope}:{self.season}" if self.season else scope


class AlreadyPendingError(ValueError):
    """The player already has a pending application (re-apply is a no-op)."""


class NotPendingError(ValueError):
    """The application is not in the pending state."""


def parse_criteria(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Parse and validate the declared enrollment criteria.

    Raises ValueError loudly on an unknown criterion — a broken
    declaration never silently disables enrollment.
    """
    declared = (settings or {}).get("enrollment") or {}
    if not isinstance(declared, dict):
        raise ValueError("'settings.enrollment' must be a mapping")
    known = {CRITERION_MIN_ELO, CRITERION_MIN_MATCHES, CRITERION_REQUIRES_PROFILE, CRITERION_MANUAL}
    unknown = set(declared) - known
    if unknown:
        raise ValueError(f"unknown enrollment criteria: {sorted(unknown)}")
    return dict(declared)


class EnrollmentService:
    """Run the generic enrollment parcours for one mod."""

    def __init__(
        self,
        mod_name: str,
        database: EnrollmentDatabase,
        stats: EnrollmentStats | None = None,
        membership: EnrollmentMembership | None = None,
        game_key: str = "",
        criteria: dict[str, Any] | None = None,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self._mod = mod_name
        self._db = database
        self._stats = stats
        self._membership = membership
        self._game_key = game_key
        self._criteria = criteria if criteria is not None else {}
        self._timezone = timezone

    @property
    def mod_name(self) -> str:
        """The mod this enrollment belongs to."""
        return self._mod

    async def apply(
        self,
        guild_id: str,
        user_id: str,
        display_name: str,
        season: str | None = None,
        now: int = 0,
    ) -> Application:
        """Apply for enrollment: criteria → auto-accept / refuse / queue.

        Idempotent while pending: re-applying returns the existing
        application untouched (one bubble, one state — Drasah's rule).
        """
        existing = await self._db.find_active_application(self._mod, guild_id, user_id)
        if existing is not None:
            return _application_from_doc(existing)
        application = Application(
            mod=self._mod,
            guild_id=guild_id,
            user_id=user_id,
            display_name=display_name,
            state=STATE_PENDING,
            season=season,
            applied_at=now,
        )
        results = await self._evaluate(application)
        stored = Application(
            mod=application.mod,
            guild_id=application.guild_id,
            user_id=application.user_id,
            display_name=application.display_name,
            state=STATE_PENDING,
            season=application.season,
            applied_at=application.applied_at,
            criteria_results=results,
        )
        if not results:
            # no automatic criteria: the queue decides (or the mod has
            # no criteria at all — the admin dashboard is the surface)
            await self._db.upsert_application(_application_to_doc(stored))
            return stored
        failures = [r.reason for r in results if not r.passed]
        if failures:
            return await self._decide(application, STATE_REFUSED, now=now, reasons=failures)
        if not self._criteria.get(CRITERION_MANUAL):
            return await self._decide(application, STATE_ACCEPTED, now=now)
        await self._db.upsert_application(_application_to_doc(stored))
        return stored

    async def accept(self, application_id: str, now: int = 0, reason: str = "") -> Application:
        """Accept a pending application; registers the member (roles sync)."""
        application = await self._require(application_id)
        if application.state != STATE_PENDING:
            raise NotPendingError(f"application {application_id!r} is {application.state}, not pending")
        return await self._decide(application, STATE_ACCEPTED, now=now, reason=reason)

    async def refuse(self, application_id: str, now: int = 0, reason: str = "") -> Application:
        """Refuse a pending application, recording the reason."""
        application = await self._require(application_id)
        if application.state != STATE_PENDING:
            raise NotPendingError(f"application {application_id!r} is {application.state}, not pending")
        return await self._decide(application, STATE_REFUSED, now=now, reason=reason)

    async def list_applications(self, guild_id: str, state: str | None = None) -> list[Application]:
        """List the guild's applications (dashboard data)."""
        docs = await self._db.list_applications(self._mod, guild_id, state)
        return [_application_from_doc(doc) for doc in docs]

    async def dashboard(self, guild_id: str) -> dict[str, Any]:
        """Build the admin dashboard data: candidates, enrolled, refused."""
        applications = await self.list_applications(guild_id)
        pending = [a for a in applications if a.state == STATE_PENDING]
        accepted = [a for a in applications if a.state == STATE_ACCEPTED]
        refused = [a for a in applications if a.state == STATE_REFUSED]
        return {
            "mod": self._mod,
            "guild_id": guild_id,
            "pending": pending,
            "accepted": accepted,
            "refused": refused,
            "total": len(applications),
        }

    async def _evaluate(self, application: Application) -> tuple[CriterionResult, ...]:
        """Run the declared automatic criteria against the player's facts."""
        if not self._criteria or self._stats is None:
            return ()
        results: list[CriterionResult] = []
        for key, value in self._criteria.items():
            if key == CRITERION_MANUAL:
                continue
            results.append(await self._evaluate_one(key, value, application))
        return tuple(results)

    async def _evaluate_one(self, key: str, value: Any, application: Application) -> CriterionResult:
        """Evaluate one criterion; an unreadable stat degrades to pending."""
        stats = self._stats
        if stats is None:
            raise ValueError("no stats seam wired — criteria cannot evaluate")
        user_id, game = application.user_id, self._game_key
        if key == CRITERION_MIN_ELO:
            elo = await stats.player_elo(user_id, game)
            if elo is None:
                return CriterionResult(key, False, f"rating unknown for {game or 'the game'} — manual review")
            passed = elo >= int(value)
            return CriterionResult(key, passed, f"rating {elo} {'≥' if passed else '<'} required {value}")
        if key == CRITERION_MIN_MATCHES:
            spec = value if isinstance(value, dict) else {}
            days, count = int(spec.get("days", 30)), int(spec.get("count", 0))
            played = await stats.matches_in_window(user_id, game, days)
            if played is None:
                return CriterionResult(key, False, "match history unknown — manual review")
            passed = played >= count
            return CriterionResult(
                key, passed, f"{played} matches in {days}d {'≥' if passed else '<'} required {count}"
            )
        if key == CRITERION_REQUIRES_PROFILE:
            has = await stats.has_profile(user_id, game)
            reason = "game profile bound" if has else f"no bound profile for {game or 'the game'}"
            return CriterionResult(key, has, reason)
        raise ValueError(f"unknown criterion {key!r}")

    async def _require(self, application_id: str) -> Application:
        """Load one application; fail loudly when unknown."""
        doc = await self._db.find_application(application_id)
        if doc is None:
            raise KeyError(f"application {application_id!r} not found")
        return _application_from_doc(doc)

    async def _decide(
        self, application: Application, state: str, now: int = 0, reason: str = "", reasons: list[str] | None = None
    ) -> Application:
        """Apply the decision and persist it; accept registers the member."""
        decided = Application(
            mod=application.mod,
            guild_id=application.guild_id,
            user_id=application.user_id,
            display_name=application.display_name,
            state=state,
            season=application.season,
            applied_at=application.applied_at,
            decided_at=now,
            reasons=tuple(reasons) if reasons is not None else ((reason,) if reason else ()),
            criteria_results=application.criteria_results,
        )
        await self._db.upsert_application(_application_to_doc(decided))
        if state == STATE_ACCEPTED and self._membership is not None:
            try:
                await self._membership.register(application.guild_id, application.user_id, application.display_name)
            except Exception:
                logger.exception(
                    "ENROLLMENT member registration failed (mod %s, user %s) — best-effort",
                    self._mod,
                    application.user_id,
                )
        logger.info(
            "ENROLLMENT %s %s (mod %s, user %s)",
            state, "by criteria" if not reason else f"({reason})", self._mod, application.user_id,
        )
        return decided


def _application_to_doc(application: Application) -> dict[str, Any]:
    """Serialize an application for persistence."""
    return {
        "_id": application.application_id,
        "mod": application.mod,
        "guild_id": application.guild_id,
        "user_id": application.user_id,
        "display_name": application.display_name,
        "state": application.state,
        "season": application.season,
        "applied_at": application.applied_at,
        "decided_at": application.decided_at,
        "reasons": list(application.reasons),
        "criteria_results": [
            {"key": r.key, "passed": r.passed, "reason": r.reason} for r in application.criteria_results
        ],
    }


def _application_from_doc(doc: dict[str, Any]) -> Application:
    """Rebuild an application from its persisted form."""
    return Application(
        mod=str(doc.get("mod", "")),
        guild_id=str(doc.get("guild_id", "")),
        user_id=str(doc.get("user_id", "")),
        display_name=str(doc.get("display_name", "")),
        state=str(doc.get("state", STATE_PENDING)),
        season=doc.get("season"),
        applied_at=int(doc.get("applied_at", 0) or 0),
        decided_at=doc.get("decided_at"),
        reasons=tuple(str(r) for r in (doc.get("reasons") or ())),
        criteria_results=tuple(
            CriterionResult(
                key=str(r.get("key", "")),
                passed=bool(r.get("passed", False)),
                reason=str(r.get("reason", "")),
            )
            for r in (doc.get("criteria_results") or ())
        ),
    )
