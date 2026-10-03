"""Ladder admin surface: creation wizard validation, settings, audit (kingdoms-services#141).

Discord-first but platform-agnostic: the Discord adapter renders the
wizard; this module owns the validation rules and the audit. The
non-negotiable of v0.4.0: **exactly one ladder per guild** (mono-guild
ladders) — identities are multi-guild, ladders are not. Creation is
refused with a clear error and audited, success is audited too.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kingdoms.mods.ladder.models import (
    LADDERS_COLLECTION,
    LadderModel,
    LadderSettingsModel,
)
from kingdoms.mods.ladder.service import LadderService

logger = logging.getLogger("kingdoms.mods.ladder.admin")

SETTINGS_BOUNDS: dict[str, tuple[int, int]] = {
    "base_elo_threshold": (1, 1000),
    "elo_threshold_increment": (0, 500),
    "increment_interval": (1, 3600),
    "elo_threshold_max": (1, 2000),
    "matchmaking_tick_interval": (1, 300),
    "ready_timeout": (10, 3600),
    "elo_initial": (100, 5000),
    "elo_floor": (0, 5000),
    "elo_k_provision_match_count": (0, 100),
    "elo_k_newbie": (1, 200),
    "elo_k_standard": (1, 200),
    "elo_max_gain": (1, 500),
    "elo_max_loss": (1, 500),
    "player_fav_count": (0, 20),
    "player_ban_count": (0, 20),
    "random_ban_count": (0, 20),
    "match_surface_cleanup_delay": (0, 86400),
}


class LadderAdminAudit(Protocol):
    """Admin-audit seam: every admin mutation is recorded."""

    async def record(self, action: str, payload: dict[str, Any]) -> None:
        """Persist one admin-audit line."""
        ...


class LadderAlreadyExistsError(ValueError):
    """The guild already has a ladder (mono-guild rule)."""


class InvalidSettingsError(ValueError):
    """A settings value is out of bounds or inconsistent."""


class LadderAdminService:
    """Admin operations on ladders: create, configure, with validation + audit."""

    def __init__(self, service: LadderService, audit: LadderAdminAudit | None = None) -> None:
        """Wire the domain service and the audit seam."""
        self._svc = service
        self._audit = audit

    async def create_ladder(
        self, owner_ref: str, name: str, game_key: str, available_games: list[str], now: int = 0
    ) -> LadderModel:
        """Create the guild's ladder after the wizard's validations.

        Validations: the game is available, the name is non-empty, and
        the guild has no ladder yet (any game — the mono-guild rule).
        """
        if game_key not in available_games:
            raise ValueError(f"game {game_key!r} is not available")
        if not name.strip():
            raise ValueError("ladder name must not be empty")
        for game in available_games:
            existing = await self._svc._db.find_ladder_by_owner(owner_ref, game)
            if existing is not None:
                raise LadderAlreadyExistsError(f"guild {owner_ref!r} already has a ladder")
        ladder = await self._svc.create_ladder(owner_ref, name.strip(), game_key, now=now)
        await self._audit_record("ladder.create", {"owner_ref": owner_ref, "name": name, "game_key": game_key})
        return ladder

    async def update_settings(self, ladder_id: str, admin_user_id: str, changes: dict[str, Any]) -> LadderModel:
        """Apply validated settings changes (bounds + consistency), audited."""
        ladder = await self._svc.get_ladder(ladder_id)
        if ladder is None:
            raise ValueError(f"unknown ladder {ladder_id!r}")
        current = ladder.settings.model_dump()
        for key, value in changes.items():
            if key not in SETTINGS_BOUNDS and not isinstance(value, bool):
                raise InvalidSettingsError(f"unknown setting {key!r}")
            if key in SETTINGS_BOUNDS:
                low, high = SETTINGS_BOUNDS[key]
                if not isinstance(value, int) or not low <= value <= high:
                    raise InvalidSettingsError(f"{key} must be an int in [{low}, {high}]")
        updated = {**current, **changes}
        settings = LadderSettingsModel.model_validate(updated)
        self._check_consistency(settings)
        ladder = ladder.model_copy(update={"settings": settings})
        await self._svc._db.upsert_entry(LADDERS_COLLECTION, ladder.to_mongo())
        await self._audit_record(
            "ladder.settings.update", {"ladder_id": ladder_id, "admin_user_id": admin_user_id, "diff": changes}
        )
        return ladder

    @staticmethod
    def _check_consistency(settings: LadderSettingsModel) -> None:
        """Cross-field consistency rules the wizard also enforces client-side."""
        if settings.elo_floor > settings.elo_initial:
            raise InvalidSettingsError("elo_floor must be <= elo_initial")
        if settings.elo_k_standard > settings.elo_k_newbie:
            raise InvalidSettingsError("elo_k_standard must be <= elo_k_newbie")
        if settings.base_elo_threshold > settings.elo_threshold_max:
            raise InvalidSettingsError("base_elo_threshold must be <= elo_threshold_max")

    async def _audit_record(self, action: str, payload: dict[str, Any]) -> None:
        """Write one audit line (best-effort)."""
        if self._audit is None:
            return
        try:
            await self._audit.record(action, payload)
        except Exception:
            logger.warning("AUDIT WRITE FAILED (%s)", action, exc_info=True)
