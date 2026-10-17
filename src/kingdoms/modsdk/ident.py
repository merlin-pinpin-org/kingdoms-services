"""The modder SDK ids: entities, views, footers — premade, one convention.

Every id the platform quotes is built the same way, from documented
parts, and the modder never hand-formats one:

- **Entities** — ``<mod>:<game>:<scope>:<slug>``: the mod's name,
  the game it belongs to (``aoe2``), the visibility scope
  (``public`` or a guild id), the name slug. Same convention as the
  platform's own entities (``map:aoe2:public:nomad``).
- **Views** — ``view:<scope>:<slug>``: the scope is the surface's
  perimeter (``home``, ``bot``, the game or mod key), the slug the
  surface itself (``view:aoe2:live``, ``view:ladder:news``).
- **Footers** — every panel and post surfaces its id as a small-text
  line, so a designer can quote it to the bot. ``footer`` renders it.

Mods must build ids and footers through this module only.
"""

from __future__ import annotations

from kingdoms.core.ids import footer as _footer
from kingdoms.core.ids import slug_id

__all__ = ["entity_id", "footer", "view_id"]


def entity_id(mod_key: str, entity_key: str, game_key: str, name: str, scope: str = "public") -> str:
    """Build one mod entity's stable id.

    ``<mod>:<game>:<scope>:<slug>`` — e.g. a kingdoms territory on the
    public catalog of the aoe2 game:
    ``kingdoms:territory:aoe2:public:buda_hills``. The slug keeps
    underscores (``slug_id``); the scope is ``public`` or the owning
    guild id.
    """
    return f"{mod_key}:{entity_key}:{game_key}:{scope}:{slug_id(name)}"


def view_id(scope: str, slug: str) -> str:
    """Build one pinned view's registry id: ``view:<scope>:<slug>``.

    ``view:home``, ``view:bot:admin``, ``view:aoe2:live``,
    ``view:ladder:news`` — the id is the pin's registry key and its
    displayed footer.
    """
    return f"view:{scope}:{slug_id(slug).replace('_', '-')}"


def footer(*ids: str) -> str:
    """Render the ids footer line of a panel or post (empty ids drop)."""
    return _footer(*ids)
