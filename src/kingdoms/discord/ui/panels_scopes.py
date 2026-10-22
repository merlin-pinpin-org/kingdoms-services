"""The platform's channel-tree scopes — generated from views.yaml.

The scopes section of ``config/views.yaml`` is the single source of
truth (kingdoms#179): the loader turns it into the ScopeSpec the
Panels SDK provisions (channels, categories, access bands). The
sync button re-provisions from the same declaration; a missing or
broken section fails loudly at boot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kingdoms.discord.ui.panels import ChannelDecl, ScopeSpec

CONFIG = Path("config/views.yaml")


def load_scopes_config(config_dir: Path | str | None = None) -> dict[str, Any]:
    """Read the scopes section of the views config."""
    path = Path(config_dir) / "views.yaml" if config_dir else CONFIG
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    scopes = (data or {}).get("scopes")
    if not isinstance(scopes, dict):
        raise ValueError(f"{path}: the views config must carry a 'scopes' mapping")
    return scopes


def scopes_from_config(config_dir: Path | str | None = None) -> dict[str, ScopeSpec]:
    """Turn the YAML scopes into ScopeSpecs (fail loudly on a bad entry)."""
    scopes = load_scopes_config(config_dir)
    out: dict[str, ScopeSpec] = {}
    for scope, raw in scopes.items():
        if not isinstance(raw, dict):
            raise ValueError(f"views config: scope {scope!r} must be a mapping")
        channels = []
        for decl in raw.get("channels", []) or []:
            channels.append(
                ChannelDecl(
                    key=str(decl["key"]),
                    label_key=str(decl.get("label", "")),
                    kind=str(decl.get("kind", "text")),
                    access=str(decl.get("access", "public")),
                )
            )
        out[str(scope)] = ScopeSpec(
            scope=str(scope),
            category_key=str(raw.get("category", "") or ""),
            channels=tuple(channels),
        )
    return out


def register_platform_scopes(config_dir: Path | str | None = None) -> None:
    """Register every declared scope (idempotent, boot-time)."""
    from kingdoms.discord.ui.panels import register_scope

    for spec in scopes_from_config(config_dir).values():
        register_scope(spec)


__all__ = ["load_scopes_config", "register_platform_scopes", "scopes_from_config"]
