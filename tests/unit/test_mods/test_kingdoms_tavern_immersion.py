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

import discord
import pytest

from kingdoms.mods.kingdoms.tavern_art import resolve_art
from kingdoms.mods.kingdoms.tavern_immersion import (
    COMPTOIR_MARKER,
    SOUDOYEUR_CALL_MARKER,
    SOUDOYEUR_MARKER,
    TAVERN_CATEGORY_NAME,
    TAVERN_ROLE_NAME,
    KingdomSoudoyeurRevealButton,
    KingdomTavernEnterButton,
    KingdomTavernExitButton,
    build_comptoir_content,
    build_facade_content,
    deploy_tavern_facade,
    ensure_tavern_immersion_structure,
    enter_tavern,
    exit_tavern,
    find_tavern_role,
    sweep_tavern_state,
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


async def test_the_info_and_purse_buttons_answer_ephemeral() -> None:
    """taverne-comptoir-v3 (Drasah 26/10): « Demander un renseignement »
    answers with the tenancier's RP + the plain drink effects; « Regarder
    sa bourse » answers with the live purse (cold wiring: no service)."""
    from kingdoms.mods.kingdoms.kingdom_tavern_panel import (
        KingdomTavernInfoButton,
        KingdomTavernPurseButton,
    )

    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    member = MockMember(id=93, name="Curieux")
    guild.add_member(member)

    info = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomTavernInfoButton("🗣️ Demander un renseignement").callback(info)
    assert info.response.sent and info.response.ephemeral  # EPHEMERAL answer
    text = str(info.response.message.content or "")
    assert "rien n'est gratuit" in text  # the tenancier's RP
    assert "Ce que chaque boisson fait réellement" in text  # plain effects
    assert "Cervoise" in text and "Hypocras" in text and "Eau-de-vie" in text

    purse = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomTavernPurseButton("💰 Regarder sa bourse").callback(purse)
    # Cold wiring in unit tests: the polite no-service note.
    assert purse.response.sent and purse.response.ephemeral
    assert "Taverne n'est pas branché" in str(purse.response.message.content or "")


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

async def test_the_drinks_stay_in_the_comptoir_and_the_mercenary_moves_to_the_soudoyeur() -> None:
    """taverne-panels-migration-v1 + taverne-soudoyeur-v1 (Drasah
    26/10): the drinks panel lives ONLY in the comptoir, the auction
    panel ONLY in the soudoyeur back room; old copies are swept."""
    guild = MockGuild()
    await _realm(guild)
    taverne_channel = next(c for c in guild.text_channels if c.name == "🍺 taverne")
    # The OLD pins left outside by taverne-v1 / T2b (bot-authored).
    old_drinks = await taverne_channel.send("<!-- kingdoms-tavern -->\n**🍺 La Taverne**")
    old_mercenary = await taverne_channel.send("<!-- kingdoms-mercenary -->\n⚔️ Chef Mercenaire")

    await ensure_tavern_immersion_structure(guild)
    await ensure_tavern_immersion_structure(guild)  # idempotent

    comptoir = next(c for c in guild.text_channels if c.name == "comptoir")
    soudoyeur = next(c for c in guild.text_channels if c.name == "⚔️ soudoyeur")
    comptoir_contents = [m.content for m in comptoir.messages]
    assert any("kingdoms-tavern-comptoir" in c for c in comptoir_contents)
    # taverne-comptoir-v2 (Drasah 26/10): the pinned drinks message is
    # GONE — the drinks live behind the comptoir pin's menu button.
    assert not any("<!-- kingdoms-tavern -->" in c for c in comptoir_contents)
    # The auction panel rides the soudoyeur back room now — NOT the comptoir.
    assert any("kingdoms-mercenary" in m.content for m in soudoyeur.messages)
    assert not any("kingdoms-mercenary" in c for c in comptoir_contents)
    # The old copies OUTSIDE are swept — the facade survives.
    assert old_drinks.deleted is True
    assert old_mercenary.deleted is True
    facades = [m for m in taverne_channel.messages if "kingdoms-tavern-facade" in m.content]
    assert len(facades) <= 1  # deployed by the channel pass, never swept


async def test_provisioning_creates_the_soudoyeur_hidden_back_room() -> None:
    """taverne-soudoyeur-v1 (Drasah 26/10): the back room is created
    HIDDEN (channel-level deny for @everyone AND the taverne role —
    the channel deny beats the category allow), it carries the pin +
    the auction panel, and the comptoir carries the ask-to-see
    button."""
    guild = MockGuild()
    await _realm(guild)

    await ensure_tavern_immersion_structure(guild)

    role = find_tavern_role(guild)
    assert role is not None
    soudoyeur = next(c for c in guild.text_channels if c.name == "⚔️ soudoyeur")
    assert soudoyeur.category is not None and soudoyeur.category.name == TAVERN_CATEGORY_NAME
    everyone_deny = soudoyeur.permission_overwrite_for(guild.default_role)
    assert everyone_deny is not None and everyone_deny.view_channel is False
    role_deny = soudoyeur.permission_overwrite_for(role)
    assert role_deny is not None and role_deny.view_channel is False  # hidden
    # The pin + the auction panel ride the back room.
    soudoyeur_contents = [m.content for m in soudoyeur.messages]
    assert any(SOUDOYEUR_MARKER in c for c in soudoyeur_contents)
    assert any(m.attachments for m in soudoyeur.messages)  # the hooded-table art
    assert any("kingdoms-mercenary" in c for c in soudoyeur_contents)
    # taverne-comptoir-v2: the comptoir pin carries the three
    # buttons — ask-to-see FIRST, drinks menu, door-out LAST.
    comptoir = next(c for c in guild.text_channels if c.name == "comptoir")
    pins = [m for m in comptoir.messages if COMPTOIR_MARKER in m.content]
    assert len(pins) == 1
    assert _button_custom_ids(pins[0].view) == [
        "kingdoms:tavern:info",  # first (taverne-comptoir-v3)
        "kingdoms:tavern:soudoyeur:reveal",
        "kingdoms:tavern:menu",
        "kingdoms:tavern:exit",  # last of the RP row
        "kingdoms:tavern:purse",  # apart, its own row below
    ]
    # No standalone call message, no pinned drinks message anymore.
    assert not any(SOUDOYEUR_CALL_MARKER in m.content for m in comptoir.messages)
    assert not any("<!-- kingdoms-tavern -->" in m.content for m in comptoir.messages)
    # Idempotent provisioning: no second room, no second pin, still hidden.
    await ensure_tavern_immersion_structure(guild)
    rooms = [c for c in guild.text_channels if c.name == "⚔️ soudoyeur"]
    assert len(rooms) == 1
    assert len([m for m in comptoir.messages if COMPTOIR_MARKER in m.content]) == 1
    assert soudoyeur.permission_overwrite_for(role) is not None


async def test_the_reveal_button_opens_the_room_to_everyone_and_never_returns() -> None:
    """One click on « Demander à voir le Soudoyeur » drops the role's
    channel deny (every insider sees the room), deletes the call
    message, and a later provisioning NEVER redeploys the button nor
    re-hides the room."""
    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    role = find_tavern_role(guild)
    assert role is not None
    soudoyeur = next(c for c in guild.text_channels if c.name == "⚔️ soudoyeur")
    comptoir = next(c for c in guild.text_channels if c.name == "comptoir")
    member = MockMember(id=91, name="Curieux")
    guild.add_member(member)
    pin = next(m for m in comptoir.messages if COMPTOIR_MARKER in m.content)

    interaction = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomSoudoyeurRevealButton("Demander à voir le Soudoyeur").callback(interaction)

    # The access fix (live regression): BOTH channel denies are gone —
    # the role's AND @everyone's — the synced channel falls back to the
    # category overwrites (role allow, outsiders gated by category).
    assert soudoyeur.permission_overwrite_for(role) is None
    assert soudoyeur.permission_overwrite_for(guild.default_role) is None
    assert any("soudoyeur" in (m.content or "").lower() for m in interaction.followup.messages)
    # The comptoir pin is re-rendered WITHOUT the reveal button — the
    # door-out stays LAST whatever happens.
    assert _button_custom_ids(pin.view) == [
        "kingdoms:tavern:info",
        "kingdoms:tavern:menu",
        "kingdoms:tavern:exit",
        "kingdoms:tavern:purse",
    ]

    # Provisioning after the reveal: the room stays open and synced,
    # the reveal button NEVER comes back, the pins stay single.
    await ensure_tavern_immersion_structure(guild)
    assert soudoyeur.permission_overwrite_for(role) is None  # never re-hidden
    assert soudoyeur.permission_overwrite_for(guild.default_role) is None
    assert _button_custom_ids(pin.view) == [
        "kingdoms:tavern:info",
        "kingdoms:tavern:menu",
        "kingdoms:tavern:exit",
        "kingdoms:tavern:purse",
    ]
    assert len([m for m in soudoyeur.messages if SOUDOYEUR_MARKER in m.content]) == 1
    # A second click (a stale button elsewhere) is a polite no-op.
    again = MockInteraction(user=member, guild=guild, locale="fr")
    await KingdomSoudoyeurRevealButton("Demander à voir le Soudoyeur").callback(again)
    assert any("déjà" in (m.content or "") for m in again.followup.messages)


async def test_sweep_tavern_state_heals_orphans_between_provisionings() -> None:
    """taverne-selfheal-v1 (Drasah 28/10, live report Rakussy): a
    half-done exit leaves an ORPHAN member deny (the player loses the
    view of his own realm) and the category's @everyone deny can be
    lost to a 429 — the sweep (run at every exit) heals both layers
    without waiting for the next provisioning pass."""
    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    court = next(c for c in guild.categories if c.name != TAVERN_CATEGORY_NAME)
    taverne = next(c for c in guild.categories if c.name == TAVERN_CATEGORY_NAME)
    # The orphan: a stale member deny on a member WITHOUT the role.
    ghost = MockMember(id=77, name="Ghost")
    guild.add_member(ghost)
    await court.set_permissions(
        ghost, overwrite=__import__("discord").PermissionOverwrite(view_channel=False)
    )
    # The lost @everyone deny on the category (the ♻️ 429 pattern).
    await taverne.set_permissions(guild.default_role, overwrite=None)

    await sweep_tavern_state(guild)
    await sweep_tavern_state(guild)  # idempotent

    assert court.permission_overwrite_for(ghost) is None
    everyone = taverne.permission_overwrite_for(guild.default_role)
    assert everyone is not None and everyone.view_channel is False


async def test_exit_retries_the_role_removal_then_heals(monkeypatch: pytest.MonkeyPatch) -> None:
    """taverne-exit-atomique-v1 (Drasah 28/10, live report Chiron): the
    role leaves FIRST (with retries on a 429) — a transient failure does
    not leave the half-out state (role kept, soudoyeur visible, sweep
    re-denying the realm at the next pass)."""
    import kingdoms.mods.kingdoms.tavern_immersion as tavern_immersion

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(tavern_immersion.asyncio, "sleep", _fake_sleep, raising=False)

    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    court = next(c for c in guild.categories if c.name != TAVERN_CATEGORY_NAME)
    role = find_tavern_role(guild)
    category_id = next(c.id for c in guild.categories if c.name == TAVERN_CATEGORY_NAME)
    member = MockMember(id=88, name="Chiron")
    guild.add_member(member)
    await enter_tavern(guild, member, role, category_id)
    assert court.permission_overwrite_for(member) is not None

    real_remove = member.remove_roles
    calls = {"n": 0}

    async def _flaky_remove(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 storm")
        await real_remove(*args, **kwargs)  # type: ignore[arg-type]

    member.remove_roles = _flaky_remove  # type: ignore[method-assign]

    await exit_tavern(guild, member, role, category_id)

    assert calls["n"] == 2  # retried exactly once
    assert sleeps == [2.0]  # backoff before the retry
    assert role not in member.roles
    assert court.permission_overwrite_for(member) is None


async def test_exit_hard_failure_leaves_the_player_coherently_inside() -> None:
    """The 3rd failed removal re-raises WITHOUT touching the denies —
    the player stays fully inside (role + denies), never half out."""
    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    court = next(c for c in guild.categories if c.name != TAVERN_CATEGORY_NAME)
    role = find_tavern_role(guild)
    category_id = next(c.id for c in guild.categories if c.name == TAVERN_CATEGORY_NAME)
    member = MockMember(id=89, name="StuckCoherent")
    guild.add_member(member)
    await enter_tavern(guild, member, role, category_id)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("429 forever")

    member.remove_roles = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await exit_tavern(guild, member, role, category_id)

    # Coherent inside state: role kept AND court deny kept.
    assert role in member.roles
    assert court.permission_overwrite_for(member) is not None


class _TavernKingdom:
    """A minimal kingdom stand-in for the realm provisioning seam."""

    id = "k-1"
    name = "Avalon"
    validation = "approved"
    is_gaia = False
    civilizations: list[str] = []


async def test_realm_provisioning_keeps_the_tavern_member_deny() -> None:
    """vision-gating-v1 (Drasah 30/10, Shirar live report): re-provisioning
    a realm category replaces the whole overwrite map — a tavern insider
    of that kingdom got his member ALLOW back mid-stay and saw the game
    salons again without leaving. The member deny is re-posed after every
    realm overwrite write (create, resync edit, member grant)."""
    from kingdoms.mods.kingdoms.kingdom_realms import (
        ensure_member_realm_access,
        ensure_realm_structure,
    )

    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    role = find_tavern_role(guild)
    tavern_id = next(c.id for c in guild.categories if c.name == TAVERN_CATEGORY_NAME)
    inside = MockMember(id=78, name="Shirar", roles=[role])
    guild.add_member(inside)
    await enter_tavern(guild, inside, role, tavern_id)

    category = await ensure_realm_structure(guild, _TavernKingdom(), [inside.id])
    overwrite = category.permission_overwrite_for(inside)
    assert overwrite is not None and overwrite.view_channel is False

    # the resync pass (an existing category re-edited in one call) too.
    category._overwrites = {}
    category = await ensure_realm_structure(guild, _TavernKingdom(), [inside.id])
    overwrite = category.permission_overwrite_for(inside)
    assert overwrite is not None and overwrite.view_channel is False

    # the targeted member-grant seam (a new King) keeps the deny as well.
    await ensure_member_realm_access(guild, _TavernKingdom(), inside.id)
    overwrite = category.permission_overwrite_for(inside)
    assert overwrite is not None and overwrite.view_channel is False


async def test_the_sweep_costs_zero_permission_writes_in_steady_state() -> None:
    """taverne-429-storm-fix (Drasah 31/10): the events loop runs the
    sweep on EVERY tick (~15 s) — the live sweep re-PUT the category's
    role and bot overwrites unconditionally, and Discord throttles
    channel overwrite edits into a permanent 429 livelock that saturated
    the bot's HTTP ("le bot ne répond pas", panels dying silently). A
    converged steady state must cost ZERO set_permissions calls."""
    guild = MockGuild()
    await _realm(guild)
    await ensure_tavern_immersion_structure(guild)
    await sweep_tavern_state(guild)  # converge once (provisioning + heal)

    taverne = next(c for c in guild.categories if c.name == TAVERN_CATEGORY_NAME)
    calls: list[tuple[int, bool]] = []
    original = taverne.set_permissions

    async def _spy(target: object, overwrite: object = None, **kwargs: object) -> None:
        calls.append((getattr(target, "id", 0), isinstance(target, discord.Role)))
        return await original(target, overwrite=overwrite, **kwargs)  # type: ignore[arg-type]

    taverne.set_permissions = _spy  # type: ignore[method-assign]
    try:
        await sweep_tavern_state(guild)
        await sweep_tavern_state(guild)  # twice — zero even after two ticks
    finally:
        taverne.set_permissions = original  # type: ignore[method-assign]

    assert calls == []

    # drift is still healed: the role allow disappears → one write.
    await taverne.set_permissions(find_tavern_role(guild), overwrite=None)
    before = len(calls)
    taverne.set_permissions = _spy  # type: ignore[method-assign]
    try:
        await sweep_tavern_state(guild)
    finally:
        taverne.set_permissions = original  # type: ignore[method-assign]
    assert len(calls) > before
