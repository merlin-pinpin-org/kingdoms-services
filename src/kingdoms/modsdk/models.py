"""The modder SDK declarations: entities, fields, actions, menus.

Plain dataclasses — a mod declares its universe and the SDK owns
every technical consequence (storage, Discord, permissions, i18n).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

Entity = dict[str, Any]


@dataclass(frozen=True)
class Field:
    """One field of an entity: a key, a display label, a kind.

    ``kind`` picks the rendering and the editor: text, long_text,
    number, bool, url, image, select (with ``choices``). ``choices``
    is a (value, label_key) tuple for select fields. ``required``
    drives both the editor and the validation.
    """

    key: str
    label_key: str
    kind: str = "text"
    required: bool = False
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EntitySpec:
    """One entity type of a mod: territories, kingdoms, matches...

    ``states`` lists the entity's lifecycle (``draft``, ``published``,
    ``closed``...); ``initial_state`` seeds a new entity; ``id_prefix``
    builds the stable ids; ``title_field`` and ``image_field`` pick
    the card's headline and thumbnail.
    """

    key: str
    id_prefix: str
    label_key: str
    states: tuple[str, ...] = ()
    initial_state: str = ""
    fields: tuple[Field, ...] = ()
    title_field: str = "name"
    image_field: str = "image_url"
    source_field: str = "source_url"


@dataclass(frozen=True)
class ActionSpec:
    """One interaction on an entity: claim, publish, close, delete.

    ``from_states`` gates the button — the SDK greys it when the
    entity's state is not in the list (empty = always available);
    ``permission`` is checked at click time (``admin`` = bot-admins,
    ``owner`` = the entity's owner, ``everyone``); ``confirm`` asks
    the user to retype the entity's title before the handler runs
    (for destructive actions); ``prompt`` opens a modal instead of
    running the handler directly (for actions that need input).

    The handler receives ``(entity, actor, input)`` and returns an
    ``ActionResult``; the SDK persists and re-renders — the mod
    never touches Mongo or Discord.
    """

    key: str
    label_key: str
    from_states: tuple[str, ...] = ()
    permission: str = "admin"
    confirm: bool = False
    prompt: tuple[Field, ...] = ()
    handler: Callable[..., Coroutine[Any, Any, Any]] | None = None


@dataclass(frozen=True)
class EntityPanelSpec:
    """One entity's public card (its forum post) + its admin surface.

    The SDK renders the card from the entity spec (title, fields,
    image, source, id footer) in the forum the tree provisions, and
    replaces every action button with the single ⚙️ Admin button —
    the admin ephemeral carries the actions, greyed by state.
    """

    entity: str
    channel: str
    order: tuple[str, ...] = ()
    admin_actions: tuple[ActionSpec, ...] = ()


@dataclass(frozen=True)
class MenuSpec:
    """One frozen pinned menu of the mod's channel tree.

    ``build`` renders the menu's layout — the SDK pins it (registry
    ``view:<guild>:<slug>``), heals it, and every button of the
    menu opens an ephemeral (the frozen-menu contract). ``scope``
    places the menu in a folder (``mods/<mod>``); ``channel_key``
    names the channel inside it.
    """

    key: str
    label_key: str
    scope: str
    channel_key: str
    build: Callable[..., Coroutine[Any, Any, Any]] | None = None
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionResult:
    """What a handler returns: the (optionally) updated entity.

    Pass ``entity=None`` for a message-only result (nothing
    persists); pass a dict to merge into the stored entity (the
    SDK persists and every surface re-renders at the next sync).
    """

    entity: Entity | None = None
    message: str = ""
    new_state: str = ""


__all__ = [
    "ActionResult",
    "ActionSpec",
    "EntityPanelSpec",
    "EntitySpec",
    "Field",
    "MenuSpec",
]
