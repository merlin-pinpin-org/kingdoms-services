"""Ladder membership: the mod's derivation of the core membership.

The ladder binds its own registration business (LadderService) to the
core ``MembershipService`` hooks: registering on the ladder, leaving
the queue first, refusing a removal with a live match. The season
label comes from the ladder's active season (SeasonService).
"""
from __future__ import annotations

import logging
from typing import Any

from kingdoms.core.services.membership import MembershipResult
from kingdoms.core.services.season_roles import SeasonRolesService
from kingdoms.mods.ladder.service import LadderService

logger = logging.getLogger("kingdoms.ladder.membership")


def _now_ms() -> int:
    """Return the current epoch in milliseconds."""
    import time

    return int(time.time() * 1000)


class LadderMembershipHooks:
    """The ladder's MembershipHooks: register/unregister on its ladder."""

    def __init__(self, service: LadderService, ladder_id: str) -> None:
        self._service = service
        self._ladder_id = ladder_id

    async def register_member(self, guild_id: str, user_id: str, display_name: str) -> MembershipResult:
        """Register the player on the ladder (idempotent)."""
        del guild_id
        player = await self._service.register_player(self._ladder_id, user_id, display_name, now=_now_ms())
        return MembershipResult(ok=True, summary=f"rating initial {player.rating}")

    async def unregister_member(self, guild_id: str, user_id: str) -> MembershipResult:
        """Leave the queue, then remove the player (a live match refuses)."""
        del guild_id
        player = await self._service.get_player(self._ladder_id, user_id)
        if player is None:
            return MembershipResult(ok=False, summary="Tu n'es pas inscrit sur le ladder.")
        try:
            await self._service.leave_queue(self._ladder_id, user_id)
        except Exception:
            logger.debug("leave_queue before unregister was a no-op", exc_info=True)
        removed = await self._service.remove_player(self._ladder_id, user_id)
        if not removed:
            return MembershipResult(ok=False, summary="Impossible de te désinscrire (match en cours ?).")
        return MembershipResult(ok=True, summary="Désinscrit du ladder.")


class LadderSeasons:
    """The ladder's MembershipSeasons: its active season label."""

    def __init__(self, season_service: Any, ladder_id: str) -> None:
        self._season_service = season_service
        self._ladder_id = ladder_id

    async def active_season(self, guild_id: str) -> str | None:
        """Read the active season of the ladder (best-effort, 's1')."""
        del guild_id
        if self._season_service is None:
            return "s1"
        try:
            from kingdoms.core.services.season_roles import season_label

            season = await self._season_service.get_active_season(self._ladder_id)
            if season is None:
                return "s1"
            return season_label({"label": season.label, "name": season.name, "season_id": season.id})
        except Exception:
            logger.warning("LADDER season lookup failed — defaulting to s1", exc_info=True)
            return "s1"


def build_ladder_membership(
    service: LadderService,
    ladder_id: str,
    season_service: Any,
    season_roles: SeasonRolesService,
) -> Any:
    """Build the ladder's MembershipService (the core wiring)."""
    from kingdoms.core.services.membership import MembershipService

    return MembershipService(
        mod_name="ladder",
        hooks=LadderMembershipHooks(service, ladder_id),
        seasons=LadderSeasons(season_service, ladder_id),
        season_roles=season_roles,
    )
