"""Layout fingerprints for the pinned surfaces.

A pinned message's rendered layout is hashed (custom ids, labels,
options) so a refresh edits **only** when the built layout differs
from what the message already shows — Discord's edit quota (30046)
stays cold on steady-state cycles.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _walk(component: Any) -> Any:
    """Walk a component tree into a comparable structure."""
    if isinstance(component, (list, tuple)):
        return [ _walk(c) for c in component ]
    if hasattr(component, "custom_id"):
        return {
            "id": getattr(component, "custom_id", None),
            "label": getattr(component, "label", None),
            "children": _walk(getattr(component, "children", []) or []),
        }
    if hasattr(component, "options"):
        return {"options": [str(getattr(o, "label", o)) for o in component.options]}
    if hasattr(component, "children"):
        return {"children": _walk(component.children)}
    return str(component)


def layout_fingerprint(layout: Any) -> str:
    """Hash a layout (or rendered component tree); '' when unreadable."""
    if not layout:
        return ""
    try:
        payload = json.dumps(_walk(layout), sort_keys=True, default=str)
    except Exception:
        return ""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
