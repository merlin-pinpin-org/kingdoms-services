"""The install/uninstall service: a guild's mods and games (botforge-services#374).

The forum's one-post-per-mod surface and the console's headless CLI
both ride THIS service — the rules live in one place:

- **Install** a mod: it joins the guild's active set, and its declared
  ``required_games`` ride along (a mod is never installed unusable).
- **Uninstall** a game: refused while an active mod requires it (the
  dependents are named in the refusal — cascading is an explicit
  operator choice, never a side effect).
- **Uninstall a mod**: optional data wipe — the mod's namespaced
  collections (``ModStorage.wipe``, the ``<mod>:`` prefix), never
  another mod's, never the platform's.

The access model (guild_access): a guild with no access document gets
the default-active sets (games active, mods not); installing into such
a guild materializes the document (the active set, explicit). Once a
document exists its lists are the whole truth, even empty — so an
uninstall sticks and the catalog's Install button brings the item back
(a reinstall restores the state when the data was kept).

Results are data (``ModInstallResult``): the surface renders them in
the guild's locale — this service never formats a user-facing string.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from botforge.core.services.guild_access import GuildAccessService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModInstallResult:
    """What one install/uninstall did (the surface renders it)."""

    ok: bool
    summary: str
    active_games: tuple[str, ...] = ()
    active_mods: tuple[str, ...] = ()
    wiped_collections: int = 0
    refused_by: tuple[str, ...] = field(default=())


class ModInstallService:
    """Install/uninstall the mods and games of one guild (the forum's engine)."""

    def __init__(
        self,
        access: GuildAccessService,
        registry: Any,
        database: Any | None = None,
    ) -> None:
        """Bind the access service, the mod registry and the shared database."""
        self._access = access
        self._registry = registry
        self._database = database

    def _definition(self, name: str) -> Any:
        """Return one mod's declaration (None when unknown)."""
        return self._registry.get(name) if self._registry is not None else None

    def _required_games(self, mod_name: str) -> tuple[str, ...]:
        """Return the games a mod declares as required."""
        definition = self._definition(mod_name)
        return tuple(getattr(definition, "required_games", ()) or ()) if definition is not None else ()

    def _dependents(self, game_key: str, active_mods: tuple[str, ...]) -> tuple[str, ...]:
        """Return the active mods that require one game."""
        return tuple(mod for mod in active_mods if game_key in self._required_games(mod))

    async def install(
        self,
        guild_id: str,
        *,
        mods: tuple[str, ...] = (),
        games: tuple[str, ...] = (),
    ) -> ModInstallResult:
        """Install mods/games: the active set grows, requirements ride along."""
        active_games = set(await self._access.enabled_games(guild_id))
        active_mods = set(await self._access.enabled_mods(guild_id))
        for mod_name in mods:
            definition = self._definition(mod_name)
            if definition is None:
                return ModInstallResult(
                    ok=False,
                    summary=f"unknown mod: {mod_name}",
                    refused_by=(mod_name,),
                )
            active_mods.add(mod_name)
            active_games.update(self._required_games(mod_name))
        active_games.update(games)
        doc = await self._access.set_grants(guild_id, sorted(active_games), sorted(active_mods))
        logger.info(
            "INSTALL (guild %s): mods=%s games=%s",
            guild_id,
            sorted(active_mods),
            sorted(active_games),
        )
        return ModInstallResult(
            ok=True,
            summary="installed",
            active_games=tuple(doc.get("games") or ()),
            active_mods=tuple(doc.get("mods") or ()),
        )

    async def uninstall(
        self,
        guild_id: str,
        *,
        mods: tuple[str, ...] = (),
        games: tuple[str, ...] = (),
        wipe_data: bool = False,
    ) -> ModInstallResult:
        """Uninstall mods/games: the active set shrinks; dependents refuse.

        A game an active mod requires is refused (the dependents are
        named); a mod's data wipe rides ``ModStorage`` — the ``<mod>:``
        prefix, nothing else.
        """
        active_games = set(await self._access.enabled_games(guild_id))
        active_mods = set(await self._access.enabled_mods(guild_id))
        for game_key in games:
            dependents = self._dependents(game_key, tuple(sorted(active_mods - set(mods))))
            if dependents:
                return ModInstallResult(
                    ok=False,
                    # the DEPENDENTS are named (the mods that require the
                    # game), never the game itself — the surface renders
                    # them localized
                    summary=f"required by active mods: {', '.join(dependents)}",
                    refused_by=dependents,
                )
            active_games.discard(game_key)
        active_mods -= set(mods)
        doc = await self._access.set_grants(guild_id, sorted(active_games), sorted(active_mods))
        wiped = 0
        if wipe_data and self._database is not None:
            from botforge.core.services.mod_storage import ModStorage

            for mod_name in mods:
                wiped += await ModStorage(self._database, mod_name).wipe()
        logger.info(
            "UNINSTALL (guild %s): mods=%s games=%s wipe_data=%s wiped=%d",
            guild_id,
            sorted(mods),
            sorted(games),
            wipe_data,
            wiped,
        )
        return ModInstallResult(
            ok=True,
            summary="uninstalled",
            active_games=tuple(doc.get("games") or ()),
            active_mods=tuple(doc.get("mods") or ()),
            wiped_collections=wiped,
        )

    async def status(self, guild_id: str) -> dict[str, tuple[str, ...]]:
        """Return the guild's active sets (the forum posts render them)."""
        return {
            "games": tuple(sorted(await self._access.enabled_games(guild_id))),
            "mods": tuple(sorted(await self._access.enabled_mods(guild_id))),
        }


__all__ = ["ModInstallResult", "ModInstallService"]
