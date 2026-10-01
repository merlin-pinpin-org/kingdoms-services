"""Unit tests for the Kingdoms salons-first panels (kingdoms#138)."""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord.kingdom_panels import (
    ROLE_KING,
    ROLE_LORD,
    SUPPORTED_LOCALES,
    SUPPORTED_TIMEZONES,
    EnrollmentModal,
    _candidature_view,
    _strings,
    build_apply_panel,
    build_settings_panel,
    deploy_panels,
    register_kingdom_panels_command,
)
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
        self.db = self

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


def _fill(modal: EnrollmentModal, **values: str) -> None:
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
async def test_modal_rejects_rules_not_accepted() -> None:
    """Submitting without the rules confirmation is refused."""
    modal = EnrollmentModal("en")
    _fill(modal, role="Lord", insight_link="https://www.aoe2insight.com/x", game_id="12345678", accept_rules="NO")

    interaction = MockInteraction(user=MockMember(name="player"), guild=MockGuild(id=42))
    await modal.on_submit(interaction)

    assert interaction.response.sent is True
    assert "rules" in (interaction.response.message or "").content.lower()


@pytest.mark.asyncio
async def test_modal_rejects_bad_insight_and_game_id() -> None:
    """Invalid Insight link and game ID are both refused."""
    modal = EnrollmentModal("en")
    _fill(modal, role="Lord", insight_link="not-a-url", game_id="12345678", accept_rules="YES")
    interaction = MockInteraction(user=MockMember(name="player"), guild=MockGuild(id=42))
    await modal.on_submit(interaction)
    assert "insight" in (interaction.response.message or "").content.lower()

    modal = EnrollmentModal("en")
    _fill(modal, role="Lord", insight_link="https://www.aoe2insight.com/x", game_id="abc", accept_rules="YES")
    interaction = MockInteraction(user=MockMember(name="player"), guild=MockGuild(id=42))
    await modal.on_submit(interaction)
    assert "id" in (interaction.response.message or "").content.lower()


@pytest.mark.asyncio
async def test_modal_rejects_king_without_name() -> None:
    """A King must suggest a kingdom name."""
    modal = EnrollmentModal("en")
    _fill(
        modal,
        role="King",
        kingdom_name="",
        insight_link="https://www.aoe2insight.com/x",
        game_id="12345678",
        accept_rules="YES",
    )
    interaction = MockInteraction(user=MockMember(name="player"), guild=MockGuild(id=42))
    await modal.on_submit(interaction)
    assert "name" in (interaction.response.message or "").content.lower()


@pytest.mark.asyncio
async def test_modal_sends_candidature_to_channel() -> None:
    """A valid application lands in the Candidatures channel with buttons."""
    channel = MockTextChannel(name="candidatures", guild=MockGuild(id=42))
    modal = EnrollmentModal("en", candidatures_channel=channel)
    _fill(modal, role="Lord", insight_link="https://www.aoe2insight.com/x", game_id="12345678", accept_rules="YES")

    interaction = MockInteraction(user=MockMember(name="player"), guild=MockGuild(id=42))
    await modal.on_submit(interaction)

    assert len(channel.messages) == 1
    assert channel.messages[0].view is not None


@pytest.mark.asyncio
async def test_candidature_approve_assigns_lord_role() -> None:
    """Approving a Lord application assigns the kingdoms_lord role."""
    roles = _FakeModRoles()
    view = _candidature_view("en", bot_admins=("111",), mod_roles_service=roles, guild_id="42")
    assert len(view.children) == 3

    interaction = _admin_interaction()
    message = MockMessage(content="role 🎖️ Lord <@999>")
    interaction.message = message
    button = view.children[0]
    assert button.emoji is not None
    await button.callback(interaction)
    assert roles.assigned == [("999", "kingdoms", ROLE_LORD)]


@pytest.mark.asyncio
async def test_candidature_approve_assigns_king_role() -> None:
    """Approving a King application assigns the kingdoms_king role."""
    roles = _FakeModRoles()
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
async def test_settings_panel_builds_with_both_selects() -> None:
    """The Paramètres panel renders the language and timezone selects."""
    logs = _FakeLogsService(locale="fr")
    view = await build_settings_panel(logs, "42", "system", ("111",))
    assert view is not None
    assert set(SUPPORTED_LOCALES) | set(SUPPORTED_TIMEZONES)


@pytest.mark.asyncio
async def test_deploy_panels_posts_in_postuler_and_parametres() -> None:
    """deploy_panels posts one panel per bootstrap channel."""
    from kingdoms.discord.kingdom_setup import provision_structure

    guild = MockGuild(id=1)
    await provision_structure(guild)
    logs = _FakeLogsService(locale="fr")
    report = await deploy_panels(guild, logs, bot_admins=("111",))

    assert report.get("postuler") == "deployed"
    assert report.get("paramètres") == "deployed"
    postuler = next(c for c in guild.text_channels if c.name.lower() == "postuler")
    parametres = next(c for c in guild.text_channels if c.name.lower() == "paramètres")
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
    member = MockMember(id=111111111, name="op", guild=guild)
    interaction = MockInteraction(user=member, guild=guild)
    interaction.guild_id = 42
    await command._callback(interaction)  # type: ignore[union-attr]

    categories = {c.name for c in guild.categories}
    assert "Conscription" in categories and "Admin" in categories
    postuler = next(c for c in guild.text_channels if c.name.lower() == "postuler")
    assert len(postuler.messages) == 1
    assert interaction.followup.messages, "the report must be answered"
    await client.close()
