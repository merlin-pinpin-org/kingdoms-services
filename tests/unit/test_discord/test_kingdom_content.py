"""Unit tests for the salons-first live content refreshers (kingdoms#138 group 1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from kingdoms.discord.kingdom_content import (
    LORDS_MARKER,
    PRESENTATION_MARKER,
    REGLES_DESCRIPTION,
    TAVERNE_DESCRIPTION,
    announce_enrollment,
    refresh_channel_descriptions,
    refresh_epoch_channel,
    refresh_lords_roster,
    refresh_presentation,
    refresh_salons_content,
)
from kingdoms.discord.kingdom_setup import _slug, provision_structure
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import KingdomModel, KingdomType, LordModel, LordRole, SeasonState
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockMember
from tests.mocks.provision import provisioned_wiring


class _FakeService:
    """A minimal KingdomsService stub (kingdoms, lords, season, config)."""

    def __init__(
        self,
        season: SeasonState | None,
        kingdoms: list[KingdomModel] | None = None,
        lords: list[LordModel] | None = None,
    ) -> None:
        self._season = season
        self._kingdoms = kingdoms or []
        self._lords = lords or []
        self.config = default_season_config()

    async def current_season(self) -> SeasonState | None:
        """Return the injected season."""
        return self._season

    async def kingdoms(self) -> list[KingdomModel]:
        """Return the injected kingdoms."""
        return list(self._kingdoms)

    async def lords(self) -> list[LordModel]:
        """Return the injected lords."""
        return list(self._lords)


def _season(age_key: str = "dark_age") -> SeasonState:
    """Build one running season in the given age."""
    return SeasonState(
        _id="2026-W40",
        started_at=datetime(2026, 9, 30, tzinfo=UTC),
        weeks=4,
        current_cycle=1,
        current_age_key=age_key,
    )


def _kingdom(name: str, id: str = "k1") -> KingdomModel:
    """Build one player kingdom."""
    return KingdomModel(_id=id, season_id="2026-W40", type=KingdomType.PLAYER, name=name)


def _lord(
    id: str,
    kingdom_id: str | None,
    role: LordRole,
    name: str | None = None,
    in_queue: bool = False,
) -> LordModel:
    """Build one active lord."""
    return LordModel(
        _id=id,
        season_id="2026-W40",
        kingdom_id=kingdom_id,
        role=role,
        display_name=name or id,
        in_queue=in_queue,
    )


def _channel(guild: MockGuild, name: str) -> Any:
    """Find one text channel by slug."""
    return next(c for c in guild.text_channels if _slug(c.name) == _slug(name))


async def _provisioned() -> MockGuild:
    """A guild carrying the full salons-first structure."""
    guild = MockGuild(id=1)
    provisioned_wiring(guild)
    await provision_structure(guild)
    return guild


@pytest.mark.asyncio
async def test_refresh_epoch_posts_and_renames() -> None:
    """The Époque channel is renamed to the current age and carries its bonuses."""
    guild = await _provisioned()
    service = _FakeService(_season("feudal_age"))
    assert await refresh_epoch_channel(guild, "fr", service)
    channel = _channel(guild, "Âge féodal")
    assert channel is not None
    content = channel.messages[0].content
    assert "Âge féodal" in content
    assert "Points de tech par cycle" in content
    assert "mercredi à minuit" in content
    # second refresh edits the marked message, no duplicate
    assert await refresh_epoch_channel(guild, "fr", service)
    marked = [m for m in channel.messages if "kingdoms:epoch:status" in (m.content or "")]
    assert len(marked) == 1


@pytest.mark.asyncio
async def test_refresh_epoch_without_season_falls_back_to_first_age() -> None:
    """Without a running season the channel keeps the starting age name."""
    guild = await _provisioned()
    service = _FakeService(None)
    assert await refresh_epoch_channel(guild, "fr", service)
    channel = _channel(guild, "Âge sombre")
    assert channel is not None
    content = channel.messages[0].content
    assert "Âge sombre" in content
    assert "Saison — cycle" not in content  # no progress line without a season


@pytest.mark.asyncio
async def test_refresh_lords_roster_groups_by_kingdom() -> None:
    """The roster lists kings, lords and the waiting queue per kingdom."""
    guild = await _provisioned()
    service = _FakeService(
        _season(),
        kingdoms=[_kingdom("Aquitaine", "k1"), _kingdom("Bourgogne", "k2")],
        lords=[
            _lord("1", "k1", LordRole.KING, "Aliénor"),
            _lord("2", "k1", LordRole.LORD, "Roger"),
            _lord("3", "k2", LordRole.KING, "Hugues"),
            _lord("4", None, LordRole.LORD, "Giscard", in_queue=True),
        ],
    )
    assert await refresh_lords_roster(guild, "fr", service)
    content = _channel(guild, "Seigneurs").messages[0].content
    assert LORDS_MARKER in content
    assert "Aquitaine" in content and "Aliénor" in content and "Roger" in content
    assert "Bourgogne" in content and "Hugues" in content
    assert "Giscard" in content  # the waiting queue


@pytest.mark.asyncio
async def test_announce_enrollment_flavors() -> None:
    """Founding, joining and queueing each send their own Géopolitique line."""
    guild = await _provisioned()
    await announce_enrollment(guild, "fr", "<@7>", "Aquitaine", True)
    await announce_enrollment(guild, "fr", "<@8>", "Aquitaine", False)
    await announce_enrollment(guild, "fr", "<@9>", None, False)
    channel = _channel(guild, "Géopolitique")
    contents = [m.content for m in channel.messages]
    assert any("fondé" in c and "Aquitaine" in c for c in contents)
    assert any("a rejoint" in c for c in contents)
    assert any("file d'attente" in c for c in contents)


@pytest.mark.asyncio
async def test_refresh_presentation_pins_the_designer_pitch() -> None:
    """The Présentation announce channel carries the designer's mod pitch."""
    guild = await _provisioned()
    assert await refresh_presentation(guild)
    channel = _channel(guild, "Présentation")
    content = channel.messages[0].content
    assert PRESENTATION_MARKER in content
    assert "mod externe" in content and "Royaume" in content
    # a second refresh edits the marked message, no duplicate
    assert await refresh_presentation(guild)
    assert len([m for m in channel.messages if PRESENTATION_MARKER in (m.content or "")]) == 1


@pytest.mark.asyncio
async def test_channel_descriptions_set_taverne_and_regles() -> None:
    """The Taverne and Règles channels get their designer description."""
    guild = await _provisioned()
    report = await refresh_channel_descriptions(guild)
    assert set(report) == {"taverne", "règles"}
    taverne = _channel(guild, "Taverne")
    assert taverne.topic == TAVERNE_DESCRIPTION
    regles = _channel(guild, "Règles")
    assert regles is not None
    assert regles.topic == REGLES_DESCRIPTION
    assert "Fair-play" in regles.topic  # the validated 11-section sommaire


@pytest.mark.asyncio
async def test_refresh_salons_content_reports_every_channel() -> None:
    """The orchestrator refreshes Époque, Seigneurs and the four forums."""
    guild = await _provisioned()
    service = _FakeService(_season())
    report = await refresh_salons_content(guild, "fr", service)
    assert report["époque"] == "deployed"
    assert report["seigneurs"] == "deployed"
    assert report["présentation"] == "deployed"
    assert set(report) == {"époque", "seigneurs", "présentation", "taverne", "règles"}


@pytest.mark.asyncio
async def test_refresh_with_no_service_skips_quietly() -> None:
    """A missing service never raises: the game refreshers return False."""
    guild = await _provisioned()
    assert await refresh_epoch_channel(guild, "fr", None) is False
    assert await refresh_lords_roster(guild, "fr", None) is False
    # the designer content does not need the service
    report = await refresh_salons_content(guild, "fr", None)
    assert set(report) == {"présentation", "taverne", "règles"}


@pytest.mark.asyncio
async def test_epoch_readonly_and_adoption_after_rename() -> None:
    """The Époque channel is read-only and survives renames across bootstraps."""
    from kingdoms.discord.kingdom_setup import provision_structure

    guild = await _provisioned()
    service = _FakeService(_season("feudal_age"))
    assert await refresh_epoch_channel(guild, "fr", service)
    epoch = _channel(guild, "Âge féodal")
    assert epoch is not None and epoch.name == "age-feodal"

    # re-running the bootstrap keeps the guild consistent, no duplicate
    await provision_structure(guild)

    # a second refresh still finds (and edits) the marked message
    assert await refresh_epoch_channel(guild, "fr", service)
    marked = [m for m in epoch.messages if "kingdoms:epoch:status" in (m.content or "")]
    assert len(marked) == 1


@pytest.mark.asyncio
async def test_reset_deletes_a_renamed_epoch_channel() -> None:
    """The admin reset removes the Époque channel even after an age rename."""
    import discord

    from kingdoms.discord.kingdom_persistent import (
        KingdomAdminButton,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    register_kingdoms_panel_wiring(KingdomsPanelWiring(bot_admins=("1",)))
    guild = await _provisioned()
    service = _FakeService(_season("feudal_age"))
    assert await refresh_epoch_channel(guild, "fr", service)
    assert any(c.name == "age-feodal" for c in guild.text_channels)

    permissions = discord.Permissions(administrator=True)
    interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    interaction.guild = guild
    interaction.locale = "fr"
    reset = KingdomAdminButton("reset", "♻️ Réinitialiser", discord.ButtonStyle.danger)
    await reset.callback(interaction)
    confirm = next(
        c for c in interaction.response.message.view.children if c.item.custom_id.endswith("reset-confirm")
    )
    confirm_interaction = MockInteraction(
        user=MockMember(id=1, name="admin", guild_permissions=permissions), guild=guild
    )
    confirm_interaction.guild = guild
    confirm_interaction.locale = "fr"
    await confirm.callback(confirm_interaction)
    remaining = {c.name for c in guild.text_channels}
    assert "Âge féodal" not in remaining
