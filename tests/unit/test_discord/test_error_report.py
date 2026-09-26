"""Tests for the enriched error reporting (kingdoms-services#113).

Guild interaction failures ride the guild's bot-logs channel with the
git reference of the emitting line and the interaction context; DM
failures are answered in the DM itself — no guild channel to log to.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.discord.error_report import (
    emitting_frame,
    git_ref,
    interaction_context,
    report_guild_error,
    report_interaction_error,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser


class _FakeLogsService:
    """LogService stand-in recording delivered lifecycle events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def log_event(self, guild_id: str, event: Any) -> None:
        self.events.append((guild_id, event.message))


def _raise_in_kingdoms_code() -> BaseException:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        return exc


def test_emitting_frame_returns_none_outside_src() -> None:
    """Test code is not runtime code: no src/kingdoms frame, no ref.

    The error path degrades to a plain message — never a crash of the
    crash reporter.
    """
    exc = _raise_in_kingdoms_code()
    assert emitting_frame(exc) is None
    assert git_ref(None, "") == ""


def test_git_ref_pins_the_source_line_with_a_synthetic_frame() -> None:
    import inspect

    def _throw() -> None:
        raise ValueError("boom")

    try:
        _throw()
    except ValueError as exc:
        frame = inspect.getinnerframes(exc.__traceback__)[-1]
        tree_url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/980a037abcd1234"
        ref = git_ref(frame, tree_url)
        assert ref == ""  # tests/ frames are not pinned, whatever the sha


def test_interaction_context_renders_who_when_where_what() -> None:
    interaction = MockInteraction(user=MockUser(id=42), guild=MockGuild(id=77))
    interaction.guild_id = 77
    context = interaction_context(interaction)
    assert "who: <@42> (`42`)" in context
    assert "where: guild 77" in context
    assert "what: component" in context
    assert "when:" in context


def test_interaction_context_renders_dm_explicitly() -> None:
    interaction = MockInteraction(user=MockUser(id=42), guild=None)
    interaction.guild_id = None
    context = interaction_context(interaction)
    assert "where: DM" in context


@pytest.mark.asyncio
async def test_guild_interaction_failure_rides_bot_logs() -> None:
    logs = _FakeLogsService()
    interaction = MockInteraction(user=MockUser(id=42), guild=MockGuild(id=77))
    interaction.guild_id = 77
    exc = _raise_in_kingdoms_code()
    await report_interaction_error(
        interaction,
        exc,
        "https://github.com/merlin-pinpin-org/kingdoms-services/tree/980a037abcd1234",
        logs,
    )
    assert len(logs.events) == 1
    guild_id, message = logs.events[0]
    assert guild_id == "77"
    assert "ValueError" in message
    assert "boom" in message
    assert "<@42>" in message


@pytest.mark.asyncio
async def test_dm_interaction_failure_answers_the_dm() -> None:
    logs = _FakeLogsService()
    interaction = MockInteraction(user=MockUser(id=42), guild=None)
    interaction.guild_id = None
    exc = _raise_in_kingdoms_code()
    await report_interaction_error(interaction, exc, "", logs)
    assert logs.events == []
    assert interaction.response.sent is True
    content = (interaction.response.message or "").content
    assert "ValueError" in content
    assert "where: DM" in content


@pytest.mark.asyncio
async def test_guild_error_without_interaction_fans_out_to_guilds() -> None:
    logs = _FakeLogsService()
    exc = _raise_in_kingdoms_code()
    await report_guild_error(exc, ["77", "78"], "", logs)
    assert [guild_id for guild_id, _ in logs.events] == ["77", "78"]
    assert all("boom" in message for _, message in logs.events)


class _FakeDmUser:
    """A user record whose DM channel records sends."""

    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.sent: list[str] = []

    async def create_dm(self) -> _FakeDmUser:
        return self

    async def send(self, content: str) -> None:
        self.sent.append(content)


class _FakeDmBot:
    """A client stand-in resolving admins to recording DM users."""

    def __init__(self, ids: list[int]) -> None:
        self._users = {i: _FakeDmUser(i) for i in ids}
        self.fetched: list[int] = []

    def get_user(self, user_id: int) -> _FakeDmUser | None:
        return self._users.get(user_id)

    async def fetch_user(self, user_id: int) -> _FakeDmUser:
        self.fetched.append(user_id)
        return self._users[user_id]


@pytest.mark.asyncio
async def test_admins_receive_the_crash_report_as_dm() -> None:
    logs = _FakeLogsService()
    bot = _FakeDmBot([111])
    interaction = MockInteraction(user=MockUser(id=42), guild=MockGuild(id=77))
    interaction.guild_id = 77
    exc = _raise_in_kingdoms_code()
    await report_interaction_error(
        interaction,
        exc,
        "https://github.com/merlin-pinpin-org/kingdoms-services/tree/980a037abcd1234",
        logs,
        bot=bot,
        admin_ids=("111", "not-an-id"),
    )
    assert len(bot._users[111].sent) == 1
    dm = bot._users[111].sent[0]
    assert "ValueError" in dm
    assert "<@42>" in dm


@pytest.mark.asyncio
async def test_dm_failure_also_dms_admins() -> None:
    logs = _FakeLogsService()
    bot = _FakeDmBot([111])
    interaction = MockInteraction(user=MockUser(id=42), guild=None)
    interaction.guild_id = None
    exc = _raise_in_kingdoms_code()
    await report_interaction_error(interaction, exc, "", logs, bot=bot, admin_ids=("111",))
    assert bot._users[111].sent, "admins are DM'd even for DM-origin failures"


@pytest.mark.asyncio
async def test_guild_error_dms_admins_without_interaction() -> None:
    logs = _FakeLogsService()
    bot = _FakeDmBot([111])
    exc = _raise_in_kingdoms_code()
    await report_guild_error(exc, ["77"], "", logs, bot=bot, admin_ids=("111",))
    assert bot._users[111].sent
