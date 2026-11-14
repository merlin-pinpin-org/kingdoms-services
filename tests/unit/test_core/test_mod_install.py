"""Unit tests for the install/uninstall service (botforge-services#374)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botforge.core.services.guild_access import GuildAccessService
from botforge.core.services.mod_install import ModInstallService


@dataclass
class FakeDefinition:
    """A mod declaration double."""

    name: str = ""
    required_games: tuple[str, ...] = ()


class FakeAccessDatabase:
    """An in-memory guild_access store."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def get_guild_access(self, guild_id: str) -> dict[str, Any] | None:
        """Read one guild's access document; None when absent."""
        return self.docs.get(guild_id)

    async def upsert_guild_access(self, document: dict[str, Any]) -> None:
        """Write one guild's access document."""
        self.docs[document["guild_id"]] = document

    async def list_guild_access(self) -> list[dict[str, Any]]:
        """List every access document."""
        return list(self.docs.values())


class FakeRegistry:
    """A registry double: kingdoms requires aoe2, ladder requires nothing."""

    def __init__(self) -> None:
        self.defs = {
            "kingdoms": FakeDefinition(name="kingdoms", required_games=("aoe2",)),
            "ladder": FakeDefinition(name="ladder"),
        }

    def get(self, name: str) -> Any:
        """Return one mod's declaration."""
        return self.defs.get(name)

    def enabled(self) -> dict[str, FakeDefinition]:
        """Return every enabled mod's declaration."""
        return self.defs


def _service(
    db: FakeAccessDatabase | None = None,
    wipe_db: Any = None,
) -> tuple[ModInstallService, FakeAccessDatabase]:
    """Build the service over the fakes (the platform catalog: aoe2 + 2 mods)."""
    db = db or FakeAccessDatabase()
    access = GuildAccessService(
        db,  # type: ignore[arg-type]
        games=("aoe2",),
        mods=("kingdoms", "ladder"),
    )
    return ModInstallService(access, FakeRegistry(), database=wipe_db), db


async def test_default_open_installing_materializes_the_doc() -> None:
    """Default: games active, mods inactive — installing materializes the set.

    The doc freezes the default-active games plus the installed mod
    (the other mod stays out: it is not default-active).
    """
    service, _ = _service()
    result = await service.install("123", mods=("ladder",))
    assert result.ok
    assert result.active_mods == ("ladder",)
    assert result.active_games == ("aoe2",)


async def test_install_propagates_the_required_games() -> None:
    """Installing kingdoms installs aoe2 along (a mod is never unusable)."""
    service, _ = _service()
    await service.install("123", mods=("ladder",))
    result = await service.install("123", mods=("kingdoms",))
    assert result.ok
    assert set(result.active_mods) == {"kingdoms", "ladder"}
    assert "aoe2" in result.active_games


async def test_install_unknown_mod_fails_loudly() -> None:
    """An undeclared mod is refused; the active set is untouched."""
    service, _ = _service()
    result = await service.install("123", mods=("ghost",))
    assert not result.ok
    assert result.refused_by == ("ghost",)


async def test_uninstall_sticks_so_the_item_can_be_reinstalled() -> None:
    """Uninstalling everything sticks: the doc stays explicit and EMPTY.

    The empty doc means NONE active (it does not re-open the defaults)
    — the catalog post flips back to Install, and installing again works.
    """
    service, db = _service()
    await service.install("123", mods=("ladder",), games=("aoe2",))
    result = await service.uninstall("123", mods=("kingdoms", "ladder"), games=("aoe2",))
    assert result.ok
    assert result.active_mods == ()
    assert result.active_games == ()
    doc = await db.get_guild_access("123")
    assert doc is not None and doc["games"] == [] and doc["mods"] == []
    status = await service.status("123")
    assert status == {"games": (), "mods": ()}  # the uninstall sticks
    again = await service.install("123", games=("aoe2",))
    assert again.ok
    assert (await service.status("123"))["games"] == ("aoe2",)  # reinstall works


async def test_uninstall_a_game_with_dependents_is_refused() -> None:
    """A game an active mod requires is refused — the dependents are named."""
    service, _ = _service()
    await service.install("123", mods=("kingdoms",))
    result = await service.uninstall("123", games=("aoe2",))
    assert not result.ok
    assert result.refused_by == ("kingdoms",)


async def test_uninstall_the_mod_then_the_game_passes() -> None:
    """Uninstalling the dependent first frees the game."""
    service, _ = _service()
    await service.install("123", mods=("kingdoms",))
    result = await service.uninstall("123", mods=("kingdoms", "ladder"), games=("aoe2",))
    assert result.ok
    assert result.active_mods == ()
    assert result.active_games == ()


class FakeWipeDatabase:
    """A database double that counts the ModStorage wipes."""

    def __init__(self) -> None:
        self.collection_names = ["kingdoms:realms", "kingdoms:attacks", "ladder:matches"]
        self.wiped: list[str] = []

    def __getitem__(self, name: str) -> Any:
        """Return a collection double for one id."""

        class _Col:
            async def delete_many(self, query: dict[str, Any]) -> Any:
                class _R:
                    deleted_count = 1

                return _R()

        return _Col()

    async def list_collection_names(self) -> list[str]:
        """Return every collection id."""
        return list(self.collection_names)


async def test_uninstall_with_data_wipe_is_namespaced() -> None:
    """The data wipe deletes the mod's collections only (the <mod>: prefix)."""
    wipe_db = FakeWipeDatabase()
    service, _ = _service(wipe_db=wipe_db)
    await service.install("123", mods=("kingdoms", "ladder"))
    result = await service.uninstall("123", mods=("kingdoms",), wipe_data=True)
    assert result.ok
    assert result.wiped_collections == 2  # kingdoms:realms + kingdoms:attacks, not ladder:matches


async def test_status_lists_the_active_sets() -> None:
    """The status is data: the active games and mods, sorted."""
    service, _ = _service()
    await service.install("123", mods=("kingdoms",))
    status = await service.status("123")
    assert status["games"] == ("aoe2",)
    assert status["mods"] == ("kingdoms",)
