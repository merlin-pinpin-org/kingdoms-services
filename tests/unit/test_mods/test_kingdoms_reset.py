"""Kingdoms salons reset & panel hygiene — unit tests.

Two regression guards of the PR #244 feedback round:

- the reset must purge the per-kingdom ``Royaume …`` categories (they
  are not declared data) and must not sweep the salons living under
  them by name — otherwise the guild ends up with leftovers/duplicates.
- a panel re-deployment must find and remove its previous message on a
  real channel (Components V2: marker inside the view, no ``.messages``
  cache — only ``history()``).
"""
from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.kingdom_persistent import (
    _delete_matching_categories,
    _delete_matching_channels,
)
from kingdoms.mods.kingdoms.kingdom_realms import (
    REALMS_PANEL_MARKER,
    deploy_realms_panel,
)
from kingdoms.mods.kingdoms.panel_messages import channel_messages, message_text
from tests.mocks.discord_mock import MockGuild

pytestmark = pytest.mark.asyncio

STRUCTURE = {"patrouille", "territoire", "seigneurs", "taverne", "conscription"}


async def test_reset_sweep_deletes_realm_salons_too() -> None:
    """Drasah's duplicates incident (2026-10-11): on real Discord a
    deleted category leaves its channels orphaned at the guild root —
    the reinstall then recreates them and the guild fills with
    duplicates. The sweep must therefore delete the realm salons too,
    plus the root-level leftovers of older resets."""
    guild = MockGuild()
    conscription = await guild.create_category("Conscription")
    realm = await guild.create_category("Royaume Avalon")
    await guild.create_text_channel("Patrouille", category=realm)  # realm salon, declared name
    await guild.create_text_channel("🗺️ territoire", category=realm)
    await guild.create_text_channel("Patrouille", category=conscription)  # declared salon
    await guild.create_text_channel("Patrouille")  # top-level, declared name
    await guild.create_text_channel("🏰 le-royaume")  # root-level realm-salon leftover

    deleted = await _delete_matching_channels(guild, STRUCTURE)

    assert deleted == 5  # declared + realm salons + root leftovers
    assert [c.name for c in realm.channels] == []
    assert [c.name for c in conscription.channels] == []


async def test_reset_purges_realm_categories_too() -> None:
    guild = MockGuild()
    await guild.create_category("Conscription")
    realm = await guild.create_category("Royaume Avalon")
    await guild.create_text_channel("Seigneurs", category=realm)
    await guild.create_category("Royaume Gaïa")
    untouched = await guild.create_category("Champs de Bataille")

    deleted = await _delete_matching_categories(guild, {"conscription", "royaume-gaia"})

    # category deletes do NOT cascade on real Discord — the salons die first
    assert [c.name for c in realm.channels] == []
    assert deleted == 4  # declared + realm slice + its salon + declared Gaïa
    assert guild.categories == [untouched]


async def test_channel_messages_falls_back_to_real_history() -> None:
    guild = MockGuild()

    class _RealChannel:
        """A channel without ``.messages`` (like the real API)."""

        def __init__(self) -> None:
            self.name = "saison"

        async def history(self, limit: int):
            class _Msg:
                content = "marker inside content"

            for _ in range(3):
                yield _Msg()

    assert len(await channel_messages(_RealChannel())) == 3
    channel = await guild.create_text_channel("saison")
    await channel.send("hello")
    assert len(await channel_messages(channel)) == 1  # mock cache path


def test_message_text_walks_components_v2_trees() -> None:
    class _Item:
        def __init__(self, text: str, children: list | None = None) -> None:
            self.content = text
            self.children = children or []

    class _Message:
        content = ""
        components = [_Item("title", [_Item("-# kingdoms:panel:royaumes", [])])]  # noqa: RUF012
        layout = None

    assert REALMS_PANEL_MARKER in message_text(_Message())
    assert "marker inside" not in message_text(_Item)  # type: ignore[arg-type]


async def test_realms_panel_redeploy_removes_the_old_marker_message() -> None:
    guild = MockGuild()
    channel = await guild.create_text_channel("royaumes")

    class _Kingdom:
        id = "k-1"
        name = "Avalon"
        validation = "pending"
        is_gaia = False

    class _Service:
        async def kingdoms(self) -> list:
            return [_Kingdom()]

        async def lords(self) -> list:
            return []

    # a first deploy pins one panel; a second deploy must replace it
    assert await deploy_realms_panel(guild, "fr", _Service()) is True
    first = [m for m in await channel_messages(channel) if not getattr(m, "deleted", False)]
    assert len(first) == 1
    marker_view = first[0].layout is not None
    assert marker_view

    assert await deploy_realms_panel(guild, "fr", _Service()) is True
    live = [m for m in await channel_messages(channel) if not getattr(m, "deleted", False)]
    assert len(live) == 1  # the old one (Components V2 marker) was found and deleted


async def test_alliances_single_message_after_launch_seam() -> None:
    """alliances-single-message-v1 (Drasah 29/10): the started-phase announce
    seam no longer writes a standalone draft starter — it sweeps any
    surviving one; the unified panel is the single source."""
    from kingdoms.mods.kingdoms.kingdom_realms import (
        DRAFT_STARTER_MARKER,
        _announce_realm_alliances,
        ensure_realm_structure,
    )

    class _Service:
        async def current_season(self):  # type: ignore[no-untyped-def]
            class _Season:
                phase = "started"

            return _Season()

    class _Kingdom:
        id = "k-1"
        name = "Avalon"
        validation = "approved"
        is_gaia = False
        civilizations = ["azteques", "berberes"]  # noqa: RUF012

    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    category = guild.categories[0]
    alliances = next(c for c in category.channels if c.name == "🤝 alliances")
    await alliances.send(f"# 🎲 Draft starter — Avalon\n-# {DRAFT_STARTER_MARKER}")

    assert await _announce_realm_alliances(guild, _Kingdom(), _Service()) is True
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert live == []  # no new standalone message, the legacy one is gone

    # idempotent on a clean salon
    assert await _announce_realm_alliances(guild, _Kingdom(), _Service()) is True
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert live == []


async def test_validation_seam_writes_the_alliances_message_in_both_phases() -> None:
    """Regression (Drasah 2026-10-12): a kingdom validated via the
    Royaumes panel got its salons provisioned but the Alliances message
    was NEVER written — only the launch path wrote it. The phase-aware
    seam writes the placeholder during setup, the draft once started."""
    from kingdoms.mods.kingdoms.kingdom_realms import (
        ALLIANCES_PENDING_MARKER,
        DRAFT_STARTER_MARKER,
        _announce_realm_alliances,
        ensure_realm_structure,
    )

    class _Civ:
        def __init__(self, key: str, display_name: str) -> None:
            self.key = key
            self.display_name = display_name

    class _Config:
        civilizations = (_Civ("azteques", "Aztèques"),)

    class _Season:
        def __init__(self, phase: str) -> None:
            self.phase = phase

    class _Service:
        config = _Config()

        def __init__(self, phase: str) -> None:
            self._season = _Season(phase)

        async def current_season(self) -> _Season:
            return self._season

    class _Kingdom:
        id = "k-1"
        name = "Avalon"
        validation = "approved"
        is_gaia = False
        civilizations = ["azteques"]  # noqa: RUF012

    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    category = guild.categories[0]
    alliances = next(c for c in category.channels if c.name == "🤝 alliances")

    # setup phase: the secret placeholder, never the draft
    assert await _announce_realm_alliances(guild, _Kingdom(), _Service("setup")) is True
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1
    assert ALLIANCES_PENDING_MARKER in live[0].content

    # started phase: the placeholder dies, the draft takes its place
    assert await _announce_realm_alliances(guild, _Kingdom(), _Service("started")) is True
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1
    assert DRAFT_STARTER_MARKER in live[0].content
    assert "Aztèques" in live[0].content


async def test_reset_strips_player_roles_before_the_wipe() -> None:
    """reset-ghost-roles-v1 (Drasah 27/10): after a back-to-setup reset
    the ex-players kept their Kingdoms Lord/King roles — the whole
    channel gating is carried by those roles, so they still saw every
    game salon (and NOT Beta/Conscription). The sweep strips the
    player roles from the live lords AND heals the ghosts known only
    from the season archives; the admin role survives."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _strip_player_mod_roles_for_all_lords_safe,
        register_kingdoms_panel_wiring,
    )
    from tests.mocks.discord_mock import MockMember, MockRole

    class _Lord:
        def __init__(self, lord_id: str) -> None:
            self.id = lord_id

    class _Service:
        async def lords(self) -> list[_Lord]:
            return [_Lord("111"), _Lord("222")]

        async def season_archives(self) -> list[dict]:
            # a previously wiped season: its lords docs are GONE, the
            # archive is the only place member 333 still exists
            return [{"lords": [{"_id": "333"}, {"_id": "111"}]}]

    guild = MockGuild()
    lord_role = MockRole(name="Kingdoms Lord")
    king_role = MockRole(name="Kingdoms King")
    admin_role = MockRole(name="Kingdoms Admin")
    for role in (lord_role, king_role, admin_role):
        guild._roles[role.id] = role
    lord_member = MockMember(id=111, name="lord", roles=[lord_role, admin_role])
    king_member = MockMember(id=222, name="king", roles=[king_role])
    ghost_member = MockMember(id=333, name="ghost", roles=[lord_role])
    for member in (lord_member, king_member, ghost_member):
        guild.add_member(member)

    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=_Service()))

    assert await _strip_player_mod_roles_for_all_lords_safe(guild) == 3

    # the player roles died, the admin role survives
    assert lord_member.roles == [admin_role]
    assert king_member.roles == []
    assert ghost_member.roles == []

    # idempotent: a second sweep changes nothing
    assert await _strip_player_mod_roles_for_all_lords_safe(guild) == 3
    assert lord_member.roles == [admin_role]
    assert king_member.roles == []
    assert ghost_member.roles == []

async def test_reset_deletes_the_season_player_roles_and_their_mappings() -> None:
    """reset-ghost-roles-v2 (Drasah 29/10): blank — an ex-king of a
    previous season — kept his Kingdoms King role after back-to-setup
    (the v1 sweep is doc-driven, blind to a ghost whose docs AND
    archives no longer resolve him). The wipe now DELETES the season
    player roles (taverne door pattern: every holder stripped at once)
    and drops their mappings (a stale mapping would pin a dead role id
    forever); the reprovision recreates fresh roles and re-gates the
    categories. kingdoms_admin is a staff role: it survives."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _delete_season_player_roles_safe,
        _reprovision_mod_roles_safe,
        register_kingdoms_panel_wiring,
    )
    from tests.mocks.discord_mock import MockGuild, MockMember, MockRole

    class _SpyRole(MockRole):
        deleted = False

        async def delete(self, *, reason: str | None = None) -> None:
            self.deleted = True

    class _RoleService:
        def __init__(self) -> None:
            self.deleted_mappings: list[str] = []
            self.setup_calls: list[str] = []

        async def resolve_role_id(self, guild_id: str, mod: str, role_key: str) -> None:
            return None

        async def delete_role_mapping(self, guild_id: str, mod: str, role_key: str) -> bool:
            self.deleted_mappings.append(role_key)
            return True

        async def setup_mod_roles(self, guild_id: str, mod_name: str) -> dict:
            self.setup_calls.append(mod_name)
            return {}

    guild = MockGuild()
    lord_role = _SpyRole(name="Kingdoms Lord")
    king_role = _SpyRole(name="Kingdoms King")
    lock_role = _SpyRole(name="💍 Verrouillé")
    admin_role = _SpyRole(name="Kingdoms Admin")
    for role in (lord_role, king_role, lock_role, admin_role):
        guild._roles.append(role)
    ghost_member = MockMember(id=999, name="blank", roles=[king_role, admin_role])
    guild.add_member(ghost_member)

    service = _RoleService()
    register_kingdoms_panel_wiring(KingdomsPanelWiring(mod_roles_service=service))

    # the three season roles die, the admin role survives
    assert await _delete_season_player_roles_safe(guild) == 3
    assert lord_role.deleted and king_role.deleted and lock_role.deleted
    assert not admin_role.deleted
    # their mappings died with them (no stale dead role id pinned)
    assert service.deleted_mappings == [
        "kingdoms_lord",
        "kingdoms_king",
        "kingdoms_locked",
    ]

    # the reprovision recreates the mod roles fresh
    await _reprovision_mod_roles_safe(guild)
    assert service.setup_calls == ["kingdoms"]

    # idempotent: a second pass changes nothing and never raises
    assert await _delete_season_player_roles_safe(guild) == 3
    assert await _delete_season_player_roles_safe(None) == 0
    await _reprovision_mod_roles_safe(None)

async def test_reset_strips_member_overwrites_and_the_taverne_door_role() -> None:
    """reset-visibility-v1 (Drasah 27/10): the taverne immersion poses
    member-level view DENIES on every other category (Beta included) —
    a member who never clicked Sortir keeps them forever (the self-heal
    iterates guild.members, empty without the members intent). The
    reset sweep clears every member overwrite (the bot's own allow and
    the role denies survive) and deletes the door role."""
    import discord

    from kingdoms.mods.kingdoms.kingdom_persistent import _strip_member_overwrites_safe
    from kingdoms.mods.kingdoms.tavern_immersion import TAVERN_ROLE_NAME

    guild = MockGuild()
    beta = await guild.create_category("Beta")
    lord_role = MockRole(name="Kingdoms Lord")
    guild._roles[lord_role.id] = lord_role
    door_role = MockRole(name=TAVERN_ROLE_NAME)
    guild._roles[door_role.id] = door_role

    blank = MockMember(id=999, name="blank", roles=[door_role, lord_role])
    guild.add_member(blank)
    bot = guild.me

    # the stale immersion state: member deny on Beta + role deny + bot allow
    await beta.set_permissions(blank, overwrite=discord.PermissionOverwrite(view_channel=False))
    await beta.set_permissions(lord_role, overwrite=discord.PermissionOverwrite(view_channel=False))
    await beta.set_permissions(bot, overwrite=discord.PermissionOverwrite(view_channel=True))

    async def _door_delete(*, reason: str | None = None) -> None:
        guild._roles.pop(door_role.id, None)

    door_role.delete = _door_delete  # type: ignore[method-assign]

    assert await _strip_member_overwrites_safe(guild) == 2  # member deny + door role

    # the member deny died; the role deny and the bot allow survive
    assert beta.permission_overwrite_for(blank) is None
    assert beta.permission_overwrite_for(lord_role) is not None
    assert beta.permission_overwrite_for(bot) is not None
    assert door_role.id not in guild._roles

    # idempotent: a second sweep finds nothing left to clear
    assert await _strip_member_overwrites_safe(guild) == 0