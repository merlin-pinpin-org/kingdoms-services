"""The mod menus' AUTOMATION (botforge-services#377): the dev is constrained, not trusted.

A mod developer declares NOTHING for their menus: the platform
GENERATES them at boot, for every enabled mod (botforge-services#378,
#379 — the pattern is enforced by construction):

- ``menu:mod:<name>`` — the home: the standard content (the
  declaration's presentation, mod_standard) + the **Admin button**
  (admin-only flip to the admin surface)
- ``menu:mod:<name>:admin`` — the admin: the standard content + the
  **repair button** (the declared channels and roles re-provision,
  #384's label)

The mods' home/admin menus (menu:mods:home, menu:mods:admin) list the
entries automatically — installed ones enabled, the not-installed
grayed (#377) — injected at render time with the guild's live state.

The games mirror the same automation (menu:games:home lists the games
with their install state).
"""

from __future__ import annotations

from typing import Any

from botforge.discord.views_registry import ButtonDecl, ViewDecl, register_view


def declare_mod_views(registry: Any) -> int:
    """Generate every enabled mod's home and admin views (boot-time).

    Idempotent: the auto-generated ids are stable (menu:mod:<name> and
    menu:mod:<name>:admin), the last declaration wins. Returns how many
    views were generated.
    """
    declared = 0
    if registry is None:
        return 0
    for name in registry.enabled():
        # the mod's channels ride its home as DOORS (link buttons, one
        # click each — the folder's own salons, #363); the admin flip
        # stays the declared button (the SDK's order constraint sorts it
        # last)
        register_view(
            ViewDecl(
                id=f"menu:mod:{name}",
                kind="menu",
                scope="mods/general",
                buttons=(
                    ButtonDecl(
                        target=f"menu:mod:{name}:admin",
                        label_key="ui.homes.btn_admin",
                        admin=True,
                    ),
                ),
            )
        )
        register_view(
            ViewDecl(
                id=f"menu:mod:{name}:admin",
                kind="menu",
                scope="mods/admin",
                admin_surface=True,
                title_emoji="🔓",
                buttons=(
                    ButtonDecl(
                        label_key="ui.homes.btn_sync",
                        sync=True,
                    ),
                ),
            )
        )
        declared += 2
    return declared


__all__ = ["declare_mod_views"]
