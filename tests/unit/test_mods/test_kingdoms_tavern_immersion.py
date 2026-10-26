"""taverne-immersion-v1 (Drasah 26/10) — unit tests.

The taverne facade (outside art + Enter button) rides the 🍺 taverne
salon; entering gives the invisible « 🍺 Dans la Taverne » role — it
denies view on every other category and allows the private « 🍺
Taverne » category (Comptoir salon, inside art + Leave button). A
member inside sees ONLY the Taverne; leaving drops the role.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from kingdoms.mods.kingdoms.tavern_art import resolve_art
from kingdoms.mods.kingdoms.tavern_immersion import (
    COMPTOIR_MARKER,
    TAVERN_CATEGORY_NAME,
    TAVERN_ROLE_NAME,
    KingdomTavernEnterButton,
    KingdomTavernExitButton,
    build_comptoir_content,
    build_facade_content,
    deploy_tavern_facade,
    ensure_tavern_immersion_structure,
    enter_tavern,
    exit_tavern,
    find_tavern_role,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockMember

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fast_ephemeral(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """10 ms TTL — the auto-delete timers never outlive the test loop."""
    import kingdoms.mods.kingdoms.tavern_immersion as tavern_immersion

    monkeypatch.setattr(tavern_immersion, "_EPHEMERAL_SECONDS", 0.01)
    yield
    for task in list(tavern_immersion._EPHEMERAL_TASKS.values()):
        task.cancel()
    tavern_immersion._EPHEMERAL_TASKS.clear()


async def _realm(guild: MockGuild) -> None:
    """A court around the taverne: two categories with synced salons."""
    court = await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ")
    await guild.create_text_channel("🍺 taverne", category=court)
    beta = await guild.create_category("Beta")
    await guild.create_text_channel("general-beta", category=beta)


def _view_labels(view: object) -> list[str | None]:
    return [getattr(item, "label", None) for item in getattr(view, "children", [])]


def _button_custom_ids(view: object) -> list[str | None]:
    return [getattr(item, "custom_id", None) for item in getattr(view, "children", [])]


async def test_provisioning_creates_role_category_comptoir_and_denies_the_rest() -> None:
    guild = MockGuild()
    await _realm(guild)

    assert await ensure_tavern_immersion_structure(guild) is True
    # idempotent: the second pass changes nothing.
    assert await ensure_tavern_immersion_structure(guild) is True

    role = find_tavern_role(guild)
    assert role is not None and role.name == TAVERN_ROLE_NAME

    categories = {category.name: category for category in guild.categories}
    assert TAVERN_CATEGORY_NAME in categories
    taverne = categories[TAVERN_CATEGORY_NAME]
    # The category is private to the immersion role.
    everyone_deny = taverne.permission_overwrite_for(guild.default_role)
    assert everyone_deny is not None and everyone_deny.view_channel is False
    role_allow = taverne.permission_overwrite_for(role)
    assert role_allow is not None and role_allow.view_channel is True
    # Every OTHER category carries the role deny (the immersion).
    for name, category in categories.items():
        if name == TAVERN_CATEGORY_NAME:
            continue
        deny = category.permission_overwrite_for(role)
        assert deny is not None and deny.view_channel is False, name

    comptoir = [c for c in guild.text_channels if c.name == "comptoir"]
    assert len(comptoir) == 1 and comptoir[0].category_id == taverne.id
    # The comptoir panel is pinned with the inside art + Leave button.
    messages = comptoir[0].messages
    assert any(COMPTOIR_MARKER in m.content for m in messages)
    panel = next(m for m in messages if COMPTOIR_MARKER in m.content)
    assert panel.attachments  # the comptoir artwork rides the pin
    assert "kingdoms:tavern:exit" in (_button_custom_ids(panel.view) or [None])


async def test_facade_deploys_idempotently_with_the_door_in_button() -> None:
    guild = MockGuild()
    await _realm(guild)
    taverne_channel = next(c for c in guild.text_channels if c.name == "🍺 taverne")

    assert await deploy_tavern_facade(taverne_channel, "fr") is True
    assert await deploy_tavern_facade(taverne_channel, "fr") is True  # idempotent

    facades = [m for m in taverne_channel.messages if "kingdoms-tavern-facade" in m.content]
    assert len(facades) == 1
    facade = facades[0]
    assert facade.attachments  # the outside artwork rides the pin
    assert "Entrer dans la Taverne" in facade.content
    assert "kingdoms:tavern:enter" in (_button_custom_ids(facade.view) or [None])
    # The warning is on the facade (the member must know he loses the rest).
    assert "disparaît de ta vue" in facade.content


async def test_enter_and_exit_move_the_member_role_and_answer_ephemeral() -> None:
    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    member = MockMember(id=77, name="Heor")
    guild.add_member(member)

    court = next(c for c in guild.categories if c.name != TAVERN_CATEGORY_NAME)
    enter_interaction = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomTavernEnterButton("🚪 Entrer dans la Taverne").callback(enter_interaction)

    role = find_tavern_role(guild)
    assert role is not None and role in member.roles
    assert enter_interaction.response.deferred is True  # acknowledged first
    notes = [m.content for m in enter_interaction.followup.messages]
    assert any("pousses la porte" in (n or "") for n in notes)
    # The immersion rides the MEMBER layer: a deny even where the
    # member's player roles carry an allow.
    deny = court.permission_overwrite_for(member)
    assert deny is not None and deny.view_channel is False

    # Re-entering is a no-op note, not a second surface.
    again = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomTavernEnterButton("🚪 Entrer").callback(again)
    assert any("déjà" in (m.content or "") for m in again.followup.messages)

    exit_interaction = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomTavernExitButton("🚪 Sortir de la Taverne").callback(exit_interaction)
    assert role not in member.roles
    assert any("ressors" in (m.content or "") for m in exit_interaction.followup.messages)
    # The member deny is gone — the court comes back.
    assert court.permission_overwrite_for(member) is None

    # Out again: no-op note.
    out_again = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomTavernExitButton("🚪 Sortir").callback(out_again)
    assert any("déjà" in (m.content or "") for m in out_again.followup.messages)


async def test_the_ephemeral_notes_self_destruct() -> None:
    guild = MockGuild()
    await ensure_tavern_immersion_structure(guild)
    member = MockMember(id=88, name="Wanderer")
    guild.add_member(member)
    interaction = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomTavernEnterButton("🚪 Entrer").callback(interaction)
    assert interaction.followup.messages
    # The auto-delete timer drops the followup note (no pollution).
    await asyncio.sleep(0.05)
    assert interaction.followup.messages == []


def test_content_builders_carry_the_markers_and_titles() -> None:
    facade = build_facade_content("fr")
    assert "kingdoms-tavern-facade" in facade and "🍺 La Taverne" in facade
    comptoir = build_comptoir_content("fr")
    assert "kingdoms-tavern-comptoir" in comptoir and "🪵 Le Comptoir" in comptoir
    en = build_facade_content("en")
    assert "🍺 The Tavern" in en


def test_the_art_resolver_finds_the_uploaded_artwork() -> None:
    for which, expected in (
        ("entree", ("taverne-entree.png", "image.png")),
        ("comptoir", ("taverne-comptoir.png", "9c1b722d-2dba-4783-99dc-ee977bbf2357.jpg")),
        (
            "mercenaire",
            ("taverne-mercenaire.png", "fd9e7f9c-5c61-46c9-aae2-67e0ba606757.jpg"),
        ),
    ):
        path = resolve_art(which)
        assert path is not None and path.name in expected, which


async def test_the_member_deny_beats_a_player_role_allow() -> None:
    """The v2 regression: a role deny loses against the player roles'
    explicit allows — the immersion must ride the MEMBER overwrite."""
    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    # A lord whose player role carries an ALLOW on the court category
    # (exactly the live permission shape of permissions-serveur-v1).
    court = next(c for c in guild.categories if c.name != TAVERN_CATEGORY_NAME)
    lord_role = await guild.create_role(name="Kingdoms Lord")
    await court.set_permissions(
        lord_role, overwrite=__import__("discord").PermissionOverwrite(view_channel=True)
    )
    member = MockMember(id=55, name="LordRole", roles=[lord_role])
    guild.add_member(member)

    role = find_tavern_role(guild)
    category_id = next(
        c.id for c in guild.categories if c.name == TAVERN_CATEGORY_NAME
    )
    await enter_tavern(guild, member, role, category_id)

    overwrite = court.permission_overwrite_for(member)
    assert overwrite is not None and overwrite.view_channel is False

    await exit_tavern(guild, member, role, category_id)
    assert court.permission_overwrite_for(member) is None


async def test_provisioning_self_heals_the_member_layer() -> None:
    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    court = next(c for c in guild.categories if c.name != TAVERN_CATEGORY_NAME)
    role = find_tavern_role(guild)
    category_id = next(c.id for c in guild.categories if c.name == TAVERN_CATEGORY_NAME)

    # A member left INSIDE by a crash (role held, member denies lost).
    inside = MockMember(id=66, name="Stuck", roles=[role])
    guild.add_member(inside)
    # A member with a STALE member deny but no role (half-done exit).
    outside = MockMember(id=67, name="Ghost")
    guild.add_member(outside)
    await court.set_permissions(
        outside,
        overwrite=__import__("discord").PermissionOverwrite(view_channel=False),
    )

    await ensure_tavern_immersion_structure(guild)  # the self-heal pass

    deny = court.permission_overwrite_for(inside)
    assert deny is not None and deny.view_channel is False
    assert court.permission_overwrite_for(outside) is None
