"""permissions-serveur-v1 (Drasah 19/10): the age lives in Géopolitique.

The dedicated 🏺 Âge salon is retired — the Géopolitique salon's NAME
carries the current age emoji and its marked message (age, countdown,
scale) is PINNED, edited in place at every refresh (never a pile of
pins)."""

from __future__ import annotations

from types import SimpleNamespace

from kingdoms.mods.kingdoms.kingdom_content import EPOCH_MARKER, refresh_age_pin
from tests.mocks.discord_mock import MockGuild


def _age_service() -> SimpleNamespace:
    """A minimal kingdoms service: dark age first, no running season."""
    ages = [
        SimpleNamespace(
            key="dark_age",
            display_name="Âge sombre",
            gaia_ai_level=2,
            tech_points=0,
            extra_marriages=1,
        ),
        SimpleNamespace(
            key="feudal_age",
            display_name="Âge féodal",
            gaia_ai_level=3,
            tech_points=1,
            extra_marriages=1,
        ),
    ]
    return SimpleNamespace(config=SimpleNamespace(ages=ages), current_season=_no_season)


async def _no_season() -> None:
    return None


async def test_age_pin_renames_geopolitics_and_pins_the_message(monkeypatch) -> None:
    """The Géopolitique salon carries the age emoji + the pinned age message."""
    import kingdoms.mods.kingdoms.kingdom_persistent as persistent

    guild = MockGuild()
    channel = await guild.create_text_channel("Géopolitique")
    monkeypatch.setattr(persistent, "_wiring", lambda: SimpleNamespace(channel_service=None))
    # No wired ChannelService: the lookup falls back to the tolerant
    # slug match (the live name carries the emoji).
    ok = await refresh_age_pin(guild, "fr", _age_service())
    assert ok is True
    # The salon name carries the current age emoji.
    assert channel.name == "🗺️ Géopolitique 🌑"
    # One pinned message carrying the marker.
    assert len(channel.messages) == 1
    message = channel.messages[0]
    assert getattr(message, "pinned", False) is True
    assert EPOCH_MARKER in message.content
    assert "Âge sombre" in message.content


async def test_age_pin_edits_in_place_never_a_pile_of_pins(monkeypatch) -> None:
    """A re-run edits the marked message — no second message, no pile."""
    import kingdoms.mods.kingdoms.kingdom_persistent as persistent

    guild = MockGuild()
    channel = await guild.create_text_channel("🗺️ Géopolitique 🌑")
    monkeypatch.setattr(persistent, "_wiring", lambda: SimpleNamespace(channel_service=None))
    service = _age_service()
    assert await refresh_age_pin(guild, "fr", service) is True
    assert await refresh_age_pin(guild, "fr", service) is True
    assert len(channel.messages) == 1
    assert channel.messages[0].edited is True
    # The renamed name is stable — no churn on a converged salon.
    assert channel.name == "🗺️ Géopolitique 🌑"
