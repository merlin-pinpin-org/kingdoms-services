"""immersion-v1 (Drasah 20/10) — the Outgame/Ingame divide, unit tests.

The immersion hides the out-of-game categories (Beta, Conscription)
from every in-game role (lord/king/admin alike); the member opts back
in through the user-settings footer of his realm sheet — a member-
level allow that overrides the role deny, so both zones are visible
AT THE SAME TIME as the game, and the toggle is reversible.
"""

from __future__ import annotations

import discord
import pytest

from kingdoms.mods.kingdoms.kingdom_immersion import (
    KingdomOutgameHideButton,
    KingdomOutgameShowButton,
    KingdomUserSettingsButton,
    apply_immersion_gating,
    outgame_visible,
    set_outgame_visibility,
)
from kingdoms.mods.kingdoms.kingdom_realm_content import _build_overview, _strings
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockMember

pytestmark = pytest.mark.asyncio


def _walk(view: object) -> list:
    """Every node of a Components V2 tree (LIFO, mock/prod agnostic)."""
    found: list = []
    stack: list = [view]
    while stack:
        node = stack.pop()
        found.append(node)
        stack.extend(getattr(node, "children", []) or [])
    return found


async def _guild_with_zones(guild: MockGuild) -> tuple:
    beta = await guild.create_category("Beta")
    conscription = await guild.create_category("✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ")
    lord = await guild.create_role(name="Kingdoms Lord")
    admin = await guild.create_role(name="Kingdoms Admin")
    return beta, conscription, lord, admin


async def test_immersion_gating_denies_every_ingame_role_on_both_zones() -> None:
    guild = MockGuild()
    beta, conscription, lord, admin = await _guild_with_zones(guild)

    await apply_immersion_gating(guild)
    await apply_immersion_gating(guild)  # idempotent

    for category in (beta, conscription):
        for role in (lord, admin):
            overwrite = category.permission_overwrite_for(role)
            assert overwrite is not None and overwrite.view_channel is False


async def test_immersion_gating_skips_absent_zones() -> None:
    guild = MockGuild()  # neither Beta nor Conscription exists
    await apply_immersion_gating(guild)  # must not raise


async def test_member_toggle_shows_both_zones_at_the_same_time() -> None:
    guild = MockGuild()
    beta, conscription, _lord, _admin = await _guild_with_zones(guild)
    member = MockMember(id=42)
    await apply_immersion_gating(guild)

    assert outgame_visible(guild, member) is False

    moved = await set_outgame_visibility(guild, member, True)

    assert moved is True
    assert outgame_visible(guild, member) is True
    for category in (beta, conscription):
        overwrite = category.permission_overwrite_for(member)
        assert overwrite is not None and overwrite.view_channel is True

    # reversible: the toggle falls back to the role deny (immersion)
    await set_outgame_visibility(guild, member, False)
    assert outgame_visible(guild, member) is False
    for category in (beta, conscription):
        assert category.permission_overwrite_for(member) is None


async def test_settings_button_opens_the_ephemeral_toggle_view() -> None:
    guild = MockGuild()
    await _guild_with_zones(guild)
    member = MockMember(id=7)
    interaction: MockInteraction = MockInteraction(user=member, guild=guild, locale="fr")

    button = KingdomUserSettingsButton("⚙️ Paramètres utilisateur")
    await button.callback(interaction)

    assert interaction.response.ephemeral is True
    message = interaction.response.message
    assert message is not None
    view = message.view
    assert view is not None
    labels = []
    for node in _walk(view):
        if hasattr(node, "to_component_dict"):
            labels.append(node.to_component_dict().get("label"))
    assert any("hors-jeu" in (lbl or "") for lbl in labels)


async def test_show_and_hide_buttons_move_the_member_overwrites() -> None:
    guild = MockGuild()
    beta, conscription, _lord, _admin = await _guild_with_zones(guild)
    member = MockMember(id=9)
    await apply_immersion_gating(guild)

    show_interaction = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomOutgameShowButton("voir").callback(show_interaction)
    assert outgame_visible(guild, member) is True
    for category in (beta, conscription):
        overwrite = category.permission_overwrite_for(member)
        assert overwrite is not None and overwrite.view_channel is True

    hide_interaction = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomOutgameHideButton("immersion").callback(hide_interaction)
    assert outgame_visible(guild, member) is False
    for category in (beta, conscription):
        assert category.permission_overwrite_for(member) is None


def test_realm_sheet_carries_the_out_of_game_footer_zone() -> None:
    """The user-settings button lives in its OWN footer container,
    detached from the game buttons (Drasah: it must read out-of-game)."""
    kingdom = type(
        "K",
        (),
        {"name": "Avalon", "civilizations": ["azteques"]},
    )()
    members = [
        type("M", (), {"role": "king", "display_name": "Arthur", "married_civilization": None})()
    ]
    state = {
        "members": members,
        "wallet": 3,
        "territories": [],
        "purchases": {},
    }
    view = _build_overview(_strings("fr"), kingdom, state)

    containers = [c for c in view.children if type(c).__name__ == "Container"]
    assert len(containers) == 2  # the game sheet + the out-of-game footer
    footer = containers[-1]
    footer_ids = {
        getattr(node, "custom_id", None) for node in _walk(footer)
    }
    assert "kingdoms:user:settings:open" in footer_ids
    game_ids = {
        getattr(node, "custom_id", None) for node in _walk(containers[0])
    }
    assert "kingdoms:user:settings:open" not in game_ids


async def test_clear_outgame_allow_safe_strips_the_member_allow() -> None:
    """validation-immersion-v1 (Drasah 28/10, live report Rakussy): the
    approved candidate's pre-validation opt-in (a MEMBER allow that
    beats the role deny) is cleared — « Postuler » hides for the
    freshly-validated player; unknown users are a silent no-op."""
    from kingdoms.mods.kingdoms.kingdom_immersion import (
        clear_outgame_allow_safe,
    )

    guild = MockGuild()
    beta, conscription, _lord, _admin = await _guild_with_zones(guild)
    await apply_immersion_gating(guild)
    member = MockMember(id=42)
    guild.add_member(member)
    await set_outgame_visibility(guild, member, True)  # the pre-validation opt-in
    assert outgame_visible(guild, member) is True

    await clear_outgame_allow_safe(guild, member.id)

    assert outgame_visible(guild, member) is False
    for category in (beta, conscription):
        assert category.permission_overwrite_for(member) is None

    # An unknown id is a silent no-op (best effort, never raises).
    await clear_outgame_allow_safe(guild, 424242)


async def test_immersion_gating_strips_stale_outgame_allows_of_kingdom_members() -> None:
    """outgame-sweep-v1 (Drasah 28/10, live report Chiron): the « tout
    visible » member allow outlives the approval moment when the player
    was validated before validation-immersion-v1 — every provisioning
    pass now strips it from kingdom members, while out-of-game players
    keep their opt-in untouched."""
    guild = MockGuild()
    beta, conscription, lord, _admin = await _guild_with_zones(guild)
    member = MockMember(id=42, roles=[lord])
    guild.add_member(member)
    outsider = MockMember(id=43)
    guild.add_member(outsider)
    await apply_immersion_gating(guild)

    await set_outgame_visibility(guild, member, True)  # stale kingdom allow
    await set_outgame_visibility(guild, outsider, True)  # legit out-of-game opt-in

    await apply_immersion_gating(guild)  # the sweep

    assert outgame_visible(guild, member) is False
    assert outgame_visible(guild, outsider) is True
    for category in (beta, conscription):
        assert category.permission_overwrite_for(member) is None
        assert category.permission_overwrite_for(outsider) is not None


async def test_immersion_gating_mirrors_denies_on_unsynced_children() -> None:
    """immersion-enfants-v1 (Drasah 30/10, live report): « postuler »
    carries its own overwrites (the taverne pass deny) — Discord then
    ignores the category deny on it, and the Conscription zone leaked
    to validated players. The immersion mirrors its denies on every
    unsynced child; a synced child stays untouched."""
    guild = MockGuild()
    beta, conscription, lord, admin = await _guild_with_zones(guild)
    king = await guild.create_role(name="Kingdoms King")
    tavern = await guild.create_role(name="🍺 Dans la Taverne")
    postuler = await guild.create_text_channel("📝 postuler", category=conscription)
    synced = await guild.create_text_channel(" général", category=conscription)
    await postuler.set_permissions(
        tavern, overwrite=discord.PermissionOverwrite(view_channel=False)
    )

    await apply_immersion_gating(guild)
    await apply_immersion_gating(guild)  # idempotent

    for role in (lord, admin, king):
        overwrite = postuler.permission_overwrite_for(role)
        assert overwrite is not None and overwrite.view_channel is False
    # the synced child inherits from the category — no own overwrites.
    assert synced.permission_overwrite_for(lord) is None
