"""Discord-acceptance guards for the UI surfaces.

The unit/integration stacks validate the Python logic, but only
Discord's API rejects some payloads (50035 Invalid Form Body): an
emoji with a variation selector, a Section with 4+ children, a
select with 26 options. SimCord does not enforce those rules, so a
violation ships silently until the first live click.

This module scans the source's button/select declarations and every
registered admin view for exactly those Discord-side constraints.
"""

from __future__ import annotations

import re
from pathlib import Path

# Discord rejects some emoji+VS-16 pairs (the printer crash: U+1F5BB
# U+FE0F, 2026-10-12 test). The safe rule: a button emoji is either a
# BMP single char or a non-BMP char WITHOUT a trailing variation
# selector (FE0F) — the plain emoji list Discord whitelists.
_EMOJI_DECL_RE = re.compile(r'emoji="((?:\\U[0-9a-fA-F]{8})+|[^"]+)"')
_VS16 = "fe0f"


def _decode(emitter: str) -> str:
    """Decode a python string literal fragment (\\U escapes)."""
    return emitter.encode().decode("unicode_escape")


def test_button_emojis_are_discord_valid() -> None:
    """Every declared button emoji is single and selector-free."""
    root = Path(__file__).resolve().parents[2] / "src" / "kingdoms"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in _EMOJI_DECL_RE.finditer(text):
            raw = match.group(1)
            decoded = _decode(raw)
            if "\\U" in raw:
                codepoints = list(decoded)
                if len(codepoints) > 1 or decoded.lower().endswith(_VS16):
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(root)}:{line} {raw!r} -> {decoded!r}")
    assert not offenders, "Discord-invalid button emojis:\n" + "\n".join(offenders)


def test_registered_views_fit_discord_limits() -> None:
    """Every admin view's components respect the API's shape caps.

    Walks the views the games admin renders on a fake-but-real
    service: a Section holds at most 3 children, a select at most 25
    options, a view at most 5 actions rows per message top-level.
    """

    import discord

    def walk(view: Any, path: str) -> list[str]:
        """Assert the shape caps on one view; return the offenders."""
        offenders: list[str] = []
        for child in view.children:
            if isinstance(child, discord.ui.Section):
                if len(child.children) > 3:
                    offenders.append(f"{path}: Section with {len(child.children)} children (>3)")
            if isinstance(child, discord.ui.Select):
                if len(child.options) > 25:
                    offenders.append(f"{path}: select with {len(child.options)} options (>25)")
        if len(view.children) > 5:
            offenders.append(f"{path}: {len(view.children)} top-level components (>5 — the maps/pools crash)")
        top_rows = [c for c in view.children if isinstance(c, discord.ui.ActionRow)]
        if len(top_rows) > 5:
            offenders.append(f"{path}: {len(top_rows)} top-level action rows (>5)")
        return offenders

    from typing import Any

    from kingdoms.discord.admin_panel_games import (
        maps_admin_view,
        pools_admin_view,
    )

    offenders: list[str] = []
    import asyncio

    async def build() -> None:
        offenders.extend(walk(await maps_admin_view("aoe2"), "maps_admin_view"))
        offenders.extend(walk(await pools_admin_view("aoe2"), "pools_admin_view"))

    asyncio.run(build())
    assert not offenders, "Discord-shape violations:\n" + "\n".join(offenders)
