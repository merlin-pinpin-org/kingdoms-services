"""Unit tests for the MockDiscord library (kingdoms-services#2).

These tests pin the MockDiscord contract itself: every mock subclasses the
real discord.py class, is built from plain in-memory data, and records both
UI surfaces per ADR-0009 (embed-based and Components V2).
"""

from __future__ import annotations

import discord
import pytest

from tests.mocks.discord_mock import (
    MockCategoryChannel,
    MockClient,
    MockDMChannel,
    MockGuild,
    MockInteraction,
    MockMember,
    MockMessage,
    MockModal,
    MockRole,
    MockTextChannel,
    MockUser,
    MockView,
    MockVoiceChannel,
    build_button_view,
    build_layout_view,
    build_select_view,
)


class TestMockUser:
    def test_subclasses_real_user(self) -> None:
        user = MockUser()
        assert isinstance(user, discord.User)

    def test_defaults(self) -> None:
        user = MockUser()
        assert user.name == "TestUser"
        assert user.bot is False
        assert user.avatar is None
        assert user.display_name == "TestUser"

    def test_explicit_fields_and_mention(self) -> None:
        user = MockUser(id=42, name="Alice")
        assert user.id == 42
        assert user.mention == "<@42>"
        assert user.display_name == "Alice"

    def test_unique_ids(self) -> None:
        assert MockUser().id != MockUser().id


class TestMockMember:
    def test_subclasses_real_member(self) -> None:
        member = MockMember()
        assert isinstance(member, discord.Member)

    def test_display_name_prefers_nick(self) -> None:
        member = MockMember(id=7, name="Bob")
        assert member.display_name == "Bob"
        member.nick = "Bobby"
        assert member.display_name == "Bobby"

    def test_mention(self) -> None:
        member = MockMember(id=7)
        assert member.mention == "<@7>"

    async def test_role_management(self) -> None:
        member = MockMember()
        role = MockRole(name="Player")
        await member.add_roles(role)
        assert member.roles == [role]
        await member.add_roles(role)
        assert member.roles == [role]
        await member.remove_roles(role)
        assert role not in member.roles


class TestMockRole:
    def test_subclasses_real_role(self) -> None:
        role = MockRole()
        assert isinstance(role, discord.Role)

    def test_color_coercion(self) -> None:
        assert MockRole().color == discord.Color.default()
        assert MockRole(color="#ff0000").color == discord.Color.from_str("#ff0000")
        assert MockRole(color=discord.Color.blue()).color == discord.Color.blue()

    def test_permissions_coercion(self) -> None:
        role = MockRole(permissions=["manage_channels", "manage_roles"])
        assert role.permissions.manage_channels
        assert role.permissions.manage_roles
        assert not role.permissions.administrator

    def test_mention(self) -> None:
        role = MockRole(id=99)
        assert role.mention == "<@&99>"


class TestMockTextChannel:
    def test_subclasses_real_channel(self) -> None:
        channel = MockTextChannel()
        assert isinstance(channel, discord.TextChannel)
        assert channel.type is discord.ChannelType.text

    async def test_send_records_history(self) -> None:
        channel = MockTextChannel(name="ladder")
        message = await channel.send("hello", embed=discord.Embed(title="t"))
        assert message in channel.messages
        assert message.content == "hello"
        assert message.channel is channel

    async def test_edit_and_permissions(self) -> None:
        channel = MockTextChannel(name="c")
        await channel.edit(name="renamed")
        assert channel.name == "renamed"
        member = MockMember(id=5)
        overwrite = discord.PermissionOverwrite(send_messages=False)
        await channel.set_permissions(member, overwrite)
        assert channel.permission_overwrite_for(member) is overwrite


class TestMockVoiceAndCategoryChannels:
    def test_voice_channel(self) -> None:
        channel = MockVoiceChannel(name="Lounge")
        assert isinstance(channel, discord.VoiceChannel)
        assert channel.type is discord.ChannelType.voice
        assert channel.members == []

    def test_category_groups_channels(self) -> None:
        category = MockCategoryChannel(name="Games")
        channel = MockTextChannel(name="ladder", category=category)
        category.add_channel(channel)
        assert channel in category.channels
        assert channel.category is category
        category.remove_channel(channel)
        assert channel not in category.channels


class TestMockDMChannel:
    def test_subclasses_real_dm(self) -> None:
        dm = MockDMChannel()
        assert isinstance(dm, discord.DMChannel)
        assert dm.type is discord.ChannelType.private
        assert dm.recipient is not None

    async def test_send_records_history(self) -> None:
        dm = MockDMChannel(recipient=MockUser(name="Player"))
        message = await dm.send("welcome")
        assert message in dm.messages
        assert message.content == "welcome"


class TestMockGuild:
    def test_subclasses_real_guild(self) -> None:
        guild = MockGuild()
        assert isinstance(guild, discord.Guild)
        assert guild.icon is None
        assert isinstance(guild.me, MockMember)

    async def test_role_lifecycle(self) -> None:
        guild = MockGuild()
        role = await guild.create_role(name="Player", permissions=["manage_channels"])
        assert role in guild.roles
        assert guild.get_role(role.id) is role
        assert guild.get_role(0) is None

    async def test_channel_lifecycle(self) -> None:
        guild = MockGuild()
        category = await guild.create_category(name="Games")
        channel = await guild.create_text_channel("ladder", category=category)
        assert channel in guild.text_channels
        assert channel in category.channels
        assert guild.get_channel(channel.id) is channel
        await guild.delete_channel(channel)
        assert guild.get_channel(channel.id) is None
        assert channel not in category.channels

    def test_member_registry(self) -> None:
        guild = MockGuild()
        member = MockMember(id=200, name="p1")
        guild.add_member(member)
        assert guild.get_member(200) is member
        assert member in guild.members
        guild.remove_member(member)
        assert guild.get_member(200) is None


class TestMockMessageDualSurface:
    def test_embed_based_surface(self) -> None:
        embed = discord.Embed(title="result")
        message = MockMessage(content="ok", embed=embed)
        assert message.embeds == [embed]
        assert message.layout is None
        assert message.components == []

    def test_view_components_surface(self) -> None:
        view, _button = build_button_view()
        message = MockMessage(view=view)
        assert message.layout is None
        assert [type(c) for c in message.components] == [discord.ui.Button]

    def test_components_v2_surface(self) -> None:
        layout = build_layout_view("Text A", "Text B")
        message = MockMessage(view=layout)
        assert message.layout is layout
        assert message.embeds == []
        children = list(layout.walk_children())
        assert message.components == children
        assert len([c for c in children if isinstance(c, discord.ui.TextDisplay)]) == 2

    async def test_edit_updates_surfaces(self) -> None:
        message = MockMessage(content="before")
        view, _button = build_button_view()
        await message.edit(content="after", view=view)
        assert message.edited
        assert message.content == "after"
        assert len(message.components) == 1

    async def test_edit_switches_to_layout(self) -> None:
        message = MockMessage(content="before")
        layout = build_layout_view("v2")
        await message.edit(view=layout)
        assert message.layout is layout
        assert isinstance(message.components[0], discord.ui.Container)


class TestMockInteraction:
    async def test_subclasses_real_interaction(self) -> None:
        interaction = MockInteraction()
        assert isinstance(interaction, discord.Interaction)
        assert isinstance(interaction.user, MockUser)
        assert isinstance(interaction.guild, MockGuild)

    async def test_send_message_response(self) -> None:
        interaction = MockInteraction()
        message = await interaction.response.send_message("pong", ephemeral=True)
        assert interaction.response.sent
        assert interaction.response.ephemeral
        assert message.content == "pong"

    async def test_edit_message_response(self) -> None:
        interaction = MockInteraction()
        await interaction.response.send_message("first")
        edited = await interaction.response.edit_message(content="second")
        assert edited.content == "second"
        assert edited.edited

    async def test_defer(self) -> None:
        interaction = MockInteraction()
        await interaction.response.defer(ephemeral=True)
        assert interaction.response.deferred
        assert interaction.response.is_done()

    async def test_followup_records_messages(self) -> None:
        interaction = MockInteraction()
        message = await interaction.followup.send("later", ephemeral=True)
        assert message in interaction.followup.messages
        await interaction.followup.edit(message.id, content="later2")
        assert message.content == "later2"
        await interaction.followup.delete_message(message.id)
        assert message not in interaction.followup.messages

    def test_locales(self) -> None:
        interaction = MockInteraction(locale="fr", guild_locale="en-US")
        assert interaction.locale == "fr"
        assert interaction.guild_locale == "en-US"
        assert MockInteraction().locale == "en-US"


class TestMockClient:
    async def test_sync_and_async_lookups(self) -> None:
        guild = MockGuild(id=100)
        user = MockUser(id=1)
        channel = MockTextChannel(id=5)
        client = MockClient(guilds=[guild], users=[user], channels=[channel])
        assert client.get_guild(100) is guild
        assert client.get_user(1) is user
        assert client.get_channel(5) is channel
        assert client.get_guild(999) is None

    async def test_fetch_creates_missing(self) -> None:
        client = MockClient()
        fetched = await client.fetch_user(999)
        assert fetched.id == 999
        assert client.get_user(999) is fetched


class TestViewHelpers:
    def test_mock_view_recording(self) -> None:
        view = MockView()
        view.record_click("a:button:1")
        view.record_click("a:button:2")
        assert view._clicked == ["a:button:1", "a:button:2"]

    async def test_mock_view_timeout_callback(self) -> None:
        calls: list[str] = []
        view = MockView(on_timeout_callback=lambda: calls.append("fired"))
        await view.on_timeout()
        assert calls == ["fired"]

    def test_button_view_convention(self) -> None:
        view, button = build_button_view(custom_id="reg:button:confirm")
        assert button.custom_id == "reg:button:confirm"
        assert view.children == [button]

    def test_select_view(self) -> None:
        _view, select = build_select_view(custom_id="reg:select:region", options=["EU", "NA"])
        assert select.custom_id == "reg:select:region"
        assert [o.label for o in select.options] == ["EU", "NA"]

    def test_layout_view_content(self) -> None:
        layout = build_layout_view("one", "two")
        assert layout.content_length() > 0
        assert len(list(layout.walk_children())) == 3


class TestMockModal:
    async def test_modal_submits_and_responds(self) -> None:
        modal = MockModal()
        assert isinstance(modal, discord.ui.Modal)
        interaction = MockInteraction()
        await modal.on_submit(interaction)
        assert interaction.response.sent
        assert interaction.response.message is not None
        assert interaction.response.message.content == "modal submitted"


class TestDeterminism:
    def test_ids_are_unique_and_increasing(self) -> None:
        first = MockUser()
        second = MockUser()
        assert second.id > first.id

    def test_no_gateway_state_required(self) -> None:
        interaction = MockInteraction()
        assert interaction.client.user is not None
        assert interaction.response.is_done() is False


@pytest.mark.parametrize(
    ("mock_class", "real_class"),
    [
        (MockUser, discord.User),
        (MockMember, discord.Member),
        (MockRole, discord.Role),
        (MockGuild, discord.Guild),
        (MockTextChannel, discord.TextChannel),
        (MockVoiceChannel, discord.VoiceChannel),
        (MockCategoryChannel, discord.CategoryChannel),
        (MockDMChannel, discord.DMChannel),
        (MockMessage, discord.Message),
        (MockInteraction, discord.Interaction),
        (MockModal, discord.ui.Modal),
    ],
)
def test_mocks_subclass_real_discord_types(mock_class: type, real_class: type) -> None:
    instance = mock_class()
    assert isinstance(instance, real_class)
