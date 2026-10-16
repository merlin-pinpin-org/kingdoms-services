"""Architectural guards: the two silent button-killers of 2026-10-16.

Two classes of failure passed the whole CI suite and only surfaced on the
test guild (each killed three admin buttons at once):

1. **Invalid emoji** — Discord's API rejects 50035 (Invalid Form Body,
   ``Invalid emoji``) for codepoints outside its emoji set (e.g. U+1F5F2
   "lightning", U+1F5C2 "card index dividers"). The button never opens;
   nothing user-facing says why. The pragmatic CI rule: the U+1F5xx
   Miscellaneous Symbols block is where almost all the invalid ones live —
   a small allowlist of known-good symbols guards it, everything else in
   that block fails the build.

2. **Double defer** — a callback that defers (or sends a modal) and then
   calls a handler that defers again raises ``InteractionResponded``; the
   click dies silently. The rule: a DynamicItem callback that dispatches
   to a `_handle_*` function from ``admin_persistent`` must not defer
   first — those handlers own their own acknowledgment.
"""

from __future__ import annotations

import pathlib
import re

# The known-good U+1F5xx symbols used on components today (validated live
# against Discord). Anything else in the block must be added here WITH a
# live validation first.
_GOOD_1F5XX = {
    0x1F512,  # lock
    0x1F513,  # open lock
    0x1F517,  # link
    0x1F527,  # wrench
    0x1F52A,  # knife (civs)
    0x1F52E,  # crystal ball
    0x1F570,  # clock
    0x1F576,  # sunglasses
    0x1F577,  # spider
    0x1F578,  # spider web
    0x1F579,  # joystick
    0x1F587,  # linked paperclips
    0x1F58A,  # pen
    0x1F58B,  # fountain pen
    0x1F5FA,  # world map
    0x1F5FB,  # mount fuji
    0x1F5FC,  # tokyo tower
    0x1F5FD,  # statue of liberty
    0x1F5DE,  # bookmark tabs (labels only — NOT valid as component emoji)
    0x1F5D1,  # wastebasket
}

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_component_emojis_are_discord_valid() -> None:
    """Every emoji in the 1F5xx symbol block must be API-validated.

    The block is where the API-invalid codepoints concentrate (U+1F5F2
    killed two buttons with 50035). A new symbol requires a live check
    before joining the allowlist.
    """
    offenders: list[str] = []
    for path in ROOT.glob("src/kingdoms/**/*.py"):
        if "rpc_generated" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for char in text:
            cp = ord(char)
            if 0x1F500 <= cp <= 0x1F5FF and cp not in _GOOD_1F5XX:
                offenders.append(f"{path.name}: U+{cp:04X} {char!r}")
    assert not offenders, (
        "Discord rejects these emoji with 50035 Invalid emoji (the button never opens; "
        "the 2026-10-16 lesson): " + "; ".join(sorted(set(offenders)))
    )


_DEFER_RE = re.compile(r"await\s+interaction\.response\.defer\(")
_HANDLE_RE = re.compile(r"from\s+kingdoms\.discord\.admin_persistent\s+import\s+(_handle_\w+)")


def test_callbacks_do_not_double_defer() -> None:
    """A DynamicItem callback must not defer before a self-acking handler.

    ``_handle_roles`` (and every ``_handle_*`` in admin_persistent) owns
    its own acknowledgment — deferring before the call raises
    InteractionResponded and the click dies (the 2026-10-16 Roles bug).
    """
    offenders: list[str] = []
    for path in ROOT.glob("src/kingdoms/**/*.py"):
        if "rpc_generated" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for match in _HANDLE_RE.finditer(text):
            # find the enclosing async def callback block
            start = text.rfind("async def callback", 0, match.start())
            if start == -1:
                continue
            # the defer must not appear between the callback start and the handler call
            block = text[start : match.start()]
            if _DEFER_RE.search(block):
                offenders.append(f"{path.name}: defers before {match.group(1)}")
    assert not offenders, (
        "These callbacks defer before calling a handler that acks itself "
        "(InteractionResponded — the 2026-10-16 Roles lesson): "
        + "; ".join(sorted(set(offenders)))
    )
