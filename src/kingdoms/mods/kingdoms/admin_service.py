"""Kingdoms mod — the 🏰 Royaume admin operations over the D75 foundation.

Every operation of the Royaume panel (D75, item 1) as a journaled
domain action: a **mandatory reason**, a snapshot of the touched
documents *before* the mutation, the mutation itself, then the journal
entry carrying both snapshots — so ``rollback`` restores the exact
prior state, document by document.

The Discord concerns (confirmation before/after, the 📣 announcement,
the player DM, the panel buttons) are the surface's, delivered by the
next tranche of epic kingdoms-services#214 phase 1.2; this module is
platform-agnostic and never imports discord.

Snapshot keys are ``<collection>:<document id>``; the rollback
reapplies the ``before`` payloads through the store seams and deletes
the documents an action created (an ``after`` entry with no ``before``
counterpart). The store stays the single persistence seam.
"""
from __future__ import annotations

import logging
from typing import Any

from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    KingdomModel,
    LordModel,
    LordRole,
    SeasonState,
    TerritoryModel,
)
from kingdoms.mods.kingdoms.storage import (
    KINGDOMS_COLLECTION,
    LORDS_COLLECTION,
    SEASONS_COLLECTION,
    TERRITORIES_COLLECTION,
    KingdomsStore,
)

from kingdoms.mods.kingdoms.admin_journal import (
    AdminActionModel,
    AdminJournalService,
    AdminReasonRequiredError,
)
from kingdoms.mods.kingdoms.service import (
    KING_ROLE,
    LORD_ROLE,
    AlreadyEnrolledError,
    KingdomNameInvalidError,
    KingdomNotFoundError,
    KingdomsModError,
    KingdomsService,
    NoSeasonError,
)

logger = logging.getLogger("kingdoms.admin_service")

__all__ = [
    "AdminActionModel",
    "AdminReasonRequiredError",
    "EjectKingError",
    "FoundationWindowClosedError",
    "KingdomAdminService",
    "ReassignError",
    "ThroneSwapError",
]


class ReassignError(KingdomsModError):
    """Raised when a reassignment target is not a movable lord (D17)."""

    code = "KINGDOMS_REASSIGN"
    message_key = "kingdoms.errors.reassign"


class ThroneSwapError(KingdomsModError):
    """Raised when a throne swap target is not a lord of the kingdom."""

    code = "KINGDOMS_THRONE_SWAP"
    message_key = "kingdoms.errors.throne_swap"


class EjectKingError(KingdomsModError):
    """Raised when ejecting a King — the kingdom needs its King (D75)."""

    code = "KINGDOMS_EJECT_KING"
    message_key = "kingdoms.errors.eject_king"


class FoundationWindowClosedError(KingdomsModError):
    """Raised when the foundation rights are edited past the initial window."""

    code = "KINGDOMS_FOUNDATION_WINDOW_CLOSED"
    message_key = "kingdoms.errors.foundation_window_closed"


def _snap(kind: str, document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Key one snapshot payload by collection and document id."""
    return {f"{kind}:{document['_id']}": document}


class KingdomAdminService:
    """The journaled Royaume operations (D75 item 1) over the store."""

    def __init__(self, service: KingdomsService, store: KingdomsStore) -> None:
        """Wrap the domain service; both share the one store seam."""
        self._kingdoms = service
        self._store = store
        self._journal = AdminJournalService(store)

    @property
    def journal(self) -> AdminJournalService:
        """The shared journal — the panels list and roll back through it."""
        return self._journal

    # ------------------------------------------------------------------
    # roster: manual add, assignment, reassignment

    async def add_lord(
        self,
        player_id: str,
        display_name: str,
        role: LordRole,
        kingdom_name: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> LordModel:
        """Add a player manually to a kingdom (D75 — modal flow).

        The admin path bypasses the queue; the capacity rules still
        apply (the D75 gel). A King may only be added to a kingdom
        without an active King.
        """
        season = await self._require_season()
        kingdom = await self._find_kingdom(kingdom_name)
        existing = await self._find_lord(player_id)
        if existing is not None and not existing.left:
            raise AlreadyEnrolledError("player already enrolled in the current season")
        before: dict[str, dict[str, Any]] = {}
        if role is KING_ROLE and await self._has_king(kingdom.id):
            raise ThroneSwapError("the kingdom already has a King")
        await self._kingdoms._check_capacity(kingdom, role, season)
        lord = LordModel(
            _id=player_id,
            season_id=season.id,
            kingdom_id=kingdom.id,
            role=role,
            display_name=display_name,
        )
        await self._store.upsert_lord(lord.to_mongo())
        await self._record(
            "add_lord",
            actor_id,
            actor_name,
            reason,
            "lord",
            player_id,
            before,
            _snap(LORDS_COLLECTION, lord.to_mongo()),
        )
        return lord

    async def assign_queued(
        self,
        player_id: str,
        kingdom_name: str,
        role: LordRole,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> LordModel:
        """Assign a queued player to a kingdom (D23), journaled."""
        self._require_reason(reason)
        before_lord = await self._require_lord(player_id)
        before = _snap(LORDS_COLLECTION, before_lord.to_mongo())
        lord = await self._kingdoms.assign(player_id, kingdom_name, role)
        await self._record(
            "assign_queued",
            actor_id,
            actor_name,
            reason,
            "lord",
            player_id,
            before,
            _snap(LORDS_COLLECTION, lord.to_mongo()),
        )
        return lord

    async def reassign(
        self,
        player_id: str,
        kingdom_name: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> LordModel:
        """Move an enrolled lord to another kingdom (D17 lives here, D75).

        The role is kept, the capacity rules apply; a King is not a
        movable piece — the throne swap (or dissolution) handles them.
        """
        self._require_reason(reason)
        season = await self._require_season()
        lord = await self._require_lord(player_id)
        if lord.in_queue or lord.left:
            raise ReassignError("only an enrolled, active lord can be reassigned")
        if lord.role is KING_ROLE:
            raise ReassignError("a King is moved through a throne swap, not a reassignment")
        target = await self._find_kingdom(kingdom_name)
        if target.id == lord.kingdom_id:
            raise ReassignError("the lord already belongs to this kingdom")
        before = _snap(LORDS_COLLECTION, lord.to_mongo())
        await self._kingdoms._check_capacity(target, lord.role, season)
        lord.kingdom_id = target.id
        await self._store.upsert_lord(lord.to_mongo())
        await self._record(
            "reassign",
            actor_id,
            actor_name,
            reason,
            "lord",
            player_id,
            before,
            _snap(LORDS_COLLECTION, lord.to_mongo()),
        )
        return lord

    async def eject_to_queue(
        self,
        player_id: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> LordModel:
        """Eject a lord back to the waiting queue, role kept (D75)."""
        self._require_reason(reason)
        lord = await self._require_lord(player_id)
        if lord.in_queue or lord.left:
            raise ReassignError("the player is not an active member of a kingdom")
        if lord.role is KING_ROLE:
            raise EjectKingError("a kingdom keeps exactly one King — swap the throne first")
        before = _snap(LORDS_COLLECTION, lord.to_mongo())
        lord.kingdom_id = None
        lord.in_queue = True
        await self._store.upsert_lord(lord.to_mongo())
        await self._record(
            "eject_to_queue",
            actor_id,
            actor_name,
            reason,
            "lord",
            player_id,
            before,
            _snap(LORDS_COLLECTION, lord.to_mongo()),
        )
        return lord

    # ------------------------------------------------------------------
    # entities: create, rename, dissolve, throne swap

    async def create_kingdom(
        self,
        name: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> KingdomModel:
        """Create a kingdom manually (D75) — distinct from the presets.

        The territories the creation draws ride the ``after`` payload,
        so a rollback deletes the kingdom **and** its drawn maps.
        """
        self._require_reason(reason)
        kingdom = await self._kingdoms.add_kingdom(name)
        after = _snap(KINGDOMS_COLLECTION, kingdom.to_mongo())
        for territory in await self._territories():
            if territory.owner_kingdom_id == kingdom.id:
                after[f"{TERRITORIES_COLLECTION}:{territory.id}"] = territory.to_mongo()
        await self._record(
            "create_kingdom",
            actor_id,
            actor_name,
            reason,
            "kingdom",
            kingdom.id,
            {},
            after,
        )
        return kingdom

    async def rename_kingdom(
        self,
        kingdom_name: str,
        new_name: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> KingdomModel:
        """Rename a kingdom (D75 — admin rename, reason mandatory)."""
        self._require_reason(reason)
        kingdom = await self._find_kingdom(kingdom_name)
        cleaned = self._kingdoms.check_name(new_name)
        if any(
            k.name.casefold() == cleaned.casefold() and k.id != kingdom.id
            for k in await self._kingdoms.kingdoms()
        ):
            raise KingdomNameInvalidError("a kingdom with this name already exists")
        before = _snap(KINGDOMS_COLLECTION, kingdom.to_mongo())
        kingdom.name = cleaned
        kingdom.name_approved = True
        await self._store.upsert_kingdom(kingdom.to_mongo())
        await self._record(
            "rename_kingdom",
            actor_id,
            actor_name,
            reason,
            "kingdom",
            kingdom.id,
            before,
            _snap(KINGDOMS_COLLECTION, kingdom.to_mongo()),
        )
        return kingdom

    async def swap_throne(
        self,
        kingdom_name: str,
        new_king_id: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> tuple[LordModel, LordModel | None]:
        """Swap the throne inside a kingdom (D75 — total inheritance, D23).

        The new King must be an active lord of the kingdom; the crowns
        and the weekly attack/defense budgets trade places — the throne
        carries the budgets (the anti-abuse inheritance of D23). When
        the kingdom has no King, the lord is simply crowned.
        """
        self._require_reason(reason)
        kingdom = await self._find_kingdom(kingdom_name)
        lords = [
            lord
            for lord in await self._kingdoms.lords()
            if lord.kingdom_id == kingdom.id and not lord.left and not lord.in_queue
        ]
        old_king = next((lord for lord in lords if lord.role is KING_ROLE), None)
        new_king = next((lord for lord in lords if lord.id == new_king_id), None)
        if new_king is None or new_king.role is KING_ROLE:
            raise ThroneSwapError("the new King must be an active lord of the kingdom")
        before: dict[str, dict[str, Any]] = {}
        if old_king is not None:
            before.update(_snap(LORDS_COLLECTION, old_king.to_mongo()))
        before.update(_snap(LORDS_COLLECTION, new_king.to_mongo()))
        if old_king is not None:
            old_king.role = LORD_ROLE
            old_king.attack_used, new_king.attack_used = new_king.attack_used, old_king.attack_used
            old_king.defense_used, new_king.defense_used = (
                new_king.defense_used,
                old_king.defense_used,
            )
        new_king.role = KING_ROLE
        for lord in (old_king, new_king):
            if lord is not None:
                await self._store.upsert_lord(lord.to_mongo())
        after = _snap(LORDS_COLLECTION, new_king.to_mongo())
        if old_king is not None:
            after.update(_snap(LORDS_COLLECTION, old_king.to_mongo()))
        await self._record(
            "swap_throne",
            actor_id,
            actor_name,
            reason,
            "kingdom",
            kingdom.id,
            before,
            after,
        )
        return new_king, old_king

    async def dissolve_kingdom(
        self,
        kingdom_name: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> KingdomModel:
        """Dissolve a kingdom (D75): territories to Gaïa, players queued.

        The territories revert to Gaïa (D32), every member falls back
        to the waiting queue **with their role kept**, and the kingdom
        document is deleted — the rollback restores the whole set.
        """
        self._require_reason(reason)
        await self._require_season()
        kingdom = await self._find_kingdom(kingdom_name)
        if kingdom.is_gaia:
            raise KingdomNotFoundError("Gaïa is never dissolvable")
        before: dict[str, dict[str, Any]] = _snap(KINGDOMS_COLLECTION, kingdom.to_mongo())
        members = [
            lord
            for lord in await self._kingdoms.lords()
            if lord.kingdom_id == kingdom.id and not lord.left
        ]
        for lord in members:
            before.update(_snap(LORDS_COLLECTION, lord.to_mongo()))
            lord.kingdom_id = None
            lord.in_queue = True
            await self._store.upsert_lord(lord.to_mongo())
        territories = [
            territory
            for territory in await self._territories()
            if territory.owner_kingdom_id == kingdom.id
        ]
        for territory in territories:
            before.update(_snap(TERRITORIES_COLLECTION, territory.to_mongo()))
            territory.owner_kingdom_id = GAIA_KINGDOM_KEY
            await self._store.upsert_territory(territory.to_mongo())
        await self._store.delete_kingdom(kingdom.id)
        after = {f"{TERRITORIES_COLLECTION}:{t.id}": t.to_mongo() for t in territories}
        await self._record(
            "dissolve_kingdom",
            actor_id,
            actor_name,
            reason,
            "kingdom",
            kingdom.id,
            before,
            after,
        )
        return kingdom

    # ------------------------------------------------------------------
    # switches and quotas (D75: free policy, gel on overcapacity)

    async def set_recruitment(
        self,
        kingdom_name: str,
        *,
        open: bool,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> KingdomModel:
        """Flip one kingdom's recruitment switch (D75)."""
        self._require_reason(reason)
        kingdom = await self._find_kingdom(kingdom_name)
        before = _snap(KINGDOMS_COLLECTION, kingdom.to_mongo())
        kingdom.recruitment_open = open
        await self._store.upsert_kingdom(kingdom.to_mongo())
        await self._record(
            "set_recruitment",
            actor_id,
            actor_name,
            reason,
            "kingdom",
            kingdom.id,
            before,
            _snap(KINGDOMS_COLLECTION, kingdom.to_mongo()),
        )
        return kingdom

    async def set_applications(
        self,
        *,
        open: bool,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> SeasonState:
        """Flip the global applications switch (D75)."""
        self._require_reason(reason)
        season = await self._require_season()
        before = _snap(SEASONS_COLLECTION, season.to_mongo())
        season.applications_open = open
        await self._store.upsert_season(season.to_mongo())
        await self._record(
            "set_applications",
            actor_id,
            actor_name,
            reason,
            "season",
            season.id,
            before,
            _snap(SEASONS_COLLECTION, season.to_mongo()),
        )
        return season

    async def set_quotas(
        self,
        *,
        kingdoms_count: int | None = None,
        lords_per_kingdom: int | None = None,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> SeasonState:
        """Move the kingdom/lord quotas (D75 — free policy).

        ``None`` resets a quota to its config default. Lowering a quota
        below a kingdom's current headcount **gels** it: capacity only
        gates new members, nobody is ever ejected.
        """
        self._require_reason(reason)
        season = await self._require_season()
        before = _snap(SEASONS_COLLECTION, season.to_mongo())
        season.kingdoms_count_override = kingdoms_count
        season.lords_per_kingdom_override = lords_per_kingdom
        await self._store.upsert_season(season.to_mongo())
        await self._record(
            "set_quotas",
            actor_id,
            actor_name,
            reason,
            "season",
            season.id,
            before,
            _snap(SEASONS_COLLECTION, season.to_mongo()),
        )
        return season

    async def set_foundation_rights(
        self,
        *,
        king: bool | None = None,
        admin: bool | None = None,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> SeasonState:
        """Edit the foundation checkboxes (D75) — initial window only.

        The window closes with the season's first cycle: once the
        season is launched and rolling, the rights are locked.
        """
        self._require_reason(reason)
        season = await self._require_season()
        if season.current_cycle > 0:
            raise FoundationWindowClosedError("the foundation rights are locked past the launch")
        before = _snap(SEASONS_COLLECTION, season.to_mongo())
        if king is not None:
            season.foundation_king = king
        if admin is not None:
            season.foundation_admin = admin
        await self._store.upsert_season(season.to_mongo())
        await self._record(
            "set_foundation_rights",
            actor_id,
            actor_name,
            reason,
            "season",
            season.id,
            before,
            _snap(SEASONS_COLLECTION, season.to_mongo()),
        )
        return season

    # ------------------------------------------------------------------
    # rollback

    async def rollback(
        self,
        action_id: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str,
    ) -> AdminActionModel:
        """Roll one admin action back (D75): 2h window, once per action.

        The ``before`` snapshots are reapplied verbatim through the
        store seams; documents the action created (an ``after`` entry
        with no ``before`` counterpart) are deleted. The rollback is
        itself journaled — with its mandatory reason.
        """
        self._require_reason(reason)
        action = await self._journal.rollback(action_id)
        for key, document in action.before.items():
            await self._restore(key, document)
        for key in action.after:
            if key not in action.before:
                await self._delete(key)
        await self._record(
            "rollback",
            actor_id,
            actor_name,
            reason,
            action.target_kind,
            action.target_id,
            dict(action.after),
            dict(action.before),
        )
        return action

    # ------------------------------------------------------------------
    # helpers

    def _require_reason(self, reason: str) -> None:
        """Gate every admin action on a non-blank reason (D75), before any resolution."""
        if not (reason or "").strip():
            raise AdminReasonRequiredError("an admin action requires a reason (D75)")


    async def _record(
        self,
        action_type: str,
        actor_id: str,
        actor_name: str,
        reason: str,
        target_kind: str,
        target_id: str,
        before: dict[str, dict[str, Any]],
        after: dict[str, dict[str, Any]],
    ) -> None:
        """Journal one action on the current season (reason mandatory)."""
        self._require_reason(reason)
        season = await self._kingdoms.current_season()
        await self._journal.record(
            season_id=season.id if season is not None else "-",
            action_type=action_type,
            actor_id=actor_id,
            actor_name=actor_name,
            reason=reason,
            target_kind=target_kind,
            target_id=target_id,
            before=before,
            after=after,
        )

    async def _restore(self, key: str, document: dict[str, Any]) -> None:
        """Reapply one before-snapshot through the matching store seam."""
        kind = key.rsplit(":", 1)[0]
        if kind == SEASONS_COLLECTION:
            await self._store.upsert_season(document)
        elif kind == KINGDOMS_COLLECTION:
            await self._store.upsert_kingdom(document)
        elif kind == LORDS_COLLECTION:
            await self._store.upsert_lord(document)
        elif kind == TERRITORIES_COLLECTION:
            await self._store.upsert_territory(document)
        else:  # pragma: no cover — the keys are built by this module
            raise ValueError(f"unknown snapshot kind: {kind}")

    async def _delete(self, key: str) -> None:
        """Delete one document created by the rolled-back action."""
        kind, doc_id = key.rsplit(":", 1)
        if kind == KINGDOMS_COLLECTION:
            await self._store.delete_kingdom(doc_id)
        elif kind == LORDS_COLLECTION:
            await self._store.delete_lord(doc_id)
        elif kind == TERRITORIES_COLLECTION:
            await self._store.delete_territory(doc_id)
        elif kind == SEASONS_COLLECTION:  # pragma: no cover — seasons are never created
            raise ValueError("a season is never deleted by a rollback")

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        return season

    async def _find_kingdom(self, kingdom_name: str) -> KingdomModel:
        """Resolve a kingdom by display name, Gaïa included."""
        kingdoms = await self._kingdoms.kingdoms()
        wanted = kingdom_name.strip().casefold()
        found = next((k for k in kingdoms if k.name.casefold() == wanted), None)
        if found is None:
            raise KingdomNotFoundError("no kingdom with this name in the current season")
        return found

    async def _find_lord(self, player_id: str) -> LordModel | None:
        """Return the lord document of a player; None when absent."""
        return next(
            (lord for lord in await self._kingdoms.lords() if lord.id == player_id), None
        )

    async def _require_lord(self, player_id: str) -> LordModel:
        """Return the player's lord document or raise a not-found error."""
        lord = await self._find_lord(player_id)
        if lord is None:
            raise KingdomNotFoundError("the player is not enrolled in the current season")
        return lord

    async def _has_king(self, kingdom_id: str) -> bool:
        """Whether the kingdom already counts an active King."""
        return any(
            lord.role is KING_ROLE and not lord.left
            for lord in await self._kingdoms.lords()
            if lord.kingdom_id == kingdom_id
        )

    async def _territories(self) -> list[TerritoryModel]:
        """Return the territory models of the current data set."""
        docs = await self._store.find_territories()
        return [TerritoryModel.from_mongo(doc) for doc in docs]
