"""Kingdoms mod season & enrollment service (kingdoms-services#157, T2).

The domain rules of reference §3-§5 and decisions D21-D26/D38: manual
admin launch, wholesale reset, King/Lord enrollment, the waiting queue,
departures with a reason and admin-driven replacements that inherit the
weekly attack/defense budgets (the D23 anti-abuse rule).

The service is storage-agnostic (the ``KingdomsStore`` Protocol); every
mutation goes through it. The i18n of the user-facing failures is the
surface's concern: each error carries a ``message_key`` the Discord
command resolves through its designer-authored FR/EN catalog.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import ClassVar

from kingdoms.core.exceptions import ErrorContext, KingdomsError
from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    KingdomModel,
    KingdomType,
    LordModel,
    LordRole,
    SeasonState,
)
from kingdoms.mods.kingdoms.storage import KingdomsStore

logger = logging.getLogger("kingdoms.kingdoms_service")

KING_ROLE = LordRole.KING
LORD_ROLE = LordRole.LORD


class KingdomsModError(KingdomsError):
    """Base of the mod errors — the surface localizes ``message_key``."""

    code = "KINGDOMS_MOD_ERROR"
    message_key: ClassVar[str] = "kingdoms.errors.unexpected"

    def __init__(self, message: str, **context: str) -> None:
        """Create the error with serializable context entries."""
        super().__init__(message, ErrorContext().extend(**context) if context else None)


class NoSeasonError(KingdomsModError):
    """Raised when an action needs a running season and none is."""

    code = "KINGDOMS_NO_SEASON"
    message_key = "kingdoms.errors.no_season"


class AlreadyEnrolledError(KingdomsModError):
    """Raised when a player is already enrolled in the current season."""

    code = "KINGDOMS_ALREADY_ENROLLED"
    message_key = "kingdoms.errors.already_enrolled"


class KingdomNameInvalidError(KingdomsModError):
    """Raised when a proposed kingdom name breaks the configurable rules (D21)."""

    code = "KINGDOMS_NAME_INVALID"
    message_key = "kingdoms.errors.name_invalid"


class KingdomLimitError(KingdomsModError):
    """Raised when the season already counts its maximum of kingdoms."""

    code = "KINGDOMS_KINGDOM_LIMIT"
    message_key = "kingdoms.errors.kingdom_limit"


class ImposedKingdomsError(KingdomsModError):
    """Raised when a player tries to found a kingdom in an imposed season (D21)."""

    code = "KINGDOMS_IMPOSED"
    message_key = "kingdoms.errors.imposed"


class KingdomNotFoundError(KingdomsModError):
    """Raised when the named kingdom does not exist in the season."""

    code = "KINGDOMS_KINGDOM_NOT_FOUND"
    message_key = "kingdoms.errors.kingdom_not_found"


class KingdomFullError(KingdomsModError):
    """Raised when a kingdom already counts its maximum of lords (D22)."""

    code = "KINGDOMS_KINGDOM_FULL"
    message_key = "kingdoms.errors.kingdom_full"


class NotEnrollableError(KingdomsModError):
    """Raised when the target kingdom cannot enroll players (Gaïa, D32)."""

    code = "KINGDOMS_NOT_ENROLLABLE"
    message_key = "kingdoms.errors.not_enrollable"


class NotQueuedError(KingdomsModError):
    """Raised when the admin assigns a player who is not waiting in the queue."""

    code = "KINGDOMS_NOT_QUEUED"
    message_key = "kingdoms.errors.not_queued"


class ReplacementError(KingdomsModError):
    """Raised when a replacement target has not left the season (D23)."""

    code = "KINGDOMS_REPLACEMENT"
    message_key = "kingdoms.errors.replacement"


def _season_id(now: datetime) -> str:
    """Build the season id from its launch timestamp."""
    return f"s-{now:%Y%m%d-%H%M%S}"


class KingdomsService:
    """Season lifecycle and enrollment rules over the store (reference §3-§5)."""

    def __init__(self, store: KingdomsStore, config: KingdomsSeasonConfig) -> None:
        """Store the seam and the validated season configuration."""
        self._store = store
        self._config = config

    async def launch(self, imposed_names: list[str] | None = None) -> SeasonState:
        """Launch a new season: wholesale reset, then the fresh state (D38).

        ``imposed_names`` switches to the imposed-kingdoms mode (D21):
        the admin-defined kingdoms exist up front and players may not
        found their own. Gaïa is always seeded (D32).
        """
        names = [name.strip() for name in imposed_names or [] if name.strip()]
        for name in names:
            self._check_name(name)
        if len(names) > self._config.kingdoms_count:
            raise KingdomLimitError("more imposed kingdoms than the configured maximum")
        await self._store.wipe_season_data()
        now = datetime.now(tz=UTC)
        season = SeasonState(
            _id=_season_id(now),
            started_at=now,
            weeks=self._config.weeks,
            current_cycle=0,
            current_age_key=self._config.ages[0].key if self._config.ages else "dark_age",
            imposed_kingdoms=bool(names),
        )
        await self._store.upsert_season(season.to_mongo())
        kingdoms = [self._new_kingdom(GAIA_KINGDOM_KEY, KingdomType.GAIA, season.id, name_approved=True)]
        for index, name in enumerate(names):
            kingdoms.append(self._new_kingdom(f"k-{index + 1}", KingdomType.PLAYER, season.id, name=name))
        for kingdom in kingdoms:
            await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms: season %s launched (imposed=%s)", season.id, bool(names))
        return season

    async def reset(self) -> None:
        """Reset the season data without launching anything (reference §3.3)."""
        await self._store.wipe_season_data()
        logger.info("kingdoms: season data reset")

    async def current_season(self) -> SeasonState | None:
        """Return the latest launched season; None before the first launch."""
        seasons = await self._store.find_seasons()
        return SeasonState.from_mongo(seasons[-1]) if seasons else None

    async def kingdoms(self) -> list[KingdomModel]:
        """Return the kingdoms of the current data set."""
        return [KingdomModel.from_mongo(doc) for doc in await self._store.find_kingdoms()]

    async def lords(self) -> list[LordModel]:
        """Return the lords of the current data set."""
        return [LordModel.from_mongo(doc) for doc in await self._store.find_lords()]

    async def enroll(
        self,
        player_id: str,
        display_name: str,
        role: LordRole,
        kingdom_name: str | None = None,
        proposed_name: str | None = None,
    ) -> LordModel:
        """Enroll a player as King or Lord (reference §5, D22).

        A King founds a kingdom with a name pending admin validation
        (D21) — refused in the imposed mode. A Lord joins the named
        kingdom, or waits in the queue when no kingdom is given (D23).
        """
        season = await self._require_season()
        existing = await self._find_lord(player_id)
        if existing is not None and not existing.left:
            raise AlreadyEnrolledError("player already enrolled in the current season")
        if role is KING_ROLE:
            return await self._enroll_king(season, player_id, display_name, proposed_name)
        return await self._enroll_lord(season, player_id, display_name, kingdom_name)

    async def leave(self, player_id: str, reason: str = "") -> None:
        """Mark a player as having left the season (D23, reason recorded).

        The budgets stay on the document until an admin replaces the
        player: a replacement inherits the weekly attack/defense state.
        """
        lord = await self._require_lord(player_id)
        lord.left = True
        lord.left_reason = reason or None
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms: player %s left the season (%s)", player_id, reason)

    async def assign(self, player_id: str, kingdom_name: str, role: LordRole) -> LordModel:
        """Assign a queued player to a kingdom (admin action, D23)."""
        await self._require_season()
        lord = await self._require_lord(player_id)
        if not lord.in_queue:
            raise NotQueuedError("the player is not waiting in the queue")
        kingdom = await self._find_kingdom_by_name(kingdom_name)
        if kingdom.is_gaia:
            raise NotEnrollableError("Gaïa kingdoms are never enrollable")
        await self._check_capacity(kingdom, role)
        lord.kingdom_id = kingdom.id
        lord.role = role
        lord.in_queue = False
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms: queued player %s assigned to %s", player_id, kingdom.name)
        return lord

    async def add_kingdom(self, name: str) -> KingdomModel:
        """Add a kingdom manually to the running season (admin action).

        The kingdom is created approved, up to the configured maximum.
        """
        season = await self._require_season()
        self._check_name(name)
        kingdoms = await self.kingdoms()
        if len([k for k in kingdoms if not k.is_gaia]) >= self._config.kingdoms_count:
            raise KingdomLimitError("the season already counts its maximum of kingdoms")
        if any(k.name.casefold() == name.strip().casefold() for k in kingdoms):
            raise KingdomNameInvalidError("a kingdom with this name already exists")
        kingdom = self._new_kingdom(
            f"k-{len(kingdoms)}",
            KingdomType.PLAYER,
            season.id,
            name=name.strip(),
            name_approved=True,
        )
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms: kingdom %s added manually by an admin", kingdom.name)
        return kingdom

    async def replace(self, outgoing_player_id: str, incoming_player_id: str) -> LordModel:
        """Replace a player who left with a queued player (D23/D25).

        The replacement inherits the weekly attack/defense budgets of
        the outgoing player (anti-abus: an already-spent attack stays
        spent) but never their marriage (D25 — person-linked only).
        """
        await self._require_season()
        outgoing = await self._require_lord(outgoing_player_id)
        if not outgoing.left:
            raise ReplacementError("the outgoing player has not left the season")
        incoming = await self._require_lord(incoming_player_id)
        if not incoming.in_queue:
            raise NotQueuedError("the incoming player is not waiting in the queue")
        incoming.kingdom_id = outgoing.kingdom_id
        incoming.role = outgoing.role
        incoming.attack_used = outgoing.attack_used
        incoming.defense_used = outgoing.defense_used
        incoming.in_queue = False
        await self._store.upsert_lord(incoming.to_mongo())
        await self._store.delete_lord(outgoing.id)
        logger.info(
            "kingdoms: player %s replaces %s (budgets %s/%s)",
            incoming_player_id,
            outgoing_player_id,
            incoming.attack_used,
            incoming.defense_used,
        )
        return incoming

    async def decide_name(self, kingdom_name: str, approved: bool) -> KingdomModel:
        """Approve or refuse a proposed kingdom name (D21).

        A refused name falls back to a neutral placeholder the admin can
        live with — the kingdom exists and must remain addressable.
        """
        kingdom = await self._find_kingdom_by_name(kingdom_name)
        if not approved:
            kingdom.name = f"kingdom-{kingdom.id}"
        kingdom.name_approved = True
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms: kingdom %s name approved=%s", kingdom.id, approved)
        return kingdom


    @property
    def config(self) -> KingdomsSeasonConfig:
        """Expose the validated season configuration to the read-only surfaces."""
        return self._config

    async def resolve_kingdom(self, kingdom_name: str | None, player_id: str) -> KingdomModel | None:
        """Resolve the kingdom to display: by name, else the player's own."""
        kingdoms = await self.kingdoms()
        if kingdom_name:
            wanted = kingdom_name.strip().casefold()
            return next((k for k in kingdoms if k.name.casefold() == wanted), None)
        lords = await self.lords()
        lord = next((item for item in lords if item.id == player_id and not item.left), None)
        if lord is None or lord.kingdom_id is None:
            return None
        return next((k for k in kingdoms if k.id == lord.kingdom_id), None)

    async def _enroll_king(
        self,
        season: SeasonState,
        player_id: str,
        display_name: str,
        proposed_name: str | None,
    ) -> LordModel:
        """Found a kingdom and become its King (D21/D22)."""
        if season.imposed_kingdoms:
            raise ImposedKingdomsError("kingdoms are imposed for this season")
        if not proposed_name:
            raise KingdomNameInvalidError("a founding King must propose a name")
        self._check_name(proposed_name)
        kingdoms = await self.kingdoms()
        if len([k for k in kingdoms if not k.is_gaia]) >= self._config.kingdoms_count:
            raise KingdomLimitError("the season already counts its maximum of kingdoms")
        kingdom = self._new_kingdom(
            f"k-{len(kingdoms)}",
            KingdomType.PLAYER,
            season.id,
            name=proposed_name,
            name_approved=False,
        )
        await self._store.upsert_kingdom(kingdom.to_mongo())
        lord = LordModel(
            _id=player_id,
            season_id=season.id,
            kingdom_id=kingdom.id,
            role=KING_ROLE,
            display_name=display_name,
        )
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms: %s founded %s as King", player_id, kingdom.name)
        return lord

    async def _enroll_lord(
        self,
        season: SeasonState,
        player_id: str,
        display_name: str,
        kingdom_name: str | None,
    ) -> LordModel:
        """Join a kingdom as Lord, or wait in the queue (D22/D23)."""
        if not kingdom_name:
            lord = LordModel(
                _id=player_id,
                season_id=season.id,
                role=LORD_ROLE,
                display_name=display_name,
                in_queue=True,
            )
            await self._store.upsert_lord(lord.to_mongo())
            logger.info("kingdoms: %s joined the waiting queue", player_id)
            return lord
        kingdom = await self._find_kingdom_by_name(kingdom_name)
        if kingdom.is_gaia:
            raise NotEnrollableError("Gaïa kingdoms are never enrollable")
        await self._check_capacity(kingdom, LORD_ROLE)
        lord = LordModel(
            _id=player_id,
            season_id=season.id,
            kingdom_id=kingdom.id,
            role=LORD_ROLE,
            display_name=display_name,
        )
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms: %s joined %s as Lord", player_id, kingdom.name)
        return lord

    async def _check_capacity(self, kingdom: KingdomModel, role: LordRole) -> None:
        """Enforce the configurable lord capacity of a kingdom (D22)."""
        if role is KING_ROLE:
            return
        lords = await self.lords()
        members = [lord for lord in lords if lord.kingdom_id == kingdom.id and not lord.left]
        subjects = [lord for lord in members if lord.role is not KING_ROLE]
        if len(subjects) >= self._config.lords_per_kingdom:
            raise KingdomFullError("the kingdom already counts its maximum of lords")

    def _check_name(self, name: str) -> None:
        """Validate a kingdom name against the configurable rules (D21)."""
        rules = self._config.names
        if not rules.min_length <= len(name) <= rules.max_length:
            raise KingdomNameInvalidError("the name length breaks the configured rules")
        if re.fullmatch(rules.pattern, name) is None:
            raise KingdomNameInvalidError("the name characters break the configured rules")

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        return season

    async def _require_lord(self, player_id: str) -> LordModel:
        """Return the player's lord document or raise a not-found error."""
        lord = await self._find_lord(player_id)
        if lord is None:
            raise KingdomNotFoundError("the player is not enrolled in the current season")
        return lord

    async def _find_lord(self, player_id: str) -> LordModel | None:
        """Return the lord document of a player; None when absent."""
        lords = await self.lords()
        return next((lord for lord in lords if lord.id == player_id), None)

    async def _find_kingdom_by_name(self, name: str) -> KingdomModel:
        """Resolve a kingdom by display name (case-insensitive)."""
        kingdoms = await self.kingdoms()
        found = next(
            (kingdom for kingdom in kingdoms if kingdom.name.casefold() == name.strip().casefold()),
            None,
        )
        if found is None:
            raise KingdomNotFoundError("no kingdom with this name in the current season")
        return found

    def _new_kingdom(
        self,
        kingdom_id: str,
        kind: KingdomType,
        season_id: str,
        *,
        name: str | None = None,
        name_approved: bool = True,
    ) -> KingdomModel:
        """Build one kingdom document (Gaïa keeps its reserved name)."""
        capacity = self._config.ages[0].extra_marriages if self._config.ages else 0
        return KingdomModel(
            _id=kingdom_id,
            season_id=season_id,
            type=kind,
            name=name if name is not None else GAIA_KINGDOM_KEY,
            name_approved=name_approved,
            marriage_capacity=0 if kind is KingdomType.GAIA else capacity,
        )
