"""The platform's declared views — the doc's list, in code.

This module is the executable mirror of docs/DEVELOPMENT/VIEWS.md
(kingdoms repo): every view the platform ships is declared here,
exactly as the doc lists them. Changing the organization means
changing this file (and the doc in the same PR).

Today: one game (aoe2), no mods yet — the mod menus carry the
placeholders the doc declares (disabled buttons), removed or
replaced as real mods land.
"""

from __future__ import annotations

from kingdoms.discord.views_registry import (
    KIND_MENU,
    KIND_PANEL,
    ButtonDecl,
    ViewDecl,
    register_view,
)

_BUTTONS_HOME = (
    ButtonDecl(target="panel:profile", label_key="ui.homes.btn_profile"),
    ButtonDecl(target="menu:games:home", label_key="ui.homes.btn_games"),
    ButtonDecl(target="menu:mods:home", label_key="ui.homes.btn_mods"),
    ButtonDecl(target="menu:admin", label_key="ui.homes.btn_admin", admin=True),
)

_BUTTONS_ADMIN = (
    ButtonDecl(target="panel:status", label_key="ui.homes.btn_status"),
    ButtonDecl(target="menu:games:admin", label_key="ui.homes.btn_games", admin=True),
    ButtonDecl(target="menu:mods:admin", label_key="ui.homes.btn_mods", admin=True),
    ButtonDecl(target="", label_key="ui.homes.btn_sync", sync=True),
)

_BUTTONS_GAMES_HOME = (
    ButtonDecl(target="menu:games:aoe2", label_key="ui.games.homes.aoe2"),
    ButtonDecl(target="menu:games:admin", label_key="ui.homes.btn_games", admin=True),
)

_BUTTONS_GAMES_ADMIN = (
    ButtonDecl(target="menu:game:aoe2:admin", label_key="ui.games.homes.aoe2", admin=True),
    ButtonDecl(target="", label_key="ui.homes.btn_sync", sync=True),
)

_BUTTONS_GAME_AOE2 = (
    ButtonDecl(target="panel:game:aoe2:live", label_key="ui.games.homes.btn_live"),
    ButtonDecl(target="panel:game:aoe2:players", label_key="ui.homes.btn_users"),
    ButtonDecl(target="menu:game:aoe2:admin", label_key="ui.homes.btn_admin", admin=True),
)

_BUTTONS_GAME_AOE2_ADMIN = (
    ButtonDecl(target="", label_key="ui.homes.btn_add_map"),
    ButtonDecl(target="", label_key="ui.homes.btn_create_pool"),
    ButtonDecl(target="", label_key="ui.homes.btn_sync", sync=True),
)

_BUTTONS_MODS_HOME = (
    ButtonDecl(target="menu:mod:ladder", label_key="ui.mods.homes.ladder", disabled=True),
    ButtonDecl(target="menu:mod:tournament", label_key="ui.mods.homes.tournament", disabled=True),
    ButtonDecl(target="menu:mod:kingdom", label_key="ui.mods.homes.kingdom", disabled=True),
    ButtonDecl(target="menu:mods:admin", label_key="ui.homes.btn_mods", admin=True),
)

_BUTTONS_MODS_ADMIN = (
    ButtonDecl(target="menu:mod:ladder:admin", label_key="ui.mods.homes.ladder", disabled=True, admin=True),
    ButtonDecl(target="menu:mod:tournament:admin", label_key="ui.mods.homes.tournament", disabled=True, admin=True),
    ButtonDecl(target="menu:mod:kingdom:admin", label_key="ui.mods.homes.kingdom", disabled=True, admin=True),
    ButtonDecl(target="", label_key="ui.homes.btn_sync", sync=True),
)


def declare_platform_views() -> None:
    """Declare every platform view (idempotent, boot-time)."""
    register_view(ViewDecl(id="menu:home", kind=KIND_MENU, scope="root/general", buttons=_BUTTONS_HOME))
    register_view(ViewDecl(id="panel:profile", kind=KIND_PANEL))
    register_view(ViewDecl(id="menu:admin", kind=KIND_MENU, scope="bot/admin", buttons=_BUTTONS_ADMIN))
    register_view(ViewDecl(id="panel:status", kind=KIND_PANEL))
    register_view(ViewDecl(id="menu:games:home", kind=KIND_MENU, scope="games/general", buttons=_BUTTONS_GAMES_HOME))
    register_view(ViewDecl(id="menu:games:admin", kind=KIND_MENU, scope="games/admin", buttons=_BUTTONS_GAMES_ADMIN))
    register_view(
        ViewDecl(id="menu:games:aoe2", kind=KIND_MENU, scope="games/aoe2/general", buttons=_BUTTONS_GAME_AOE2)
    )
    register_view(
        ViewDecl(id="menu:game:aoe2:admin",
                 kind=KIND_MENU,
                 scope="games/aoe2/admin",
                 buttons=_BUTTONS_GAME_AOE2_ADMIN)
    )
    register_view(ViewDecl(id="panel:game:aoe2:live", kind=KIND_PANEL))
    register_view(ViewDecl(id="panel:game:aoe2:players", kind=KIND_PANEL))
    register_view(ViewDecl(id="menu:mods:home", kind=KIND_MENU, scope="mods/general", buttons=_BUTTONS_MODS_HOME))
    register_view(ViewDecl(id="menu:mods:admin", kind=KIND_MENU, scope="mods/admin", buttons=_BUTTONS_MODS_ADMIN))
    register_view(ViewDecl(id="menu:mod:ladder", kind=KIND_MENU))
    register_view(ViewDecl(id="menu:mod:tournament", kind=KIND_MENU))
    register_view(ViewDecl(id="menu:mod:kingdom", kind=KIND_MENU))
    register_view(ViewDecl(id="menu:mod:ladder:admin", kind=KIND_MENU))
    register_view(ViewDecl(id="menu:mod:tournament:admin", kind=KIND_MENU))
    register_view(ViewDecl(id="menu:mod:kingdom:admin", kind=KIND_MENU))


__all__ = ["declare_platform_views"]
