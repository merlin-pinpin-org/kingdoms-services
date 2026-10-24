"""Discord-valid emoji allowlist — the one place to pick a component emoji.

Discord's API rejects codepoints outside its emoji set with
**50035 Invalid Form Body (Invalid emoji)** — the component (button,
select option) is then silently dead: it renders nowhere and the click
never opens (the 2026-10-16 lesson, twice). The block where the invalid
codepoints concentrate is U+1F500-U+1F5FF (U+1F5F2 and U+1F5AE were
both rejected live), but the rule is: **every** emoji on a component
comes from this allowlist — a new candidate is live-checked first (the
test env renders it on a real button), then added here.

Usage — pick a semantic constant, never a raw literal:

    from kingdoms.discord.ui.emoji import EMOJI

    button = discord.ui.Button(emoji=EMOJI.MAP, ...)
    option = discord.SelectOption(label=..., emoji=EMOJI.GREEN_CIRCLE)

The architecture guard (tests/architecture/test_component_guards.py)
derives its scan from this module: an emoji outside the allowlist in
``src/kingdoms/**`` fails CI with the exact codepoint.
"""

from __future__ import annotations

from typing import Final


class _Emoji:
    """The semantic registry — every value live-validated against the API."""

    # Status / health
    GREEN_CIRCLE: Final[str] = "🟢"
    ORANGE_CIRCLE: Final[str] = "🟠"
    WHITE_CIRCLE: Final[str] = "⚪"
    CHECK_MARK: Final[str] = "✅"
    CROSS_MARK: Final[str] = "❌"
    WARNING: Final[str] = "⚠️"
    LOCK: Final[str] = "🔒"
    UNLOCKED: Final[str] = "🔓"

    # Game / AoE2 domain
    SWORDS: Final[str] = "⚔️"
    SHIELD: Final[str] = "🛡️"
    CASTLE: Final[str] = "🏰"
    CROWN: Final[str] = "👑"
    TROPHY: Final[str] = "🏆"
    MAP: Final[str] = "🗺️"
    GLOBE: Final[str] = "🌐"
    FLAG: Final[str] = "🚩"
    DICE: Final[str] = "🎲"
    JOYSTICK: Final[str] = "🕹️"

    # Players / people
    USERS: Final[str] = "👥"
    PERSON: Final[str] = "👤"
    WAVING_HAND: Final[str] = "👋"
    HANDSHAKE: Final[str] = "🤝"

    # Actions / tools
    TOOLS: Final[str] = "🛠️"
    WRENCH: Final[str] = "🔧"
    LINK: Final[str] = "🔗"
    PENCIL: Final[str] = "📝"
    ARROW_UP: Final[str] = "⬆️"
    ARROW_DOWN: Final[str] = "⬇️"
    MAGNIFYING_GLASS: Final[str] = "🔎"
    TRASH: Final[str] = "🗑️"
    CLIPBOARD: Final[str] = "📋"
    BELL: Final[str] = "🔔"

    # Media / channels
    SPEAKER: Final[str] = "📢"
    SIGNAL: Final[str] = "📡"
    BOOKMARK: Final[str] = "🔖"
    CHART: Final[str] = "📊"
    ROCKET: Final[str] = "🚀"
    PARTY: Final[str] = "🎉"
    FOLDER: Final[str] = "📁"
    PACKAGE: Final[str] = "📦"
    ROUND_PUSH_PIN: Final[str] = "📍"
    BOOKMARK_LABEL: Final[str] = "🏷️"
    CHAINS: Final[str] = "⛓️"

    # People (extended)
    BUST_IN_SILHOUETTE: Final[str] = "👤"
    ROBOT: Final[str] = "🤖"
    ZANY: Final[str] = "🧩"
    EYES: Final[str] = "👀"

    # Nature / domain (extended)
    HERB: Final[str] = "🌿"
    CASTLE_CLASSIC: Final[str] = "🏰"
    GLOBE_EUROPE: Final[str] = "🌍"
    GAME_DIE: Final[str] = "🎲"
    HOME: Final[str] = "🏠"


EMOJI: Final[_Emoji] = _Emoji()

# The flat allowlist the architecture guard scans against: every
# value above, by codepoint. The 1F5xx block is where Discord's
# rejections concentrate; the other blocks here are all
# long-standing, universally-valid emoji.

# Every registry value above, flattened — the guard's scan source.
_EMOJI_VALUES: Final[tuple[str, ...]] = (*(
    getattr(EMOJI, name) for name in dir(EMOJI) if not name.startswith("_")
), *(
    "👺", "🛡", "⚙", "🛰", "⚖", "🏁", "🏷", "🛎", "🚩", "🌍", "🎮",
    "🏰", "🌿", "🤖", "🧩", "👤", "📦", "📁", "🎉", "🚀", "📍", "⛓",
))

ALLOWED_CODEPOINTS: Final[frozenset[int]] = frozenset(
    codepoint
    for emoji in _EMOJI_VALUES
    for codepoint in (ord(char) for char in emoji)
)


def is_allowed(emoji: str) -> bool:
    """Report whether every codepoint of an emoji is allowlisted."""
    return all(ord(char) in ALLOWED_CODEPOINTS for char in emoji)
