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
import random
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from kingdoms.core.exceptions import ErrorContext, KingdomsError
from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    KingdomModel,
    KingdomType,
    KingdomValidation,
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
        await self._archive_current_data()
        await self._store.wipe_season_data()
        now = datetime.now(tz=UTC)
        season = SeasonState(
            _id=_season_id(now),
            started_at=now,
            weeks=self._config.weeks,
            current_cycle=0,
            current_age_key=self._config.ages[0].key if self._config.ages else "dark_age",
            imposed_kingdoms=bool(names),
            phase="setup",
        )
        await self._store.upsert_season(season.to_mongo())
        kingdoms = [self._new_kingdom(GAIA_KINGDOM_KEY, KingdomType.GAIA, season.id, name_approved=True)]
        for index, name in enumerate(names):
            kingdoms.append(self._new_kingdom(f"k-{index + 1}", KingdomType.PLAYER, season.id, name=name))
        for kingdom in kingdoms:
            # setup phase: NO starting draft — the civilizations are
            # drawn for everyone at once by start_season() (Drasah's
            # game-design rule: nothing random is revealed before the
            # admin actually starts the game).
            await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms: season %s launched (imposed=%s)", season.id, bool(names))
        return season

    async def start_season(self) -> SeasonState:
        """Start the game: reveal the random content to everyone (D70).

        The ``setup`` → ``started`` transition (Drasah's game-design
        phase rule): every non-Gaïa kingdom draws its starting
        civilizations at once (no duplicates between kingdoms), then
        the season is marked started — the surface then distributes
        the territories and reveals the Alliances/Territoire salons.
        Idempotent: calling it on an already-started season is a no-op.
        """
        season = await self._require_season()
        if season.phase != "setup":
            return season
        for kingdom in await self.kingdoms():
            if kingdom.is_gaia or kingdom.civilizations:
                continue
            await self._grant_starting_civilizations(kingdom)
            await self._store.upsert_kingdom(kingdom.to_mongo())
        season.phase = "started"
        await self._store.upsert_season(season.to_mongo())
        logger.info("kingdoms: season %s started by an admin", season.id)
        return season

    async def set_imposed_mode(self, imposed: bool) -> SeasonState:
        """Flip the imposed-kingdoms mode mid-season (Drasah's rule).

        A mis-launched season must be fixable in one click without
        wiping anything: imposed → free unblocks the King candidatures
        (an imposed season refuses every king enrollment), free →
        imposed freezes them. The kingdoms themselves never change —
        the admin adds/removes them by hand if needed.
        """
        season = await self._require_season()
        season.imposed_kingdoms = bool(imposed)
        await self._store.upsert_season(season.to_mongo())
        logger.info("kingdoms: season %s imposed mode set to %s", season.id, bool(imposed))
        return season

    async def reset(self) -> None:
        """Reset the season data without launching anything (reference §3.3)."""
        await self._archive_current_data()
        await self._store.wipe_season_data()
        logger.info("kingdoms: season data reset")

    async def _find_all(self, finder: str) -> list[dict[str, Any]]:
        """Read a store collection defensively (optional on minimal stores)."""
        method = getattr(self._store, finder, None)
        if method is None:
            return []
        try:
            return list(await method())
        except Exception:
            logger.warning("kingdoms: archive read %s failed", finder, exc_info=True)
            return []

    async def _delete_one(self, deleter: str, document_id: str) -> None:
        """Delete one document defensively (optional on minimal stores)."""
        method = getattr(self._store, deleter, None)
        if method is None:
            return
        await method(document_id)

    async def _archive_current_data(self) -> None:
        """Snapshot the whole current season data into season_archives.

        Runs before every wipe (launch and reset): the data of the
        season that is about to disappear is archived verbatim in the
        ``kingdoms_season_archives`` collection (Drasah's backup rule).
        """
        previous = await self.current_season()
        if previous is None and not await self._find_all("find_kingdoms"):
            return  # nothing to archive: the data set is already empty
        seasons = await self._find_all("find_seasons")
        timestamp = datetime.now(tz=UTC)
        label = previous.id if previous is not None else "orphaned"
        archive: dict[str, Any] = {
            "_id": f"archive-{label}-{timestamp:%Y%m%d-%H%M%S}",
            "archived_at": timestamp,
            "season_id": label,
            "seasons": seasons,
            "kingdoms": await self._find_all("find_kingdoms"),
            "lords": await self._find_all("find_lords"),
            "territories": await self._find_all("find_territories"),
            "technologies": await self._find_all("find_technologies"),
            "attacks": await self._find_all("find_attacks"),
            "showmatches": await self._find_all("find_showmatches"),
        }
        try:
            await self._store.upsert_season_archive(archive)
            logger.info("kingdoms: season %s archived before wipe", label)
        except Exception:
            logger.warning("kingdoms: season archive failed — wiping anyway", exc_info=True)

    async def season_archives(self) -> list[dict[str, Any]]:
        """Return every archived season snapshot (oldest first)."""
        return await self._find_all("find_season_archives")

    async def delete_kingdom(self, kingdom_id: str) -> KingdomModel:
        """Hard-delete one kingdom and all of its data (Drasah's rule).

        Unlike ``refuse`` (which keeps the document for the record),
        the deletion removes everything: the kingdom, its lords, its
        territories, its technology state and its attacks. The Discord
        structure is deleted by the surface after this call.
        """
        kingdom = await self._require_kingdom(kingdom_id)
        if kingdom.is_gaia:
            raise NotEnrollableError("Gaïa is never subject to deletion")
        for lord in await self.lords():
            if lord.kingdom_id == kingdom.id:
                await self._store.delete_lord(lord.id)
        for territory in await self._find_all("find_territories"):
            if territory.get("owner_kingdom_id") == kingdom.id:
                await self._delete_one("delete_territory", str(territory["_id"]))
        for technology in await self._find_all("find_technologies"):
            if technology.get("kingdom_id") == kingdom.id:
                await self._delete_one("delete_technology", str(technology["_id"]))
        for attack in await self._find_all("find_attacks"):
            if attack.get("attacker_kingdom_id") == kingdom.id or attack.get(
                "defender_kingdom_id"
            ) == kingdom.id:
                await self._delete_one("delete_attack", str(attack["_id"]))
        await self._store.delete_kingdom(kingdom.id)
        logger.info("kingdoms: kingdom %s deleted by an admin", kingdom.name)
        return kingdom

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
        if season.phase != "setup":
            # once the game is started there is no secret left to keep:
            # a kingdom added afterwards draws its hand immediately
            await self._grant_starting_civilizations(kingdom)
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

    async def approve_kingdom(self, kingdom_id: str) -> KingdomModel:
        """Validate a pending (or previously refused) kingdom (rule 35, D70).

        The kingdom's Discord structure is provisioned by the surface
        after this call — the service only flips the state.
        """
        kingdom = await self._require_kingdom(kingdom_id)
        if kingdom.is_gaia:
            raise NotEnrollableError("Gaïa is never subject to validation")
        if kingdom.validation is KingdomValidation.APPROVED:
            return kingdom  # idempotent: re-validating is a no-op
        kingdom.validation = KingdomValidation.APPROVED
        kingdom.name_approved = True
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms: kingdom %s approved by an admin", kingdom.id)
        return kingdom

    async def refuse_kingdom(self, kingdom_id: str) -> KingdomModel:
        """Refuse a pending kingdom: its members fall back to the queue.

        The document stays REFUSED for the record (the admin may later
        re-validate it with a corrected name — D70); every lord and king
        of the kingdom is moved to the waiting queue (kingdom_id None).
        """
        kingdom = await self._require_kingdom(kingdom_id)
        if kingdom.is_gaia:
            raise NotEnrollableError("Gaïa is never subject to validation")
        kingdom.validation = KingdomValidation.REFUSED
        await self._store.upsert_kingdom(kingdom.to_mongo())
        for lord in await self.lords():
            if lord.kingdom_id == kingdom.id and not lord.left:
                lord.kingdom_id = None
                lord.in_queue = True
                await self._store.upsert_lord(lord.to_mongo())
                logger.info("kingdoms: %s moved to the queue (kingdom refused)", lord.id)
        logger.info("kingdoms: kingdom %s refused by an admin", kingdom.id)
        return kingdom

    async def rename_kingdom(self, kingdom_id: str, new_name: str) -> KingdomModel:
        """Correct a kingdom's proposed name before/while validating (D70)."""
        kingdom = await self._require_kingdom(kingdom_id)
        self._check_name(new_name)
        kingdoms = await self.kingdoms()
        if any(k.id != kingdom.id and k.name.casefold() == new_name.casefold() for k in kingdoms):
            raise KingdomNameInvalidError("a kingdom with this name already exists")
        kingdom.name = new_name.strip()
        await self._store.upsert_kingdom(kingdom.to_mongo())
        logger.info("kingdoms: kingdom %s renamed to %s", kingdom.id, kingdom.name)
        return kingdom

    async def _require_kingdom(self, kingdom_id: str) -> KingdomModel:
        """Fetch one kingdom by id or raise (admin validation path)."""
        for kingdom in await self.kingdoms():
            if kingdom.id == kingdom_id:
                return kingdom
        raise KingdomNotFoundError(f"no kingdom with id {kingdom_id}")

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
        kingdom.validation = KingdomValidation.PENDING
        if season.phase != "setup":
            # started game: reveal at once (nothing to hide anymore)
            await self._grant_starting_civilizations(kingdom)
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

    async def enroll_king_awaiting_name(self, player_id: str, display_name: str) -> LordModel:
        """Enroll an approved King without a kingdom yet (D70, flow v2).

        The validated King waits in the queue until they reply with
        their kingdom's name — :meth:`found_kingdom` then creates the
        PENDING kingdom (rule 35).
        """
        season = await self._require_season()
        if season.imposed_kingdoms:
            raise ImposedKingdomsError("kingdoms are imposed for this season")
        existing = await self._find_lord(player_id)
        if existing is not None and not existing.left:
            raise AlreadyEnrolledError("player already enrolled in the current season")
        lord = LordModel(
            _id=player_id,
            season_id=season.id,
            role=KING_ROLE,
            display_name=display_name,
            in_queue=True,
        )
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms: %s enrolled as King awaiting a kingdom name", player_id)
        return lord

    async def found_kingdom(self, player_id: str, name: str) -> KingdomModel:
        """Create the PENDING kingdom of a King who replied with its name.

        Flow v2 (D70): the King was approved without a name, then sent
        the name back to the bot — the kingdom lands in the admin
        validation queue (rule 35), exactly like a proposed name at
        enrollment.
        """
        season = await self._require_season()
        if season.imposed_kingdoms:
            raise ImposedKingdomsError("kingdoms are imposed for this season")
        self._check_name(name)
        lord = await self._require_lord(player_id)
        if lord.role is not KING_ROLE or not lord.in_queue or lord.kingdom_id is not None:
            raise NotQueuedError("only a King awaiting a kingdom name can found one")
        kingdoms = await self.kingdoms()
        if len([k for k in kingdoms if not k.is_gaia]) >= self._config.kingdoms_count:
            raise KingdomLimitError("the season already counts its maximum of kingdoms")
        if any(k.name.casefold() == name.casefold() for k in kingdoms):
            raise KingdomNameInvalidError("a kingdom with this name already exists")
        kingdom = self._new_kingdom(
            f"k-{len(kingdoms)}",
            KingdomType.PLAYER,
            season.id,
            name=name,
            name_approved=False,
        )
        kingdom.validation = KingdomValidation.PENDING
        if season.phase != "setup":
            # started game: reveal at once (nothing to hide anymore)
            await self._grant_starting_civilizations(kingdom)
        await self._store.upsert_kingdom(kingdom.to_mongo())
        lord.kingdom_id = kingdom.id
        lord.in_queue = False
        await self._store.upsert_lord(lord.to_mongo())
        logger.info("kingdoms: %s founded %s as King (DM reply)", player_id, kingdom.name)
        return kingdom

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
        if kingdom.validation is KingdomValidation.REFUSED:
            raise KingdomNotFoundError("this kingdom was refused and cannot be joined")
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

    async def _grant_starting_civilizations(self, kingdom: KingdomModel) -> None:
        """Draw the season's starting civilizations for a fresh kingdom.

        Every newly created player kingdom draws
        ``starting_civilizations`` civilizations at random, without
        duplicates between kingdoms: the pool shrinks as the other
        kingdoms take theirs (draft decided with the game designer,
        2026-10-10). The acquisition conditions (CIVILIZATIONS.md) come
        later; until then the recalculation keeps a drawn civilization
        that has no condition attached. Gaïa never draws.
        """
        count = self._config.starting_civilizations
        if count <= 0 or kingdom.is_gaia:
            return
        taken = {civ for other in await self.kingdoms() for civ in other.civilizations}
        pool = [civ.key for civ in self._config.civilizations if civ.key not in taken]
        if len(pool) < count:
            logger.warning(
                "kingdoms: starting draft short - %s civilizations left for %s",
                len(pool),
                kingdom.id,
            )
        kingdom.civilizations = random.sample(pool, min(count, len(pool)))
        logger.info(
            "kingdoms: starting draft for %s - %s", kingdom.id, kingdom.civilizations
        )
