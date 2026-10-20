"""The platform's declared views — loaded from ``config/views.yaml``.

The YAML is the single source of truth (docs/DEVELOPMENT/VIEWS.md in
the kingdoms repo renders it for humans): every menu, panel, button
and navigation target is declared there, and this loader turns it
into the runtime registry. Adding a menu is YAML lines, zero code —
the menu builder, the pins and the router all render from the
registry.

An action button (``action: <action-id>``) references a behavior the
code wires at boot (``wire_action``); an unwired action renders
disabled, never dead.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from kingdoms.discord.views_registry import (
    KIND_MENU,
    KIND_PANEL,
    ButtonDecl,
    ViewDecl,
    register_view,
)

logger = logging.getLogger("kingdoms.views.declared")

VIEWS_CONFIG = Path("config/views.yaml")


def load_views_config(config_dir: Path | str | None = None) -> dict[str, Any]:
    """Read and parse the views YAML (anchors and aliases resolve here)."""
    path = Path(config_dir) / "views.yaml" if config_dir else VIEWS_CONFIG
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("views"), dict):
        raise ValueError(f"{path}: the views config must carry a 'views' mapping")
    views: dict[str, Any] = data["views"]
    return views


def _parse_button(view_id: str, raw: dict[str, Any]) -> ButtonDecl:
    """Turn one YAML button entry into a ButtonDecl (fail loudly)."""
    if not isinstance(raw, dict):
        raise ValueError(f"views config: {view_id}: a button must be a mapping, got {raw!r}")
    button = ButtonDecl(
        target=str(raw.get("target", "") or ""),
        label_key=str(raw.get("label", "") or ""),
        admin=bool(raw.get("admin", False)),
        sync=bool(raw.get("sync", False)),
        disabled=bool(raw.get("disabled", False)),
        action=str(raw.get("action", "") or ""),
    )
    return button


def declare_views_from_config(config_dir: Path | str | None = None) -> None:
    """Load every declared view from the YAML into the registry."""
    views = load_views_config(config_dir)
    for view_id, raw in views.items():
        if not isinstance(raw, dict):
            raise ValueError(f"views config: {view_id}: a view must be a mapping")
        kind = str(raw.get("kind", ""))
        if kind not in (KIND_MENU, KIND_PANEL):
            raise ValueError(f"views config: {view_id}: kind must be menu or panel, got {kind!r}")
        buttons = tuple(_parse_button(view_id, b) for b in raw.get("buttons", []) or ())
        register_view(
            ViewDecl(
                id=str(view_id),
                kind=kind,
                scope=str(raw.get("scope", "") or ""),
                buttons=buttons,
            )
        )
    logger.info("VIEWS declared from config: %d views", len(views))


def declare_platform_views(config_dir: Path | str | None = None) -> None:
    """Declare every platform view (compatibility name, boot-time)."""
    declare_views_from_config(config_dir)


__all__ = ["declare_platform_views", "declare_views_from_config", "load_views_config"]
