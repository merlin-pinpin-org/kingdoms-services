"""Registration service: profile binding + game-profile validation (kingdoms-services#133).

Registration is a *permanent* mod (profile + validation, once): it binds a
platform user to game profiles. Seasonal binding (per-ladder enrollment)
is the ladder mod's business. Profile bindings are stored game-side
(``games/<game>/`` collections, reference §0/§6) — never in ladder
collections — and validation is delegated to the game seam
(``validate_profile``), so the core stays game-agnostic.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.registration")

PROFILE_BINDINGS_COLLECTION = "profile_bindings"
REGISTRATIONS_COLLECTION = "registrations"


class RegistrationDatabase(Protocol):
    """Narrow async MongoDB seam the RegistrationService depends on."""

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        """Insert or replace one document by ``_id``."""
        ...

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        """Return one document by ``_id``; None when absent."""
        ...

    async def find_user_bindings(self, user_id: str) -> list[dict[str, Any]]:
        """List the game-profile bindings of a user (all games)."""
        ...

    async def find_binding_by_profile(self, game_key: str, profile_id: str) -> dict[str, Any] | None:
        """Return the binding owning a profile; None when unbound."""
        ...

    async def delete_binding(self, game_key: str, user_id: str, profile_id: str) -> bool:
        """Drop one profile binding; True when one existed."""
        ...


class GameProfileSeam(Protocol):
    """Game-side validation seam (games/aoe2 adapter implements this)."""

    async def validate_profile(self, profile_id: str) -> dict[str, Any] | None:
        """Validate a game profile id; return its public data, None when invalid."""
        ...


class RegistrationEvents(Protocol):
    """Notification-intent seam (registration.requested/accepted/refused)."""

    async def emit(self, intent: str, payload: dict[str, Any]) -> None:
        """Emit one notification intent (best-effort)."""
        ...


class RegistrationAudit(Protocol):
    """Narrow audit seam: every registration mutation is recorded."""

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        """Persist one admin-audit line."""
        ...


class InvalidProfileError(ValueError):
    """The game seam rejected the profile id (unresolvable/unknown)."""


class ProfileBoundError(ValueError):
    """The profile is already bound to another user."""


class RegistrationService:
    """Bind platform users to validated game profiles (permanent registration)."""

    def __init__(
        self,
        database: RegistrationDatabase,
        profile_seams: dict[str, GameProfileSeam] | None = None,
        events: RegistrationEvents | None = None,
        audit: RegistrationAudit | None = None,
    ) -> None:
        """Wire persistence, per-game validation seams and event/audit seams."""
        self._db = database
        self._seams = profile_seams or {}
        self._events = events
        self._audit = audit

    async def bind_profile(self, user_id: str, game_key: str, profile_id: str) -> dict[str, Any]:
        """Validate then bind a game profile to a user (permanent, once per profile).

        Validation is delegated to the game seam; a profile already bound
        to another user is refused. The binding is stored game-side.
        """
        seam = self._seams.get(game_key)
        if seam is None:
            raise ValueError(f"unknown game {game_key!r}: no profile seam registered")
        validated = await seam.validate_profile(profile_id)
        if validated is None:
            raise InvalidProfileError(f"profile {profile_id!r} is not valid for game {game_key!r}")
        owner = await self._db.find_binding_by_profile(game_key, profile_id)
        if owner is not None and owner.get("user_id") != user_id:
            raise ProfileBoundError(f"profile {profile_id!r} is already bound to {owner.get('user_id')!r}")
        binding = {
            "_id": f"binding:{game_key}:{user_id}:{profile_id}",
            "user_id": user_id,
            "game_key": game_key,
            "profile_id": profile_id,
            "profile": validated,
            "bound_at": _now_ms(),
        }
        await self._db.upsert_entry(PROFILE_BINDINGS_COLLECTION, binding)
        await self._audit_record("profile.bind", {"user_id": user_id, "game_key": game_key, "profile_id": profile_id})
        return binding

    async def get_binding(self, user_id: str, game_key: str) -> dict[str, Any] | None:
        """Return the user's first binding for a game; None when unbound.

        Multiple profiles per game are supported; callers that need them
        all use ``list_bindings_for_game``/``list_bindings``.
        """
        bindings = await self._db.find_user_bindings(user_id)
        for binding in bindings:
            if binding.get("game_key") == game_key:
                return binding
        return None

    async def list_bindings(self, user_id: str) -> list[dict[str, Any]]:
        """List all of a user's game-profile bindings."""
        return await self._db.find_user_bindings(user_id)

    async def unlink_profile(self, user_id: str, game_key: str, profile_id: str) -> bool:
        """Remove one profile binding; True when one existed.

        Multi-profile by design: a user may hold several bindings per
        game; unlinking one never touches the others.
        """
        removed = await self._db.delete_binding(game_key, user_id, profile_id)
        if removed:
            await self._audit_record(
                "profile.unbind", {"user_id": user_id, "game_key": game_key, "profile_id": profile_id}
            )
        return removed

    async def has_any_profile(self, user_id: str) -> bool:
        """Ladder join precondition: at least one linked game profile."""
        return bool(await self._db.find_user_bindings(user_id))

    async def request_enrollment(self, user_id: str, guild_id: str) -> dict[str, Any]:
        """Record an enrollment request and notify admins (acceptance is admin-side)."""
        request = {
            "_id": f"registration:{guild_id}:{user_id}",
            "user_id": user_id,
            "guild_id": guild_id,
            "requested_at": _now_ms(),
            "status": "pending",
        }
        await self._db.upsert_entry(REGISTRATIONS_COLLECTION, request)
        await self._emit("registration.requested", {"user_id": user_id, "guild_id": guild_id})
        await self._audit_record("registration.request", {"user_id": user_id, "guild_id": guild_id})
        return request

    # ── helpers ──────────────────────────────────────────────────────────

    async def _emit(self, intent: str, payload: dict[str, Any]) -> None:
        """Emit one notification intent (best-effort, never raises)."""
        if self._events is None:
            return
        try:
            await self._events.emit(intent, payload)
        except Exception:
            logger.warning("REGISTRATION EVENT EMIT FAILED (%s)", intent, exc_info=True)

    async def _audit_record(self, action: str, payload: dict[str, Any]) -> None:
        """Write one audit line (best-effort: a store failure logs, never raises)."""
        if self._audit is None:
            return
        try:
            await self._audit.record(action, payload)
        except Exception:
            logger.warning("AUDIT WRITE FAILED (%s)", action, exc_info=True)


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)
