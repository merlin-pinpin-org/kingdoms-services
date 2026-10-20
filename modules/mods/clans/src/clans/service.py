"""clans mod — reference example (generated via the mod-intake skill).

Permanent mod: clan management with mutual-agreement admissions.

Rules (shaped with the game designer, 2026-10-20 — mod.yaml holds the
declaration, this package holds the logic against the core seams only):

- anyone can create a clan and becomes its leader;
- admission is MUTUAL: a player asks (the clan's leader/officers get
  the request and approve) or a clan admin invites (the player accepts);
- a player belongs to at most ONE clan at a time;
- declared criteria live in ``settings.enrollment`` (the generic
  ``EnrollmentService`` evaluates them — unknown stats degrade to a
  manual-review reason, never a crash or a silent accept).

The Discord surface (commands, panels, forum posts) is built through
the core UI SDK and the generic admin seams — never raw ``discord.ui``.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kingdoms.core.services.enrollment import (
    EnrollmentService,
    parse_criteria,
)

logger = logging.getLogger("kingdoms.mods.clans")

ROLE_LEADER = "leader"
ROLE_OFFICER = "officer"
ROLE_MEMBER = "member"

CLANS_COLLECTION = "clans"


class ClansDatabase(Protocol):
    """The persistence seam for clans and their memberships."""

    async def upsert_clan(self, document: dict[str, Any]) -> None:
        """Insert or replace one clan document."""
        ...

    async def find_clan(self, clan_id: str) -> dict[str, Any] | None:
        """Return one clan document; None when unknown."""
        ...

    async def find_clan_by_name(self, guild_id: str, name: str) -> dict[str, Any] | None:
        """Return the guild's clan by (casefolded) name; None when none."""
        ...

    async def list_clans(self, guild_id: str) -> list[dict[str, Any]]:
        """List the guild's clans."""
        ...

    async def find_membership(self, guild_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the player's active membership; None when clanless."""
        ...

    async def upsert_membership(self, document: dict[str, Any]) -> None:
        """Insert or replace one membership document."""
        ...

    async def delete_membership(self, guild_id: str, user_id: str) -> bool:
        """Drop the player's membership; True when one was removed."""
        ...

    async def delete_clan(self, guild_id: str, clan_id: str) -> bool:
        """Drop a clan; True when one was removed."""
        ...

    async def list_members(self, guild_id: str, clan_id: str) -> list[dict[str, Any]]:
        """List a clan's active memberships."""
        ...


class ClansModError(Exception):
    """Base of the clans domain errors."""


class ClanNameTakenError(ClansModError):
    """A clan with this name already exists in the guild."""


class AlreadyInClanError(ClansModError):
    """The player already belongs to a clan (one at a time)."""


class NotInClanError(ClansModError):
    """The player belongs to no clan."""


class NotClanAdminError(ClansModError):
    """The player is neither leader nor officer of the clan."""


class MutuallyRequiredError(ClansModError):
    """Both sides must agree — the other side's consent is missing."""


class ClanService:
    """The clans business logic — everything but the Discord rendering."""

    def __init__(self, database: ClansDatabase, enrollment: EnrollmentService | None = None) -> None:
        self._db = database
        self._enrollment = enrollment

    async def create_clan(
        self, guild_id: str, owner_id: str, owner_name: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        """Create a clan; the creator becomes its leader.

        Free for everyone (mod rule 1) — one clan per player at a time.
        """
        if await self._db.find_clan_by_name(guild_id, name) is not None:
            raise ClanNameTakenError(f"a clan named {name!r} already exists")
        if await self._db.find_membership(guild_id, owner_id) is not None:
            raise AlreadyInClanError("you already belong to a clan (leave it first)")
        clans = await self._db.list_clans(guild_id)
        clan_id = f"clan:{guild_id}:{len(clans) + 1}"
        clan = {
            "_id": clan_id,
            "guild_id": guild_id,
            "name": name.strip(),
            "description": description.strip(),
            "leader_id": owner_id,
            "created_at": None,
            "created_by": owner_id,
        }
        await self._db.upsert_clan(clan)
        await self._db.upsert_membership(
            {
                "_id": f"clan:{guild_id}:{owner_id}",
                "guild_id": guild_id,
                "clan_id": clan_id,
                "user_id": owner_id,
                "display_name": owner_name,
                "role": ROLE_LEADER,
            }
        )
        logger.info("CLANS: clan %s created by %s in guild %s", name, owner_id, guild_id)
        return clan

    async def apply_to_clan(self, guild_id: str, user_id: str, display_name: str, clan_id: str) -> object:
        """Handle a player asking to join a clan — mutual agreement, side 1.

        The generic enrollment records the application (criteria
        evaluated; the clan's admins decide from the request view).
        """
        if await self._db.find_membership(guild_id, user_id) is not None:
            raise AlreadyInClanError("you already belong to a clan (leave it first)")
        if await self._db.find_clan(clan_id) is None:
            raise KeyError(f"clan {clan_id!r} not found")
        if self._enrollment is None:
            raise MutuallyRequiredError("the clan's admins must agree — contact them directly")
        return await self._enrollment.apply(guild_id, user_id, display_name, season=clan_id)

    async def approve_application(self, guild_id: str, application: Any, admin_id: str) -> Any:
        """Approve a pending application as a clan admin — side 2.

        Runs the admin guard (leader/officer of the clan the candidate
        applied to), then the enrollment decision — the member lands in
        the clan with the ``member`` role.
        """
        await self._require_clan_admin(guild_id, admin_id)
        if await self._db.find_membership(guild_id, application.user_id) is not None:
            raise AlreadyInClanError("the candidate already belongs to a clan")
        if self._enrollment is None:
            raise MutuallyRequiredError("no enrollment wired — the admins must agree directly")
        decided = await self._enrollment.accept(application.application_id, reason="clan admin approval")
        membership_id = f"clan:{guild_id}:{application.user_id}"
        await self._db.upsert_membership(
            {
                "_id": membership_id,
                "guild_id": guild_id,
                "clan_id": application_clan_id(application),
                "user_id": application.user_id,
                "display_name": application.display_name,
                "role": ROLE_MEMBER,
            }
        )
        return decided

    async def leave_clan(self, guild_id: str, user_id: str) -> None:
        """Remove the member from their clan (the leader must hand over first)."""
        membership = await self._db.find_membership(guild_id, user_id)
        if membership is None:
            raise NotInClanError("you belong to no clan")
        if membership.get("role") == ROLE_LEADER:
            members = await self._db.list_members(guild_id, str(membership.get("clan_id")))
            others = [m for m in members if m.get("user_id") != user_id]
            if others:
                raise ClansModError("the leader cannot leave a clan with other members — hand it over first")
            await self._db.delete_clan(guild_id, str(membership.get("clan_id")))
        await self._db.delete_membership(guild_id, user_id)

    async def clan_of(self, guild_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the player's clan document; None when clanless."""
        membership = await self._db.find_membership(guild_id, user_id)
        if membership is None:
            return None
        clan = await self._db.find_clan(str(membership.get("clan_id")))
        return clan if clan is not None else None

    async def dashboard(self, guild_id: str) -> list[dict[str, Any]]:
        """Build the dashboard rows: every clan with its member count."""
        clans = await self._db.list_clans(guild_id)
        rows: list[dict[str, Any]] = []
        for clan in clans:
            members = await self._db.list_members(guild_id, str(clan.get("_id")))
            rows.append({**clan, "member_count": len(members)})
        return rows

    async def members(self, guild_id: str, clan_id: str) -> list[dict[str, Any]]:
        """Return a clan's member list (drill-down view)."""
        if await self._db.find_clan(clan_id) is None:
            raise KeyError(f"clan {clan_id!r} not found")
        return await self._db.list_members(guild_id, clan_id)

    async def _require_clan_admin(self, guild_id: str, user_id: str) -> dict[str, Any]:
        """Fail loudly unless the user is a leader/officer of their clan."""
        membership = await self._db.find_membership(guild_id, user_id)
        if membership is None or membership.get("role") not in (ROLE_LEADER, ROLE_OFFICER):
            raise NotClanAdminError("only the clan's leader or officers can do this")
        return membership


def application_clan_id(application: Any) -> str:
    """Return the clan an application targets (stored as the application scope)."""
    return str(getattr(application, "season", "") or "")


def criteria_from_declaration(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Read the declared enrollment criteria (fail loudly on typos)."""
    return parse_criteria(settings)
