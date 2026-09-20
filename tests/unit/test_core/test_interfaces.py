"""Unit tests for the platform interface Protocols (ADR-0011)."""

from __future__ import annotations

from kingdoms.core.interfaces.platform import IChannel, IMessage, IPlatform, IUser, IWorkflow


class FakeUser:
    @property
    def id(self) -> str:
        return "user-1"

    @property
    def display_name(self) -> str:
        return "PlayerOne"


class FakeChannel:
    @property
    def id(self) -> str:
        return "channel-1"

    @property
    def name(self) -> str:
        return "general"


class FakeMessage:
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


class FakePlatform:
    async def send_message(self, channel: IChannel, content: str) -> IMessage:
        return FakeMessage()

    async def send_dm(self, user: IUser, content: str) -> IMessage:
        return FakeMessage()

    async def create_channel(self, guild_id: str, category: str) -> IChannel:
        return FakeChannel()

    async def assign_role(self, user: IUser, role_key: str) -> None:
        return None


class FakeWorkflow:
    async def start(self, context: dict[str, object]) -> None:
        return None

    async def handle_interaction(self, event: dict[str, object]) -> None:
        return None

    def steps(self) -> list[str]:
        return ["start", "ask_name", "confirm"]


async def test_ipatform_contract_can_be_satisfied() -> None:
    platform: IPlatform = FakePlatform()
    channel = await platform.create_channel("guild-1", "example:announce")
    assert channel.id == "channel-1"


async def test_imessage_exposes_author_and_channel() -> None:
    message: IMessage = FakeMessage()
    assert message.author.display_name == "PlayerOne"
    assert message.channel.name == "general"


def test_iworkflow_steps_are_ordered() -> None:
    workflow: IWorkflow = FakeWorkflow()
    assert workflow.steps()[0] == "start"


def test_concrete_classes_satisfy_protocols_at_runtime() -> None:
    assert isinstance(FakeUser(), IUser)
    assert isinstance(FakeChannel(), IChannel)
    assert isinstance(FakeMessage(), IMessage)
    assert isinstance(FakePlatform(), IPlatform)
    assert isinstance(FakeWorkflow(), IWorkflow)


def test_duck_typed_adapters_satisfy_protocols_without_inheritance() -> None:
    class PlainUser:
        @property
        def id(self) -> str:
            return "user-2"

        @property
        def display_name(self) -> str:
            return "DuckTyped"

    def accepts_user(user: IUser) -> str:
        return user.display_name

    user: IUser = PlainUser()
    assert isinstance(PlainUser(), IUser)
    assert accepts_user(user) == "DuckTyped"
