"""MockDiscord: in-memory mocks for Discord objects, views and interactions.

Tracked by kingdoms-services#2. Every mock subclasses the real discord.py
class so code under test can use ``isinstance`` checks against the real API
types, but instances are built from plain Python data: no gateway, no HTTP,
no rate limits.

ADR-0009 (dual Discord UI system) is respected: :class:`MockMessage` records
**both** UI surfaces a message can carry:

- embed-based: ``.embeds`` plus legacy ``.components`` (from ``ui.View``)
- Components V2: ``.layout`` (the ``ui.LayoutView`` root) whose full
  component tree is available through ``walk_children()``
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

import discord
from discord.ui import Button, Select

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


def _next_id() -> int:
    """Return a stable, process-unique snowflake-like id.

    ``hash(str)`` is randomized per process (PYTHONHASHSEED), so it must not
    be used for default mock ids; a monotonic counter keeps tests
    deterministic and collision-free.
    """
    return next(_next_id._counter)


_next_id._counter = itertools.count(100000000000000000)


type MockChannel = "MockTextChannel" | "MockVoiceChannel" | "MockCategoryChannel" | "MockDMChannel"


class MockUser(discord.User):
    """In-memory :class:`discord.User`.

    ``avatar``, ``mention`` and ``display_name`` are read-only properties on
    ``BaseUser`` (backed by an unavailable connection state) so they are
    overridden here with plain values.
    """

    def __init__(
        self,
        id: int | None = None,
        name: str = "TestUser",
        *,
        bot: bool = False,
        **kwargs: Any,
    ) -> None:
        self.id = id if id is not None else _next_id()
        self.name = name
        self.discriminator = "0000"
        self.global_name = name
        self.bot = bot
        self.system = False
        self._avatar = None
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def avatar(self) -> None:
        return self._avatar

    @avatar.setter
    def avatar(self, value: object) -> None:
        self._avatar = value  # type: ignore[assignment]

    @property
    def display_name(self) -> str:
        return self.global_name or self.name

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    def __repr__(self) -> str:
        return f"<MockUser id={self.id} name={self.name!r}>"


class MockMember(discord.Member):
    """In-memory :class:`discord.Member`.

    ``Member`` wraps an internal ``_user`` payload in the real library; here
    ``id``, ``name`` and the display properties are overridden directly.
    """

    def __init__(
        self,
        id: int | None = None,
        name: str = "TestMember",
        *,
        guild: MockGuild | None = None,
        roles: list[MockRole] | None = None,
        bot: bool = False,
        **kwargs: Any,
    ) -> None:
        self._mock_id = id if id is not None else _next_id()
        self._mock_name = name
        self._mock_bot = bot
        self._roles: list[MockRole] = list(roles or [])
        self.guild = guild or MockGuild()
        self.joined_at = None
        self.premium_since = None
        self.nick = None
        self.pending = False
        self.timed_out_until = None
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def id(self) -> int:
        return self._mock_id

    @property
    def name(self) -> str:
        return self._mock_name

    @property
    def bot(self) -> bool:
        return self._mock_bot

    @property
    def display_name(self) -> str:
        return self.nick or self._mock_name

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def roles(self) -> list[MockRole]:
        return list(self._roles)

    async def add_roles(self, *roles: discord.Role, reason: str | None = None, atomic: bool = True) -> None:
        for role in roles:
            if role not in self._roles:
                self._roles.append(role)  # type: ignore[arg-type]

    async def remove_roles(self, *roles: discord.Role, reason: str | None = None, atomic: bool = True) -> None:
        for role in roles:
            if role in self._roles:
                self._roles.remove(role)  # type: ignore[arg-type]

    def __repr__(self) -> str:
        return f"<MockMember id={self.id} name={self.name!r}>"


class MockRole(discord.Role):
    """In-memory :class:`discord.Role` with settable fields.

    ``color``, ``permissions`` and ``mention`` are read-only in discord.py
    (they recompute from the connection state) and are overridden here.
    ``color`` accepts a hex string or :class:`discord.Color`; ``permissions``
    accepts a list of permission flag names.
    """

    def __init__(
        self,
        id: int | None = None,
        name: str = "Test Role",
        *,
        color: discord.Color | str | int | None = None,
        permissions: list[str] | None = None,
        hoist: bool = False,
        mentionable: bool = False,
        **kwargs: Any,
    ) -> None:
        self.id = id if id is not None else _next_id()
        self.name = name
        self.hoist = hoist
        self.mentionable = mentionable
        self._color = self._coerce_color(color)
        self._permissions = self._coerce_permissions(permissions)
        for key, value in kwargs.items():
            setattr(self, key, value)

    @staticmethod
    def _coerce_color(value: discord.Color | str | int | None) -> discord.Color:
        if value is None:
            return discord.Color.default()
        if isinstance(value, discord.Color):
            return value
        if isinstance(value, str):
            return discord.Color.from_str(value)
        return discord.Color(value)

    @staticmethod
    def _coerce_permissions(flags: list[str] | None) -> discord.Permissions:
        permissions = discord.Permissions()
        for flag in flags or []:
            setattr(permissions, flag, True)
        return permissions

    @property
    def color(self) -> discord.Color:
        return self._color

    @color.setter
    def color(self, value: discord.Color | str | int) -> None:
        self._color = self._coerce_color(value)

    @property
    def permissions(self) -> discord.Permissions:
        return self._permissions

    @permissions.setter
    def permissions(self, flags: list[str]) -> None:
        self._permissions = self._coerce_permissions(flags)

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    def __repr__(self) -> str:
        return f"<MockRole id={self.id} name={self.name!r}>"


class MockTextChannel(discord.TextChannel):
    """In-memory :class:`discord.TextChannel` with message history capture."""

    def __init__(
        self,
        id: int | None = None,
        name: str = "test-channel",
        *,
        guild: MockGuild | None = None,
        category: MockCategoryChannel | None = None,
        position: int = 0,
        **kwargs: Any,
    ) -> None:
        self.id = id if id is not None else _next_id()
        self.name = name
        self.position = position
        self.guild = guild or MockGuild()
        self._category = category
        self._permissions: dict[tuple[int, bool], discord.PermissionOverwrite | None] = {}
        self.messages: list[MockMessage] = []
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def type(self) -> discord.ChannelType:
        return discord.ChannelType.text

    @property
    def category(self) -> MockCategoryChannel | None:
        return self._category

    @category.setter
    def category(self, value: MockCategoryChannel | None) -> None:
        self._category = value

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        **kwargs: Any,
    ) -> MockMessage:
        message = MockMessage(content=content, embed=embed, view=view, channel=self)
        self.messages.append(message)
        return message

    async def edit(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    async def set_permissions(
        self,
        target: discord.Member | discord.Role,
        overwrite: discord.PermissionOverwrite | None = None,
        *,
        reason: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._permissions[(target.id, isinstance(target, discord.Role))] = overwrite

    def permissions_for(self, member: discord.abc.User) -> discord.Permissions:
        """In-memory permissions: union of the member's role permissions.

        Overrides the connection-state computation of discord.py: a
        member's permissions are the union of its roles' permissions
        (no overwrite model in the mock — sendable checks stay simple).
        """
        permissions = discord.Permissions()
        for role in getattr(member, "_roles", ()):
            role_permissions = getattr(role, "_permissions", None)
            if isinstance(role_permissions, discord.Permissions):
                permissions |= role_permissions
        return permissions

    def permission_overwrite_for(self, target: discord.Member | discord.Role) -> discord.PermissionOverwrite | None:
        return self._permissions.get((target.id, isinstance(target, discord.Role)))

    def __repr__(self) -> str:
        return f"<MockTextChannel id={self.id} name={self.name!r}>"


class MockVoiceChannel(discord.VoiceChannel):
    """In-memory :class:`discord.VoiceChannel` (structure only, no audio)."""

    def __init__(
        self,
        id: int | None = None,
        name: str = "test-voice",
        *,
        guild: MockGuild | None = None,
        category: MockCategoryChannel | None = None,
        position: int = 0,
        **kwargs: Any,
    ) -> None:
        self.id = id if id is not None else _next_id()
        self.name = name
        self.position = position
        self.guild = guild or MockGuild()
        self._category = category
        self._connected_members: list[discord.Member] = []
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def type(self) -> discord.ChannelType:
        return discord.ChannelType.voice

    @property
    def category(self) -> MockCategoryChannel | None:
        return self._category

    @property
    def members(self) -> list[discord.Member]:
        return list(self._connected_members)

    def __repr__(self) -> str:
        return f"<MockVoiceChannel id={self.id} name={self.name!r}>"


class MockCategoryChannel(discord.CategoryChannel):
    """In-memory :class:`discord.CategoryChannel` grouping other channels."""

    def __init__(
        self,
        id: int | None = None,
        name: str = "Test Category",
        *,
        guild: MockGuild | None = None,
        position: int = 0,
        **kwargs: Any,
    ) -> None:
        self.id = id if id is not None else _next_id()
        self.name = name
        self.position = position
        self.guild = guild or MockGuild()
        self._channels: list[MockChannel] = []
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def type(self) -> discord.ChannelType:
        return discord.ChannelType.category

    @property
    def channels(self) -> list[MockChannel]:
        return list(self._channels)

    def add_channel(self, channel: MockChannel) -> None:
        if channel not in self._channels:
            self._channels.append(channel)

    def remove_channel(self, channel: MockChannel) -> None:
        if channel in self._channels:
            self._channels.remove(channel)

    def __repr__(self) -> str:
        return f"<MockCategoryChannel id={self.id} name={self.name!r}>"


class MockDMChannel(discord.DMChannel):
    """In-memory :class:`discord.DMChannel` with message history capture."""

    def __init__(
        self,
        recipient: MockUser | None = None,
        id: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.id = id if id is not None else _next_id()
        self._recipient = recipient or MockUser(name="TestRecipient")
        self.messages: list[MockMessage] = []
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def recipient(self) -> MockUser:
        return self._recipient

    @property
    def type(self) -> discord.ChannelType:
        return discord.ChannelType.private

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        **kwargs: Any,
    ) -> MockMessage:
        message = MockMessage(content=content, embed=embed, view=view, channel=self)
        self.messages.append(message)
        return message

    def __repr__(self) -> str:
        return f"<MockDMChannel id={self.id} recipient={self.recipient.name!r}>"


class MockGuild(discord.Guild):
    """In-memory :class:`discord.Guild` tracking roles, members and channels.

    ``icon``, ``members``, ``roles``, ``text_channels`` and ``me`` are
    read-only properties in discord.py and are overridden here with
    in-memory containers.
    """

    def __init__(
        self,
        id: int | None = None,
        name: str = "Test Guild",
        *,
        owner_id: int | None = None,
        me: MockMember | None = None,
        **kwargs: Any,
    ) -> None:
        self.id = id if id is not None else _next_id()
        self.name = name
        self.owner_id = owner_id if owner_id is not None else self.id
        self._roles: dict[int, MockRole] = {}
        self._members: dict[int, MockMember] = {}
        self._channels: dict[int, MockChannel] = {}
        self._me = me if me is not None else MockMember(name="TestBot", guild=self, bot=True)
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def icon(self) -> None:
        return None

    @property
    def me(self) -> MockMember:
        return self._me

    @me.setter
    def me(self, value: MockMember) -> None:
        self._me = value

    @property
    def roles(self) -> list[MockRole]:
        return list(self._roles.values())

    @property
    def members(self) -> list[MockMember]:
        return list(self._members.values())

    @property
    def text_channels(self) -> list[MockTextChannel]:
        return [ch for ch in self._channels.values() if isinstance(ch, MockTextChannel)]

    @property
    def channels(self) -> list[MockChannel]:
        return list(self._channels.values())

    @property
    def system_channel(self) -> MockTextChannel | None:
        """The guild's system channel (overridden in-memory for tests)."""
        channel = self.__dict__.get("_system_channel")
        return channel if isinstance(channel, MockTextChannel) else None

    @system_channel.setter
    def system_channel(self, value: MockTextChannel | None) -> None:
        self.__dict__["_system_channel"] = value

    def get_role(self, role_id: int) -> MockRole | None:
        return self._roles.get(role_id)

    def get_member(self, user_id: int) -> MockMember | None:
        return self._members.get(user_id)

    def get_channel(self, channel_id: int) -> MockChannel | None:
        return self._channels.get(channel_id)

    def add_member(self, member: MockMember) -> None:
        self._members[member.id] = member
        member.guild = self

    def remove_member(self, member: MockMember) -> None:
        self._members.pop(member.id, None)

    async def create_role(self, **kwargs: Any) -> MockRole:
        role = MockRole(**kwargs)
        self._roles[role.id] = role
        return role

    async def create_text_channel(self, name: str, **kwargs: Any) -> MockTextChannel:
        channel = MockTextChannel(name=name, guild=self, **kwargs)
        self._channels[channel.id] = channel
        if channel.category is not None:
            channel.category.add_channel(channel)
        return channel

    async def create_voice_channel(self, name: str, **kwargs: Any) -> MockVoiceChannel:
        channel = MockVoiceChannel(name=name, guild=self, **kwargs)
        self._channels[channel.id] = channel
        if channel.category is not None and isinstance(channel.category, MockCategoryChannel):
            channel.category.add_channel(channel)
        return channel

    async def create_category(self, name: str, **kwargs: Any) -> MockCategoryChannel:
        channel = MockCategoryChannel(name=name, guild=self, **kwargs)
        self._channels[channel.id] = channel
        return channel

    async def delete_channel(self, channel: MockChannel) -> None:
        self._channels.pop(channel.id, None)
        category = getattr(channel, "category", None)
        if isinstance(category, MockCategoryChannel):
            category.remove_channel(channel)

    def __repr__(self) -> str:
        return f"<MockGuild id={self.id} name={self.name!r}>"


class MockMessage(discord.Message):
    """In-memory :class:`discord.Message` recording both UI surfaces.

    Per ADR-0009 a message is either embed-based or Components V2:

    - embed-based: ``.embeds`` (list) + ``.components`` (flat children of the
      attached ``ui.View``), ``.layout`` is ``None``
    - Components V2: ``.layout`` is the ``ui.LayoutView`` root,
      ``.components`` is the flattened tree from ``walk_children()``
    """

    def __init__(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        channel: MockChannel | None = None,
        author: MockUser | MockMember | None = None,
        **kwargs: Any,
    ) -> None:
        self.id = _next_id()
        self.content = content or ""
        self.channel = channel if channel is not None else MockTextChannel()
        self.author = author if author is not None else MockUser(name="TestBot", bot=True)
        self.embeds = [embed] if embed is not None else []
        self.view = view
        self._edited = False
        for key, value in kwargs.items():
            setattr(self, key, value)
        self._sync_ui_surfaces(view)

    def _sync_ui_surfaces(self, view: discord.ui.View | None) -> None:
        self.layout = view if isinstance(view, discord.ui.LayoutView) else None
        if isinstance(view, discord.ui.LayoutView):
            self.components = list(view.walk_children())
        elif view is not None:
            self.components = list(view.children)
        else:
            self.components = []

    async def edit(self, **kwargs: Any) -> MockMessage:
        self._edited = True
        if "content" in kwargs:
            self.content = kwargs["content"] or ""
        if "embed" in kwargs:
            self.embeds = [kwargs["embed"]] if kwargs["embed"] is not None else []
        if "view" in kwargs:
            self.view = kwargs["view"]
            self._sync_ui_surfaces(kwargs["view"])
        return self

    async def delete(self, *, delay: float | None = None) -> None:
        self.deleted = True  # type: ignore[attr-defined]

    @property
    def edited(self) -> bool:
        return self._edited

    def __repr__(self) -> str:
        return f"<MockMessage id={self.id} content={self.content!r}>"


class MockResponse:
    """In-memory mirror of :class:`discord.InteractionResponse`.

    Records every acknowledgment: ``sent`` (a response was sent), ``deferred``
    and the first :class:`MockMessage` produced.
    """

    def __init__(self) -> None:
        self.sent = False
        self.deferred = False
        self.ephemeral = False
        self.message: MockMessage | None = None

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> MockMessage:
        self.sent = True
        self.ephemeral = ephemeral
        self.message = MockMessage(content=content, embed=embed, view=view)
        return self.message

    async def edit_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        **kwargs: Any,
    ) -> MockMessage:
        self.sent = True
        if self.message is None:
            self.message = MockMessage(content=content, embed=embed, view=view)
            return self.message
        return await self.message.edit(content=content, embed=embed, view=view)

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self.deferred = True
        self.ephemeral = ephemeral

    def is_done(self) -> bool:
        return self.sent or self.deferred


class MockFollowup:
    """In-memory mirror of :class:`discord.Webhook` (interaction followup).

    Every message sent through the followup is recorded in ``messages``.
    """

    def __init__(self) -> None:
        self.messages: list[MockMessage] = []

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> MockMessage:
        message = MockMessage(content=content, embed=embed, view=view)
        self.messages.append(message)
        return message

    async def edit(self, message_id: int, **kwargs: Any) -> MockMessage | None:
        for message in self.messages:
            if message.id == message_id:
                return await message.edit(**kwargs)
        return None

    async def delete_message(self, message_id: int) -> None:
        self.messages = [m for m in self.messages if m.id != message_id]


class MockClient:
    """In-memory mirror of :class:`discord.Client` lookups.

    Synchronous ``get_*`` mirrors the real client; ``fetch_*`` mirrors the
    async HTTP endpoints with the same in-memory data.
    """

    def __init__(
        self,
        *,
        user: MockUser | None = None,
        guilds: list[MockGuild] | None = None,
        users: list[MockUser] | None = None,
        channels: list[MockChannel] | None = None,
    ) -> None:
        self.user = user if user is not None else MockUser(name="TestBot", bot=True)
        self._guilds = {g.id: g for g in guilds or []}
        self._users = {u.id: u for u in users or []}
        self._channels = {c.id: c for c in channels or []}

    def get_user(self, user_id: int) -> MockUser | None:
        return self._users.get(user_id)

    async def fetch_user(self, user_id: int) -> MockUser:
        user = self._users.get(user_id)
        if user is None:
            user = MockUser(id=user_id)
            self._users[user_id] = user
        return user

    def get_channel(self, channel_id: int) -> MockChannel | None:
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> MockChannel:
        channel = self._channels.get(channel_id)
        if channel is None:
            channel = MockTextChannel(id=channel_id, name=f"channel-{channel_id}")
            self._channels[channel_id] = channel
        return channel

    def get_guild(self, guild_id: int) -> MockGuild | None:
        return self._guilds.get(guild_id)

    async def fetch_guild(self, guild_id: int) -> MockGuild:
        guild = self._guilds.get(guild_id)
        if guild is None:
            guild = MockGuild(id=guild_id)
            self._guilds[guild_id] = guild
        return guild


class MockInteraction(discord.Interaction):
    """In-memory :class:`discord.Interaction` for slash commands and UI components.

    ``response`` and ``followup`` are in-memory recorders; ``guild`` and
    ``client`` (read-only properties on the real class) are overridden with
    the provided mocks.
    """

    def __init__(
        self,
        *,
        user: MockUser | MockMember | None = None,
        guild: MockGuild | None = None,
        channel: MockChannel | None = None,
        custom_id: str | None = None,
        data: dict[str, Any] | None = None,
        locale: str = "en-US",
        guild_locale: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.user = user if user is not None else MockUser()
        self._guild = guild if guild is not None else MockGuild()
        self.channel = channel if channel is not None else MockTextChannel()
        self.data = data if data is not None else {}
        self.custom_id = custom_id or "test_interaction"
        self.locale = locale
        self.guild_locale = guild_locale if guild_locale is not None else locale
        self.command_failed = False
        self.extras: dict[str, Any] = {}
        self.response = MockResponse()
        self.followup = MockFollowup()
        self.guild_id = guild.id if guild is not None else None
        self._client = client if client is not None else MockClient()

    @property
    def guild(self) -> MockGuild:
        return self._guild

    @guild.setter
    def guild(self, value: MockGuild) -> None:
        self._guild = value

    @property
    def client(self) -> Any:
        return self._client

    def __repr__(self) -> str:
        return f"<MockInteraction custom_id={self.custom_id!r} user={self.user!r}>"


class MockView(discord.ui.View):
    """In-memory :class:`discord.ui.View` with configurable callbacks.

    The real ``discord.ui.View`` already runs fully in-memory; this subclass
    adds test conveniences: a configurable ``on_timeout`` and a ``clicked``
    record of button invocations.
    """

    def __init__(
        self,
        *,
        timeout: float | None = 180.0,
        on_timeout_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self._clicked: list[str] = []
        self._on_timeout_callback = on_timeout_callback

    def record_click(self, custom_id: str) -> None:
        self._clicked.append(custom_id)

    async def on_timeout(self) -> None:
        if self._on_timeout_callback is not None:
            self._on_timeout_callback()


def build_button_view(
    custom_id: str = "test:button:payload",
    *,
    label: str = "Click me",
    style: discord.ButtonStyle = discord.ButtonStyle.primary,
    callback: Callable[[MockInteraction], Any] | None = None,
) -> tuple[MockView, Button]:
    """Build a :class:`MockView` holding one wired-up button."""
    view = MockView()
    button = Button(label=label, style=style, custom_id=custom_id)
    view.add_item(button)
    if callback is not None:
        button.callback = callback
    return view, button


def build_select_view(
    custom_id: str = "test:select:payload",
    *,
    options: list[str] | None = None,
) -> tuple[MockView, Select]:
    """Build a :class:`MockView` holding one select menu."""
    view = MockView()
    select = Select(
        custom_id=custom_id,
        options=[discord.SelectOption(label=opt) for opt in options or ["Option 1", "Option 2"]],
    )
    view.add_item(select)
    return view, select


def build_layout_view(*texts: str) -> discord.ui.LayoutView:
    """Build a Components V2 ``LayoutView`` for dual-surface assertions."""
    layout = discord.ui.LayoutView()
    container = discord.ui.Container(*[discord.ui.TextDisplay(text) for text in texts or ("Layout text",)])
    layout.add_item(container)
    return layout


class MockModal(discord.ui.Modal):
    """In-memory :class:`discord.ui.Modal` with settable children."""

    def __init__(self, *, title: str = "Test Modal", timeout: float | None = None) -> None:
        super().__init__(title=title, timeout=timeout)

    @property
    def modal_children(self) -> list[Any]:
        return list(self.children)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("modal submitted")
