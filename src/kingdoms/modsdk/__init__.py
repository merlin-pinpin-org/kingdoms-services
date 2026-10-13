"""The modder SDK: declare entities, actions and menus — nothing else.

A game designer (or their agent) building a mod writes three kinds
of declarations and no platform code:

- **Entities** — the mod's data (``EntitySpec`` + ``Field``): a
  territory, a kingdom, a match. The SDK stores them (one generic
  Mongo collection per entity), never the mod.
- **Actions** — the mod's interactions (``ActionSpec``): claim,
  publish, close, delete. The SDK renders them as Discord buttons
  (greyed by the entity's state through ``available``), checks the
  permission, and hands the mod a plain dict; the mod returns the
  new state, the SDK persists and re-renders. No discord.py.
- **Menus** — ``EntityPanelSpec``/``MenuSpec``: where the mod's
  surfaces live in the channel tree. The SDK provisions the
  channels in the guild's locale, pins the frozen menus, heals
  them, and turns every button click into an ephemeral.

Everything the platform already does for its own games (the channel
tree, the ⚙️ Admin rule, the pin registry, the read-only menus)
applies to mod menus unchanged. The aoe2 game (maps, map pools,
civs) is built on this SDK — read its binding as the reference
example.
"""

from kingdoms.modsdk.models import ActionResult, ActionSpec, EntityPanelSpec, EntitySpec, Field, MenuSpec
from kingdoms.modsdk.runtime import ModBinding, bind_mod, register_dynamic_items
from kingdoms.modsdk.store import EntityStore

__all__ = [
    "ActionResult",
    "ActionSpec",
    "EntityPanelSpec",
    "EntitySpec",
    "EntityStore",
    "Field",
    "MenuSpec",
    "ModBinding",
    "bind_mod",
    "register_dynamic_items",
]
