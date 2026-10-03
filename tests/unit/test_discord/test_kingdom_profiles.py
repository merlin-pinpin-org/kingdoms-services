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
from tests.mocks.provision import provisioned_wiring
from tests.mocks.discord_mock import (
    MockGuild,
    MockInteraction,
    MockMember,
    MockMessage,
)
from tests.mocks.provision import provisioned_wiring


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
    categories = {c.name.lower() for c in guild.categories}
    assert PROFILES_CATEGORY.lower() in categories

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
    by_custom_id = {child.item.custom_id: child for child in view.children}
    interaction = MockInteraction(user=MockMember(name="p", guild=MockGuild(id=7)))
    interaction.locale = "fr"
    await by_custom_id["kingdoms:profile:stats"].callback(interaction)
    assert "ultérieurement" in (interaction.response.message or "").content

    interaction = MockInteraction(user=MockMember(name="p", guild=MockGuild(id=7)))
    interaction.locale = "fr"
    await by_custom_id["kingdoms:profile:success"].callback(interaction)
    assert "ultérieurement" in (interaction.response.message or "").content


@pytest.mark.asyncio
async def test_edit_button_before_validation_is_free() -> None:
    """Before validation, the edit button answers with the free-edit note."""
    view = build_profile_view("fr", validated=False)
    by_custom_id = {child.item.custom_id: child for child in view.children}
    interaction = MockInteraction(user=MockMember(name="p", guild=MockGuild(id=7)))
    interaction.locale = "fr"
    await by_custom_id["kingdoms:profile:edit"].callback(interaction)
    assert "pas encore été examinée" in (interaction.response.message or "").content


@pytest.mark.asyncio
async def test_edit_button_after_validation_relays_request() -> None:
    """After validation, the edit button relays a request to Demandes."""
    guild = MockGuild(id=7)
    admin_category = await guild.create_category("Admin")
    demandes = await guild.create_text_channel(PENDING_REQUESTS_CHANNEL, category=admin_category)
    from kingdoms.discord.kingdom_profiles import _strings as profile_strings

    view = build_profile_view("fr", validated=True)
    by_custom_id = {child.item.custom_id: child for child in view.children}
    interaction = MockInteraction(user=MockMember(name="p", guild=guild), guild=guild)
    interaction.locale = "fr"
    interaction.message = MockMessage(content=profile_strings("fr")["state_validated"])
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


@pytest.mark.asyncio
async def test_admin_remove_button_opens_modal() -> None:
    """The remove button (admin) opens the removal modal."""
    from kingdoms.discord.kingdom_persistent import (
        KingdomAdminButton,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    register_kingdoms_panel_wiring(KingdomsPanelWiring(bot_admins=("1",)))
    permissions = discord.Permissions(administrator=True)
    interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=_guild()
    )
    button = KingdomAdminButton("remove", "🗑️ Retirer un joueur", discord.ButtonStyle.danger)
    await button.callback(interaction)
    assert interaction.response.modal is not None


@pytest.mark.asyncio
async def test_admin_reset_confirms_then_deletes_salons() -> None:
    """The reset flow asks for confirmation, then deletes the salons."""
    from kingdoms.discord.kingdom_persistent import KingdomAdminButton
    from kingdoms.discord.kingdom_setup import provision_structure

    guild = MockGuild(id=9)
    provisioned_wiring(guild, bot_admins=("1",))
    await provision_structure(guild)
    assert guild.text_channels, "structure provisioned"

    permissions = discord.Permissions(administrator=True)
    interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    interaction.guild = guild
    interaction.locale = "fr"
    reset = KingdomAdminButton("reset", "♻️ Réinitialiser", discord.ButtonStyle.danger)
    await reset.callback(interaction)
    assert interaction.response.message is not None
    assert interaction.response.message.view is not None

    confirm = next(
        c for c in interaction.response.message.view.children if c.item.custom_id.endswith("reset-confirm")
    )
    confirm_interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    confirm_interaction.guild = guild
    confirm_interaction.locale = "fr"
    await confirm.callback(confirm_interaction)
    remaining = {c.name.lower() for c in guild.text_channels}
    assert not ({"postuler", "candidatures", "paramètres", "demandes"} & remaining), "salons deleted"


@pytest.mark.asyncio
async def test_admin_deploy_confirms_then_reprovisions() -> None:
    """The deploy flow asks for confirmation, then re-provisions the salons."""
    from kingdoms.discord.kingdom_persistent import KingdomAdminButton

    guild = MockGuild(id=11)
    provisioned_wiring(guild, bot_admins=("1",))
    permissions = discord.Permissions(administrator=True)
    interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    interaction.guild = guild
    interaction.locale = "fr"
    deploy = KingdomAdminButton("deploy", "🚀 Déployer", discord.ButtonStyle.primary)
    await deploy.callback(interaction)
    assert interaction.response.message is not None
    assert interaction.response.message.view is not None
    confirm = next(
        c for c in interaction.response.message.view.children if c.item.custom_id.endswith("deploy-confirm")
    )
    confirm_interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    confirm_interaction.guild = guild
    confirm_interaction.locale = "fr"
    await confirm.callback(confirm_interaction)
    assert guild.text_channels, "salons provisioned by the deploy action"


@pytest.mark.asyncio
async def test_admin_status_answers_kingdoms_and_queue() -> None:
    """The status button answers with kingdoms, players and waiting counts."""
    from kingdoms.discord.kingdom_persistent import (
        KingdomAdminButton,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    class FakeKingdom:
        name = "Aquitaine"
        is_gaia = False

    class FakeLord:
        in_queue = True
        left = False

    class FakeService:
        async def kingdoms(self):
            return [FakeKingdom()]

        async def lords(self):
            return [FakeLord()]

    register_kingdoms_panel_wiring(KingdomsPanelWiring(bot_admins=("1",), kingdoms_service=FakeService()))
    guild = _guild()
    permissions = discord.Permissions(administrator=True)
    interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    interaction.guild = guild
    interaction.locale = "fr"
    status = KingdomAdminButton("status", "📊 Statut", discord.ButtonStyle.secondary)
    await status.callback(interaction)
    assert interaction.response.message is not None
    assert "Aquitaine" in interaction.response.message.content


@pytest.mark.asyncio
async def test_admin_assign_and_add_kingdom_open_modals() -> None:
    """Assign and add-kingdom open their respective admin modals."""
    from kingdoms.discord.kingdom_persistent import (
        KingdomAdminButton,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    register_kingdoms_panel_wiring(KingdomsPanelWiring(bot_admins=("1",)))
    permissions = discord.Permissions(administrator=True)
    for action in ("assign", "add-kingdom"):
        interaction = MockInteraction(
            user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=_guild()
        )
        button = KingdomAdminButton(action, action, discord.ButtonStyle.primary)
        await button.callback(interaction)
        assert interaction.response.modal is not None


@pytest.mark.asyncio
async def test_candidature_approval_enrolls_the_player() -> None:
    """Approving a candidature enrolls the player into the season."""
    from kingdoms.discord.kingdom_persistent import (
        KingdomCandidatureButton,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    class FakeLord:
        in_queue = False
        kingdom_id = "k-1"

    class FakeService:
        def __init__(self):
            self.calls = []

        async def enroll(self, player_id, display_name, role, kingdom_name=None, proposed_name=None):
            self.calls.append((player_id, role, kingdom_name, proposed_name))
            return FakeLord()

        async def kingdoms(self):
            return []

        async def lords(self):
            return []

    service = FakeService()
    register_kingdoms_panel_wiring(KingdomsPanelWiring(bot_admins=("1",), kingdoms_service=service))
    guild = MockGuild(id=13)
    permissions = discord.Permissions(administrator=True)
    interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    interaction.guild = guild
    interaction.locale = "fr"
    message = MockMessage(content="📋 Hana\n**Rôle demandé** : 🪙 Roi\n**Royaume** : HeN\n<@555>", channel=None)
    interaction.message = message
    approve = KingdomCandidatureButton("approve", "✅", discord.ButtonStyle.success)
    await approve.callback(interaction)
    assert service.calls and service.calls[0][0] == "555"
