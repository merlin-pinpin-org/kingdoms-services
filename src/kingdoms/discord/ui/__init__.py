"""Discord UI components: the UI SDK plus legacy view/modal seams.

Everything user-facing is built through :mod:`.factory` — the
declarative bricks (Text, Section, Row, Button, Container…) behind
``UILayout``/``UIEmbed``. Never build ``discord.ui`` objects directly
in a feature: the SDK enforces the ADR-0009 layout rules (text and
component budgets, Section accessory constraints) at build time.
"""

from kingdoms.discord.ui.factory import (
    BLURPLE,
    GREEN,
    Button,
    Container,
    Row,
    Section,
    Separator,
    Text,
    Thumbnail,
    UIEmbed,
    UILayout,
    UILayoutError,
)

__all__ = [
    "BLURPLE",
    "GREEN",
    "Button",
    "Container",
    "Row",
    "Section",
    "Separator",
    "Text",
    "Thumbnail",
    "UIEmbed",
    "UILayout",
    "UILayoutError",
]
