"""MockDiscord: in-memory mocks for Discord objects, views and interactions.

Public surface lives in :mod:`tests.mocks.discord_mock`; this package
exports it for convenience so tests can do
``from tests.mocks import MockInteraction``.
"""

from tests.mocks.discord_mock import (
    MockCategoryChannel,
    MockClient,
    MockDMChannel,
    MockFollowup,
    MockGuild,
    MockInteraction,
    MockMember,
    MockMessage,
    MockModal,
    MockResponse,
    MockRole,
    MockTextChannel,
    MockUser,
    MockView,
    MockVoiceChannel,
    build_button_view,
    build_layout_view,
    build_select_view,
)

__all__ = [
    "MockCategoryChannel",
    "MockClient",
    "MockDMChannel",
    "MockFollowup",
    "MockGuild",
    "MockInteraction",
    "MockMember",
    "MockMessage",
    "MockModal",
    "MockResponse",
    "MockRole",
    "MockTextChannel",
    "MockUser",
    "MockView",
    "MockVoiceChannel",
    "build_button_view",
    "build_layout_view",
    "build_select_view",
]
