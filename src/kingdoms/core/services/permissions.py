"""Core permission service: runtime role-based authorization for actions.

Seeing a button is a UX hint, never a security boundary: roles drift,
messages persist and any member can attempt an interaction. The only
trustworthy enforcement point is the moment the interaction arrives —
this service answers it (kingdoms-services#55).

Inputs, in order:

- **bot operators** — the ``BOT_ADMINS`` ids bypass mod-level checks;
- **mod-declared required role keys** — resolved to platform role ids
  through the mod-role mappings (kingdoms-services#26);
- **the member's live roles** — read through the narrow
  :class:`MemberRoles` seam, never through a stale snapshot.

A denial is a decision, not an absence of one: callers answer
ephemerally and audit the denial (ADR-0003) — a check never fails
silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("kingdoms.core.permissions")

PERMISSION_DENIED_KEY = "permissions.denied"


class MemberRoles(Protocol):
    """Narrow platform seam: the live roles of one guild member."""

    async def get_member_role_ids(self, guild_id: str, user_id: str) -> list[str]:
        """Return the member's live platform role ids (empty when absent)."""
        ...


@dataclass(frozen=True, slots=True)
class ActionContext:
    """One interactive action, as seen at click time."""

    user_id: str
    mod: str
    custom_id: str
    guild_id: str | None = None
    required_roles: tuple[str, ...] = ()
    dm_allowed: bool = False

    def __post_init__(self) -> None:
        """Fail fast on a malformed custom_id (repo convention)."""
        parts = self.custom_id.split(":")
        if len(parts) < 2 or not parts[0]:
            raise ValueError(f"custom_id {self.custom_id!r} must follow '<mod>:<component>:<payload>'")


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """The authorization decision for one action, with its reason."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        """Use the result directly in ``if await service.is_authorized(...)``."""
        return self.allowed


class PermissionService:
    """Authorize component actions against the live member roles."""

    def __init__(
        self,
        members: MemberRoles,
        roles: ModRolesServiceLike,
        bot_admins: tuple[str, ...] = (),
    ) -> None:
        """Wire the live-roles seam, the mod-role resolver and the operators."""
        self._members = members
        self._roles = roles
        self._bot_admins = bot_admins

    async def user_has_role(self, guild_id: str, user_id: str, mod: str, role_key: str) -> bool:
        """Whether a member holds one mod role, resolved through the mapping."""
        try:
            role_id = await self._roles.resolve_role_id(guild_id, mod, role_key)
        except Exception:
            logger.warning("ROLE RESOLUTION FAILED (guild %s, %s:%s) — denying", guild_id, mod, role_key, exc_info=True)
            return False
        if role_id is None:
            return False
        member_role_ids = await self._safe_member_roles(guild_id, user_id)
        return role_id in member_role_ids

    async def user_has_any_role(self, guild_id: str, user_id: str, mod: str, role_keys: list[str]) -> bool:
        """Whether a member holds at least one of the mod role keys."""
        for role_key in role_keys:
            if await self.user_has_role(guild_id, user_id, mod, role_key):
                return True
        return False

    async def is_authorized(self, ctx: ActionContext) -> PermissionResult:
        """Decide one action: bypass, DM policy, then required roles.

        Bot admins bypass mod-level checks; DM interactions need
        ``dm_allowed`` (there are no guild roles to check); an action
        declaring no required role is open to every guild member. Every
        lookup failure denies — a broken store must never open a door.
        """
        if ctx.user_id in self._bot_admins:
            return PermissionResult(allowed=True, reason="bot_admin")
        if ctx.guild_id is None:
            if ctx.dm_allowed:
                return PermissionResult(allowed=True, reason="dm_allowed")
            return PermissionResult(allowed=False, reason="dm_not_allowed")
        if not ctx.required_roles:
            return PermissionResult(allowed=True, reason="open")
        if await self.user_has_any_role(ctx.guild_id, ctx.user_id, ctx.mod, list(ctx.required_roles)):
            return PermissionResult(allowed=True, reason="role")
        return PermissionResult(allowed=False, reason="missing_role")

    async def _safe_member_roles(self, guild_id: str, user_id: str) -> list[str]:
        """Read the member's live roles; any failure reads as no roles."""
        try:
            return [str(role_id) for role_id in await self._members.get_member_role_ids(guild_id, user_id)]
        except Exception:
            logger.warning("MEMBER ROLE LOOKUP FAILED (guild %s, user %s) — denying", guild_id, user_id, exc_info=True)
            return []


class ModRolesServiceLike(Protocol):
    """Narrow seam on the mod-role resolver (ModRolesService, #26)."""

    async def resolve_role_id(self, guild_id: str, mod: str, role_key: str) -> str | None:
        """Resolve a logical role key to a platform role id; None unmapped."""
        ...
