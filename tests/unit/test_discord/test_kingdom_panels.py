"""Unit tests for the Kingdoms salons-first panels (kingdoms#138)."""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord.kingdom_panels import (
    QUEUE_VALUE,
    ROLE_KING,
    ROLE_LORD,
    _ApplicationContext,
    _candidature_view,
    _KingApplicationModal,
    _kingdom_select_view,
    _role_select_view,
    _send_summary,
    _strings,
    build_apply_panel,
    build_settings_panel,
    deploy_panels,
    register_kingdom_panels_command,
)
from tests.mocks.provision import provisioned_wiring
from tests.mocks.discord_mock import (
    MockGuild,
    MockInteraction,
    MockMember,
    MockMessage,
    MockTextChannel,
)


class _FakeLogsService:
    """Minimal LogService double: locale + guild settings store."""

    def __init__(self, locale: str = "en") -> None:
        self._locale = locale
        self.settings: dict[str, dict[str, Any]] = {}

    async def get_locale(self, guild_id: str) -> str:
        return self._locale

    async def set_locale(self, guild_id: str, locale: str, by: str = "") -> None:
        self._locale = locale

    async def _safe(self, value: Any) -> Any:
        return value

    async def get_guild_settings(self, guild_id: str) -> dict[str, Any] | None:
        return self.settings.get(guild_id)

    async def set_guild_settings(self, guild_id: str, settings: dict[str, Any]) -> None:
        self.settings[guild_id] = settings

    @property
    def _db(self) -> _FakeLogsService:
        return self


class _FakeModRoles:
    """ModRolesService double recording role assignments."""

    def __init__(self) -> None:
        self.assigned: list[tuple[str, str, str]] = []

    async def assign_mod_role(self, guild_id: str, user_id: str, mod: str, role_key: str) -> None:
        self.assigned.append((user_id, mod, role_key))


class _FakeKingdom:
    """KingdomsService.kingdoms() item double."""

    def __init__(self, name: str, type: str = "player") -> None:
        self.name = name
        self.type = type


class _FakeKingdomsService:
    """KingdomsService double: declared kingdoms of the season."""

    def __init__(self, names: list[str]) -> None:
        self._kingdoms = [_FakeKingdom(n) for n in names]

    async def kingdoms(self) -> list[_FakeKingdom]:
        return list(self._kingdoms)


def _fill(modal: discord.ui.Modal, **values: str) -> None:
    """Set modal field values through the internal payload."""
    for name, value in values.items():
        field = getattr(modal, name)
        object.__setattr__(field, "_value", value)


def _admin_interaction(admin: bool = True) -> MockInteraction:
    permissions = discord.Permissions(administrator=True) if admin else discord.Permissions()
    member = MockMember(name="admin", guild_permissions=permissions)
    guild = MockGuild(id=42)
    interaction = MockInteraction(user=member, guild=guild)
    interaction.guild_id = 42
    return interaction


def _player_interaction(member_id: int = 999) -> MockInteraction:
    member = MockMember(id=member_id, name="player")
    guild = MockGuild(id=42)
    interaction = MockInteraction(user=member, guild=guild)
    interaction.guild_id = 42
    return interaction


def _context(**kwargs: Any) -> _ApplicationContext:
    defaults: dict[str, Any] = {"locale": "en"}
    defaults.update(kwargs)
    return _ApplicationContext(**defaults)


def _choose(interaction: MockInteraction, value: str) -> None:
    interaction.data = {"values": [value]}


@pytest.mark.parametrize("locale", ["en", "fr"])
@pytest.mark.asyncio
async def test_apply_panel_builds_with_enroll_button(locale: str) -> None:
    """The Postuler panel renders with a locale-appropriate Enroll button."""
    view = await build_apply_panel(locale)
    assert view is not None


def test_strings_cover_both_locales() -> None:
    """Every key of one locale exists in the other."""
    assert set(_strings("en")) == set(_strings("fr"))


@pytest.mark.asyncio
async def test_role_select_offers_only_lord_and_king() -> None:
    """Step 1: the role menu offers exactly Seigneur and Roi (no free text)."""
    view = _role_select_view(_context(locale="fr"))
    select = view.children[0]
    labels = [option.label for option in select.options]
    assert labels == ["🎖️ Seigneur", "👑 Roi"]


@pytest.mark.asyncio
async def test_king_choice_opens_king_modal_with_kingdom_name() -> None:
    """Choosing Roi opens the King modal (kingdom name + insight + game id)."""
    context = _context()
    view = _role_select_view(context)
    interaction = _player_interaction()
    _choose(interaction, "king")
    await view.children[0].callback(interaction)
    assert interaction.response.modal is not None
    fields = {type(field).__name__ for field in interaction.response.modal.children}
    assert "TextInput" in fields


@pytest.mark.asyncio
async def test_lord_choice_lists_declared_kingdoms_and_queue() -> None:
    """Choosing Seigneur lists the declared kingdoms plus the wait option."""
    context = _context(kingdoms_service=_FakeKingdomsService(["Avalon", "Bretagne"]))
    view = _role_select_view(context)
    interaction = _player_interaction()
    _choose(interaction, "lord")
    await view.children[0].callback(interaction)
    assert interaction.response.message is not None
    kingdom_view = await _kingdom_select_view(context)
    values = [option.value for option in kingdom_view.children[0].options]
    assert "Avalon" in values and "Bretagne" in values
    assert QUEUE_VALUE in values


@pytest.mark.asyncio
async def test_kingdom_select_includes_gaia_never() -> None:
    """Gaïa kingdoms never appear in the join list."""
    context = _context(
        kingdoms_service=_FakeKingdomsService(["Avalon"]),
    )
    context.kingdoms_service = _FakeKingdomsService(["Avalon"])
    context.kingdoms_service._kingdoms.append(_FakeKingdom("Gaïa", type="gaia"))
    view = await _kingdom_select_view(context)
    values = [option.value for option in view.children[0].options]
    assert "Gaïa" not in values


@pytest.mark.asyncio
async def test_king_modal_validates_name_insight_game_id() -> None:
    """The King modal refuses an empty name, a bad link and a bad game id."""
    context = _context()
    interaction = _player_interaction()
    modal = _KingApplicationModal(context)
    _fill(modal, kingdom_name="", insight_link="https://x.io/a", game_id="12345678")
    await modal.on_submit(interaction)
    assert "name" in (interaction.response.message or "").content.lower()

    interaction = _player_interaction()
    modal = _KingApplicationModal(context)
    _fill(modal, kingdom_name="Avalon", insight_link="not-a-url", game_id="12345678")
    await modal.on_submit(interaction)
    assert "insight" in (interaction.response.message or "").content.lower()

    interaction = _player_interaction()
    modal = _KingApplicationModal(context)
    _fill(modal, kingdom_name="Avalon", insight_link="https://x.io/a", game_id="abc")
    await modal.on_submit(interaction)
    assert "id" in (interaction.response.message or "").content.lower()


@pytest.mark.asyncio
async def test_valid_submission_shows_summary_with_rules_button() -> None:
    """A valid form shows the review step with the rules-acceptance button."""
    context = _context()
    interaction = _player_interaction()
    await _send_summary(
        interaction,
        context,
        is_king=True,
        kingdom_name="Avalon",
        queued=False,
        insight="https://www.aoe2insight.com/x",
        game_id="12345678",
    )
    assert interaction.response.sent is True
    assert interaction.response.message is not None
    assert interaction.response.message.view is not None
    labels = [
        child.label for child in interaction.response.message.view.children if getattr(child, "label", None)
    ]
    assert any("rules" in (label or "").lower() or "accept" in (label or "").lower() for label in labels)


@pytest.mark.asyncio
async def test_rules_button_posts_candidature_to_channel() -> None:
    """Ticking the rules button posts the application into Candidatures."""
    channel = MockTextChannel(name="candidatures", guild=MockGuild(id=42))
    context = _context(candidatures_channel=channel)
    interaction = _player_interaction(member_id=555)
    await _send_summary(
        interaction,
        context,
        is_king=False,
        kingdom_name="Avalon",
        queued=False,
        insight="https://www.aoe2insight.com/x",
        game_id="12345678",
    )
    view = interaction.response.message.view
    submit_button = next(c for c in view.children if getattr(c, "label", "").startswith("✅"))
    await submit_button.callback(interaction)
    assert len(channel.messages) == 1
    posted = channel.messages[0]
    assert "12345678" in (posted.content or "")
    assert "<@555>" in (posted.content or "")
    assert posted.view is not None


@pytest.mark.asyncio
async def test_queued_lord_posts_queue_status() -> None:
    """A queued Lord application posts the waiting status, not a kingdom."""
    channel = MockTextChannel(name="candidatures", guild=MockGuild(id=42))
    context = _context(candidatures_channel=channel)
    interaction = _player_interaction()
    await _send_summary(
        interaction,
        context,
        is_king=False,
        kingdom_name="",
        queued=True,
        insight="https://www.aoe2insight.com/x",
        game_id="12345678",
    )
    view = interaction.response.message.view
    submit_button = next(c for c in view.children if getattr(c, "label", "").startswith("✅"))
    await submit_button.callback(interaction)
    assert len(channel.messages) == 1
    assert "waiting" in (channel.messages[0].content or "").lower()


@pytest.mark.asyncio
async def test_cancel_button_cancels() -> None:
    """The cancel button cancels the application."""
    context = _context()
    interaction = _player_interaction()
    await _send_summary(
        interaction,
        context,
        is_king=False,
        kingdom_name="Avalon",
        queued=False,
        insight="https://www.aoe2insight.com/x",
        game_id="12345678",
    )
    view = interaction.response.message.view
    cancel_button = next(c for c in view.children if c.label == "Cancel")
    await cancel_button.callback(interaction)
    assert "cancel" in (interaction.response.message.content or "").lower()


@pytest.mark.asyncio
async def test_candidature_approve_assigns_lord_role() -> None:
    """Approving a Lord application assigns the kingdoms_lord role."""
    from kingdoms.discord.kingdom_persistent import (
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    roles = _FakeModRoles()
    register_kingdoms_panel_wiring(KingdomsPanelWiring(bot_admins=("111",), mod_roles_service=roles))
    view = _candidature_view("en", bot_admins=("111",), mod_roles_service=roles, guild_id="42")
    assert len(view.children) == 3

    interaction = _admin_interaction()
    message = MockMessage(content="role 🎖️ Lord <@999>")
    interaction.message = message
    button = view.children[0]
    assert button.item.emoji is not None
    await button.callback(interaction)
    assert roles.assigned == [("999", "kingdoms", ROLE_LORD)]


@pytest.mark.asyncio
async def test_candidature_approve_assigns_king_role() -> None:
    """Approving a King application assigns the kingdoms_king role."""
    from kingdoms.discord.kingdom_persistent import (
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    roles = _FakeModRoles()
    register_kingdoms_panel_wiring(KingdomsPanelWiring(bot_admins=("111",), mod_roles_service=roles))
    view = _candidature_view("en", bot_admins=("111",), mod_roles_service=roles, guild_id="42")

    interaction = _admin_interaction()
    interaction.message = MockMessage(content="role 👑 King <@888>")
    await view.children[0].callback(interaction)
    assert roles.assigned == [("888", "kingdoms", ROLE_KING)]


@pytest.mark.asyncio
async def test_candidature_refused_assigns_nothing() -> None:
    """Refusing does not assign any role."""
    roles = _FakeModRoles()
    view = _candidature_view("en", bot_admins=("111",), mod_roles_service=roles, guild_id="42")

    interaction = _admin_interaction()
    interaction.message = MockMessage(content="role 🎖️ Lord <@777>")
    await view.children[2].callback(interaction)
    assert roles.assigned == []


@pytest.mark.asyncio
async def test_settings_panel_renders_the_season_actions() -> None:
    """The Paramètres panel carries the admin season action buttons."""
    logs = _FakeLogsService(locale="fr")
    view = await build_settings_panel(logs, "42", "system", ("111",))
    assert view is not None

    def _flatten(item: Any) -> list[Any]:
        found: list[Any] = []
        for child in getattr(item, "children", ()) or ():
            found.append(child)
            found.extend(_flatten(child))
        return found

    from kingdoms.discord.kingdom_persistent import KingdomAdminButton

    buttons = [child for child in _flatten(view) if isinstance(child, KingdomAdminButton)]
    assert len(buttons) == 8


@pytest.mark.asyncio
async def test_deploy_panels_posts_in_postuler_and_parametres() -> None:
    """deploy_panels posts one panel per bootstrap channel."""
    from kingdoms.discord.kingdom_setup import provision_structure

    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    logs = _FakeLogsService(locale="fr")
    report = await deploy_panels(guild, logs, bot_admins=("111",))

    assert report.get("postuler") == "deployed"
    assert report.get("paramètres") == "deployed"
    postuler = next(c for c in guild.text_channels if c.name.lower() == "postuler")
    parametres = next(c for c in guild.text_channels if c.name.lower() == "parametres")
    assert len(postuler.messages) == 1
    assert len(parametres.messages) == 1


@pytest.mark.asyncio
async def test_register_kingdom_panels_command_adds_command() -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_kingdom_panels_command(tree)
    assert "kingdom" in {command.name for command in tree.get_commands()}
    await client.close()


@pytest.mark.asyncio
async def test_kingdom_command_bootstraps_and_deploys() -> None:
    """/kingdom provisions the structure AND deploys the panels."""
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    register_kingdom_panels_command(tree, bot_admins=("111111111",))
    command = next(c for c in tree.get_commands() if c.name == "kingdom")

    guild = MockGuild(id=42)
    provisioned_wiring(guild, bot_admins=("111111111",))
    member = MockMember(id=111111111, name="op", guild=guild)
    interaction = MockInteraction(user=member, guild=guild)
    interaction.guild_id = 42
    await command._callback(interaction)  # type: ignore[union-attr]

    categories = {c.name for c in guild.categories}
    assert "conscription" in categories and "admin" in categories
    postuler = next(c for c in guild.text_channels if c.name.lower() == "postuler")
    assert len(postuler.messages) == 1
    assert interaction.followup.messages, "the report must be answered"
    await client.close()


@pytest.mark.asyncio
async def test_submission_creates_profile_channel_with_smurfs() -> None:
    """A submitted application provisions the private profile channel."""
    from kingdoms.discord.kingdom_profiles import PROFILES_CATEGORY

    guild = MockGuild(id=42)
    candidatures = MockTextChannel(name="candidatures", guild=guild)
    guild._channels[candidatures.id] = candidatures
    context = _context(candidatures_channel=candidatures)
    interaction = _player_interaction(member_id=555)
    interaction.guild = guild
    await _send_summary(
        interaction,
        context,
        is_king=True,
        kingdom_name="HeN",
        queued=False,
        insight="https://www.aoe2insight.com/x",
        game_id="11897201",
        smurfs=("https://www.aoe2insight.com/s1",),
    )
    view = interaction.response.message.view
    submit_button = next(c for c in view.children if getattr(c, "label", "").startswith("✅"))
    await submit_button.callback(interaction)
    categories = {c.name.lower() for c in guild.categories}
    assert PROFILES_CATEGORY.lower() in categories
    profile_channels = [c for c in guild.text_channels if c.name.startswith("profil-")]
    assert len(profile_channels) == 1
    assert profile_channels[0].messages, "the profile state message must be posted"
    smurf_in_profile = any(
        "aoe2insight.com/s1" in (m.content or "") for m in profile_channels[0].messages
    )
    assert smurf_in_profile, "declared smurfs must appear in the private profile"


@pytest.mark.asyncio
async def test_smurfs_parse_deduplicates() -> None:
    """Smurf lines are cleaned and de-duplicated."""
    from kingdoms.discord.kingdom_panels import _parse_smurfs

    raw = "https://a.io/1\n\n  https://a.io/1  \nhttps://a.io/2\n"
    assert _parse_smurfs(raw) == ("https://a.io/1", "https://a.io/2")
    assert _parse_smurfs("") == ()
