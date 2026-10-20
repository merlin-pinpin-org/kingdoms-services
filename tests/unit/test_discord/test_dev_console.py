"""Dev console tests: prod gate, propagation rules, grants replacement.

Everything runs against fakes \u2014 no test ever touches the real Discord
API (MockDiscord rule). The destructive paths are exercised through
seams, the gates through environment dicts.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.guild_access import GuildAccessService
from kingdoms.core.services.mod_definition import (
    ChannelCategoryDef,
    ModDefinition,
    RoleDef,
)
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.discord.dev_console import (
    DEV_CONSOLE_ENV_VAR,
    _propagate,
    dev_console_enabled,
)


class FakeGuildAccessDB:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def get_guild_access(self, guild_id: str) -> dict[str, Any] | None:
        return self.docs.get(guild_id)

    async def upsert_guild_access(self, document: dict[str, Any]) -> None:
        self.docs[str(document["guild_id"])] = {k: v for k, v in document.items() if k != "_id"}

    async def list_guild_access(self) -> list[dict[str, Any]]:
        return list(self.docs.values())


def _registry() -> ModRegistry:
    return ModRegistry(
        {
            "ladder": ModDefinition(
                name="ladder",
                seasonal=True,
                channel_categories=(ChannelCategoryDef(key="rankings", display_name="Rankings"),),
                roles=(RoleDef(key="ladder_admin", display_name="Ladder Admin"),),
                games=("aoe2",),
            ),
            "register": ModDefinition(
                name="register",
                seasonal=False,
                channel_categories=(ChannelCategoryDef(key="announce", display_name="Announces"),),
                roles=(),
                games=(),
            ),
        }
    )


def test_dev_console_opt_in_gate() -> None:
    assert dev_console_enabled({DEV_CONSOLE_ENV_VAR: "1"}) is True
    assert dev_console_enabled({DEV_CONSOLE_ENV_VAR: "true"}) is True
    assert dev_console_enabled({}) is False
    assert dev_console_enabled({DEV_CONSOLE_ENV_VAR: "0"}) is False
    assert dev_console_enabled({DEV_CONSOLE_ENV_VAR: ""}) is False


def test_prod_env_is_recognized() -> None:
    from kingdoms.discord.dev_console import PROD_ENV_NAMES

    assert "prod" in PROD_ENV_NAMES
    assert "production" in PROD_ENV_NAMES


def test_propagate_mod_installs_its_games() -> None:
    registry = _registry()
    games, mods = _propagate(registry, ("aoe2",), ("ladder", "register"), ["ladder"], [])
    assert games == ["aoe2"]
    assert mods == ["ladder"]


def test_propagate_game_uninstall_drops_dependent_mods_only() -> None:
    registry = _registry()
    games, mods = _propagate(registry, ("aoe2",), ("ladder", "register"), ["ladder", "register"], [])
    assert games == ["aoe2"]
    assert mods == ["ladder", "register"]
    games, mods = _propagate(registry, ("aoe2",), ("ladder", "register"), ["register"], [])
    assert games == []
    assert mods == ["register"]


def test_propagate_keeps_game_without_mods() -> None:
    registry = _registry()
    games, mods = _propagate(registry, ("aoe2",), ("ladder",), [], ["aoe2"])
    assert games == ["aoe2"]
    assert mods == []


async def test_set_grants_replaces_wholesale() -> None:
    db = FakeGuildAccessDB()
    service = GuildAccessService(db, games=("aoe2",), mods=("ladder", "register"))
    doc = await service.set_grants("123", games=["aoe2"], mods=["ladder"])
    assert doc["games"] == ["aoe2"]
    assert doc["mods"] == ["ladder"]
    doc = await service.set_grants("123", games=[], mods=["register"])
    assert doc["games"] == []
    assert doc["mods"] == ["register"]
    assert await service.enabled_mods("123") == ("register",)
