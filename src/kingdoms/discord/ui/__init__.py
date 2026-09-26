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
    Action,
    Button,
    ChannelSelect,
    Container,
    Option,
    Row,
    Section,
    SelectMenu,
    Separator,
    Text,
    Thumbnail,
    UIEmbed,
    UILayout,
    UILayoutError,
)
from kingdoms.discord.ui.screens import (
    PaginatedScreen,
    Ranking,
    build_config_panel,
    build_match_report,
    render_ranking,
)

__all__ = [
    "BLURPLE",
    "GREEN",
    "Action",
    "Button",
    "ChannelSelect",
    "Container",
    "Option",
    "PaginatedScreen",
    "Ranking",
    "Row",
    "Section",
    "SelectMenu",
    "Separator",
    "Text",
    "Thumbnail",
    "UIEmbed",
    "UILayout",
    "UILayoutError",
    "build_config_panel",
    "build_match_report",
    "render_ranking",
]
