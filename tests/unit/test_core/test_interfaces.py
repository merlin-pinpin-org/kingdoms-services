"""Unit tests for the platform interfaces."""

from __future__ import annotations

import pytest

from kingdoms.core.interfaces.platform import IChannel, IMessage, IPlatform, IUser, IWorkflow


class FakeUser(IUser):
    @property
    def id(self) -> str:
        return "user-1"

    @property
    def display_name(self) -> str:
        return "PlayerOne"


class FakeChannel(IChannel):
    @property
    def id(self) -> str:
        return "channel-1"

    @property
    def name(self) -> str:
        return "general"


class FakeMessage(IMessage):
    @property
    def id(self) -> str:
        return "message-1"

    @property
    def content(self) -> str:
        return "hello"

    @property
    def author(self) -> IUser:
        return FakeUser()

    @property
    def channel(self) -> IChannel:
        return FakeChannel()


class FakePlatform(IPlatform):
    async def send_message(self, channel: IChannel, content: str) -> IMessage:
        return FakeMessage()

    async def send_dm(self, user: IUser, content: str) -> IMessage:
        return FakeMessage()

    async def create_channel(self, guild_id: str, category: str) -> IChannel:
        return FakeChannel()

    async def assign_role(self, user: IUser, role_key: str) -> None:
        return None


class FakeWorkflow(IWorkflow):
    async def start(self, context: dict) -> None:
        return None

    async def handle_interaction(self, event: dict) -> None:
        return None

    def steps(self) -> list[str]:
        return ["start", "ask_name", "confirm"]


async def test_ipatform_contract_can_be_implemented() -> None:
    platform = FakePlatform()
    channel = await platform.create_channel("guild-1", "registration")
    assert channel.id == "channel-1"


async def test_imessage_exposes_author_and_channel() -> None:
    message = FakeMessage()
    assert message.author.display_name == "PlayerOne"
    assert message.channel.name == "general"


def test_iworkflow_steps_are_ordered() -> None:
    workflow = FakeWorkflow()
    assert workflow.steps()[0] == "start"


def test_interfaces_are_abstract() -> None:
    with pytest.raises(TypeError):
        IPlatform()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        IUser()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        IChannel()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        IMessage()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        IWorkflow()  # type: ignore[abstract]
