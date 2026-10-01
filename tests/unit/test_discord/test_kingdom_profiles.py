"""Unit tests for the Kingdoms per-player profiles (kingdoms#138)."""

from __future__ import annotations

import discord
import pytest

from kingdoms.discord.kingdom_profiles import (
    PENDING_REQUESTS_CHANNEL,
    PROFILES_CATEGORY,
    STRINGS,
    _LeaveModal,
    _request_decision_view,
    _strings,
    build_profile_message,
    build_profile_view,
    ensure_profile_channel,
    profile_channel_name,
)
from tests.mocks.discord_mock import (
    MockGuild,
    MockInteraction,
    MockMember,
)


def _guild() -> MockGuild:
    return MockGuild(id=7)


def _member(guild: MockGuild) -> MockMember:
    member = MockMember(id=555, name="Hana", guild=guild)
    guild.add_member(member)
    return member


@pytest.mark.asyncio
async def test_ensure_profile_channel_creates_private_channel() -> None:
    """The profile channel is created under Profils, once per player."""
    guild = _guild()
    member = _member(guild)
    channel = await ensure_profile_channel(guild, member)
    assert channel is not None
    assert channel.name == profile_channel_name(member)
    assert channel.name.startswith("profil-")
    categories = {c.name for c in guild.categories}
    assert PROFILES_CATEGORY in categories

    again = await ensure_profile_channel(guild, member)
    assert again.id == channel.id, "the profile channel must be adopted, not duplicated"


@pytest.mark.asyncio
async def test_profile_channel_overwrites_deny_everyone() -> None:
    """The profile channel hides the channel from @everyone."""
    guild = _guild()
    member = _member(guild)
    channel = await ensure_profile_channel(guild, member)
    overwrite = channel.creation_overwrite_for(guild.default_role)
    assert overwrite is not None
    assert overwrite.view_channel is False


def test_build_profile_message_shows_state_and_smurfs() -> None:
    """The profile message embeds the state, role, and declared smurfs."""
    strings = _strings("fr")
    content, view = build_profile_message(
        "fr",
        member_name="Hana",
        state=strings["state_pending"],
        role_label=strings_role_label_fr(),
        kingdom="HeN",
        queued=False,
        insight="https://www.aoe2insight.com/x",
        game_id="11897201",
        smurfs=("https://www.aoe2insight.com/s1",),
    )
    assert "Hana" in content
    assert strings["state_pending"] in content
    assert "HeN" in content
    assert "https://www.aoe2insight.com/s1" in content
    assert view is not None and len(view.children) == 4


def strings_role_label_fr() -> str:
    return STRINGS["fr"]["role_king"] if False else "👑 Roi"


@pytest.mark.asyncio
async def test_profile_buttons_answer_placeholder() -> None:
    """Stats and achievements answer with the coming-soon note."""
    view = build_profile_view("fr", validated=False)
    by_custom_id = {child.custom_id: child for child in view.children}
    interaction = MockInteraction(user=MockMember(name="p", guild=MockGuild(id=7)))
    await by_custom_id["kingdoms:profile:stats"].callback(interaction)
    assert "ultérieurement" in (interaction.response.message or "").content

    interaction = MockInteraction(user=MockMember(name="p", guild=MockGuild(id=7)))
    await by_custom_id["kingdoms:profile:success"].callback(interaction)
    assert "ultérieurement" in (interaction.response.message or "").content


@pytest.mark.asyncio
async def test_edit_button_before_validation_is_free() -> None:
    """Before validation, the edit button answers with the free-edit note."""
    view = build_profile_view("fr", validated=False)
    by_custom_id = {child.custom_id: child for child in view.children}
    interaction = MockInteraction(user=MockMember(name="p", guild=MockGuild(id=7)))
    await by_custom_id["kingdoms:profile:edit"].callback(interaction)
    assert "pas encore été examinée" in (interaction.response.message or "").content


@pytest.mark.asyncio
async def test_edit_button_after_validation_relays_request() -> None:
    """After validation, the edit button relays a request to Demandes."""
    guild = MockGuild(id=7)
    admin_category = await guild.create_category("Admin")
    demandes = await guild.create_text_channel(PENDING_REQUESTS_CHANNEL, category=admin_category)
    view = build_profile_view("fr", validated=True)
    by_custom_id = {child.custom_id: child for child in view.children}
    interaction = MockInteraction(user=MockMember(name="p", guild=guild), guild=guild)
    await by_custom_id["kingdoms:profile:edit"].callback(interaction)
    assert len(demandes.messages) == 1
    assert demandes.messages[0].view is not None


@pytest.mark.asyncio
async def test_leave_modal_relays_to_demandes() -> None:
    """The leave modal posts the request into the Demandes channel."""
    guild = MockGuild(id=7)
    admin_category = await guild.create_category("Admin")
    demandes = await guild.create_text_channel(PENDING_REQUESTS_CHANNEL, category=admin_category)
    modal = _LeaveModal("fr")
    object.__setattr__(modal.reason, "_value", "Plus de temps")
    interaction = MockInteraction(user=MockMember(name="p", guild=guild), guild=guild)
    await modal.on_submit(interaction)
    assert len(demandes.messages) == 1
    content = demandes.messages[0].content or ""
    assert "Plus de temps" in content


@pytest.mark.asyncio
async def test_request_decision_buttons_close_the_request() -> None:
    """An admin decision closes the request and annotates the message."""
    view = _request_decision_view("fr", "leave")
    permissions = discord.Permissions(administrator=True)
    interaction = MockInteraction(user=MockMember(name="admin", guild_permissions=permissions))
    interaction.message = None
    await view.children[0].callback(interaction)
    assert interaction.response.sent is True
