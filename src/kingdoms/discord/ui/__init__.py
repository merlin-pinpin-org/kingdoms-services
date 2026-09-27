"""Discord UI components: the UI SDK plus the reusable archetypes.

Everything user-facing is built through :mod:`.factory` — the
declarative bricks (Text, Section, Row, Button, Container…) behind
``UILayout``/``UIEmbed``. Never build ``discord.ui`` objects directly
in a feature: the SDK enforces the ADR-0009 layout rules (text and
component budgets, Section accessory constraints) at build time.

On top of the bricks (kingdoms-services#13):

- :mod:`.embeds` — sober, lightweight status/user/ladder embeds;
- :mod:`.layouts` — Components V2 counterparts of the same cards;
- :mod:`.views` — interactive views (confirmation, game selection,
  pagination) with the runtime permission checks (#55) at click time;
- :mod:`.modals` — forms (registration, feedback);
- :mod:`.components` — consistent button/select builders;
- :mod:`.screens` — paged screens (rankings, config panels, match
  reports) editing in place.
"""

from kingdoms.discord.ui.components import (
    ButtonBuilder,
    SelectBuilder,
)
from kingdoms.discord.ui.embeds import (
    EmbedBuilder,
    LadderEmbedBuilder,
    UserEmbedBuilder,
)
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
from kingdoms.discord.ui.layouts import (
    LadderLayout,
    StatusLayout,
    UserProfileLayout,
    build_ladder_layout,
    build_status_layout,
    build_user_profile_layout,
)
from kingdoms.discord.ui.modals import (
    FeedbackModal,
    RegistrationModal,
)
from kingdoms.discord.ui.screens import (
    PaginatedScreen,
    Ranking,
    build_config_panel,
    build_match_report,
    render_ranking,
)
from kingdoms.discord.ui.views import (
    ConfirmationView,
    GameSelectionView,
    PaginationView,
)

__all__ = [
    "BLURPLE",
    "GREEN",
    "Action",
    "Button",
    "ButtonBuilder",
    "ChannelSelect",
    "ConfirmationView",
    "Container",
    "EmbedBuilder",
    "FeedbackModal",
    "GameSelectionView",
    "LadderEmbedBuilder",
    "LadderLayout",
    "Option",
    "PaginatedScreen",
    "PaginationView",
    "Ranking",
    "RegistrationModal",
    "Row",
    "Section",
    "SelectBuilder",
    "SelectMenu",
    "Separator",
    "StatusLayout",
    "Text",
    "Thumbnail",
    "UIEmbed",
    "UILayout",
    "UILayoutError",
    "UserEmbedBuilder",
    "UserProfileLayout",
    "build_config_panel",
    "build_ladder_layout",
    "build_match_report",
    "build_status_layout",
    "build_user_profile_layout",
    "render_ranking",
]
