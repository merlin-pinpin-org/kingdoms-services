"""ordre-salons-v1 (Drasah 19/10) — the grimoire charte, unit tests.

The charte renames the LIVE structure (categories small caps + emoji,
salons lowercase + emoji) BEFORE the core provisions, hides
Conscription from validated players, keeps one decorative divider per
game category, applies one micro-lore topic per salon and forces the
sidebar order (Beta → Conscription → Champs de Bataille → Kingdoms →
Gaïa → realms → Admin → Profils at the very bottom).
"""

from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.kingdom_realms import (
    delete_orphan_realm_categories,
    ensure_realm_structure,
    realm_category_name,
)
from kingdoms.mods.kingdoms.kingdom_setup import (
    _CHARTE_DIVIDER_NAME,
    _apply_charte_topics,
    _apply_structure_charte,
    _enforce_category_order,
    _enforce_player_category_gating,
    _ensure_charte_dividers,
    _hide_conscription_from_players,
    _migrate_gaia_into_kingdoms,
    _slug,
    cleanup_legacy_structure,
)
from tests.mocks.discord_mock import MockGuild

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- slug


def test_slug_is_decoration_blind() -> None:
    """The lookup slug ignores emojis and small caps — the charte can
    never break an adoption-by-slug (the Check 87 bug class)."""
    assert _slug("🛒 marché") == _slug("Marché") == "marche"
    assert _slug("✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ") == _slug("Conscription") == "conscription"
    assert _slug("🏰 ᴋɪɴɢᴅᴏᴍs") == "kingdoms"
    # restructuration-gaia-v1 (Drasah 24/10): the renamed structure
    # category slugs « kingdoms-gaia » — the realm sweep and the player
    # gating must recognize it.
    assert _slug("🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ") == _slug("Kingdoms-Gaia") == "kingdoms-gaia"
    assert _slug("⚔️ ᴄʜᴀᴍᴘs ᴅᴇ ʙᴀᴛᴀɪʟʟᴇ") == "champs-de-bataille"
    assert _slug("🗺️ Géopolitique") == "geopolitique"
    # the decorative divider slugs to nothing — it can never collide
    assert _slug(_CHARTE_DIVIDER_NAME) == ""


# ------------------------------------------------------- charte renames


async def test_charte_renames_the_live_structure_and_is_idempotent() -> None:
    guild = MockGuild()
    conscription = await guild.create_category("Conscription")
    await guild.create_text_channel("Postuler", category=conscription)
    await guild.create_text_channel("Candidatures", category=conscription)
    kingdoms = await guild.create_category("Kingdoms")
    await guild.create_text_channel("Marché", category=kingdoms)
    # a legacy realm category must NOT be renamed by the structure pass
    await guild.create_category("Royaume Avalon")

    await _apply_structure_charte(guild)

    names = {c.name for c in guild.categories}
    assert "✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ" in names
    assert "🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ" in names
    assert "Royaume Avalon" in names  # untouched: realm renames belong to kingdom_realms
    salons = {c.name for c in guild.text_channels}
    assert "📝 postuler" in salons and "📥 candidatures" in salons and "🛒 marché" in salons

    # idempotent: a second pass is a no-op (names already correct)
    await _apply_structure_charte(guild)
    assert len([c for c in guild.categories if _slug(c.name) == "conscription"]) == 1
    assert len([c for c in guild.text_channels if _slug(c.name) == "marche"]) == 1


# ------------------------------------------------- conscription hidden


async def test_conscription_hidden_from_validated_players() -> None:
    guild = MockGuild()
    category = await guild.create_category("✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ")
    lord = await guild.create_role(name="Kingdoms Lord")
    king = await guild.create_role(name="Kingdoms King")

    await _hide_conscription_from_players(guild)

    for role in (lord, king):
        overwrite = category.permission_overwrite_for(role)
        assert overwrite is not None and overwrite.view_channel is False


# ------------------------------------------------------------ dividers


async def test_charte_dividers_created_muted_and_unique() -> None:
    guild = MockGuild()
    conscription = await guild.create_category("✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ")
    battlefield = await guild.create_category("⚔️ ᴄʜᴀᴍᴘs ᴅᴇ ʙᴀᴛᴀɪʟʟᴇ")

    await _ensure_charte_dividers(guild)
    await _ensure_charte_dividers(guild)  # idempotent: never a second divider

    for category in (conscription, battlefield):
        dividers = [c for c in category.channels if _slug(c.name) == ""]
        assert len(dividers) == 1
        everyone = guild.default_role
        overwrite = dividers[0].permission_overwrite_for(everyone)
        assert overwrite is not None and overwrite.send_messages is False


# -------------------------------------------------------------- topics


async def test_charte_topics_apply_once_and_stick() -> None:
    guild = MockGuild()
    kingdoms = await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs")
    market = await guild.create_text_channel("🛒 marché", category=kingdoms)

    await _apply_charte_topics(guild)
    assert market.topic is not None and "marchands" in market.topic

    # idempotent: a fresh channel keeps its applied topic untouched
    market.topic = None
    await _apply_charte_topics(guild)
    assert market.topic is not None


# --------------------------------------------------------- category order


async def test_category_order_beta_conscription_realms_admin_profiles_last() -> None:
    guild = MockGuild()
    # everything created in the WORST order — the pass must sort it out
    await guild.create_category("👤 ᴘʀᴏꜰɪʟs")
    await guild.create_category("⚙️ ᴀᴅᴍɪɴ")
    await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs")
    await guild.create_category("🏰 ɢᴀɪᴀ")
    await guild.create_category("⚔️ ᴄʜᴀᴍᴘs ᴅᴇ ʙᴀᴛᴀɪʟʟᴇ")
    await guild.create_category("✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ")
    await guild.create_category("Beta")
    await guild.create_category("🏰 Avalon")

    await _enforce_category_order(guild)

    order = {c.name: c.position for c in guild.categories}
    assert order["Beta"] == 0
    assert order["✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ"] == 1
    assert order["⚔️ ᴄʜᴀᴍᴘs ᴅᴇ ʙᴀᴛᴀɪʟʟᴇ"] == 2
    assert order["🏰 ᴋɪɴɢᴅᴏᴍs"] == 3
    assert order["🏰 ɢᴀɪᴀ"] == 4
    assert order["🏰 Avalon"] == 5
    assert order["⚙️ ᴀᴅᴍɪɴ"] == 6  # ALWAYS behind the last realm
    assert order["👤 ᴘʀᴏꜰɪʟs"] == 7  # at the very bottom

    # a new realm category falls to the bottom of Discord's sidebar —
    # re-running the pass pushes Admin (and Profils) back in place
    camelot = await guild.create_category("🏰 Camelot")
    camelot.position = 99  # Discord drops a fresh category at the bottom
    await _enforce_category_order(guild)
    order = {c.name: c.position for c in guild.categories}
    assert order["🏰 Camelot"] == 6
    assert order["⚙️ ᴀᴅᴍɪɴ"] == 7
    assert order["👤 ᴘʀᴏꜰɪʟs"] == 8
    assert camelot is not None


async def test_category_order_without_beta_or_profiles_is_silent() -> None:
    guild = MockGuild()
    await guild.create_category("✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ")
    await guild.create_category("⚙️ ᴀᴅᴍɪɴ")
    await _enforce_category_order(guild)  # must not raise
    assert {c.name for c in guild.categories} == {"✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ", "⚙️ ᴀᴅᴍɪɴ"}


# -------------------------------------------------- support group purge


async def test_support_group_swept_by_the_legacy_cleanup() -> None:
    guild = MockGuild()
    support = await guild.create_category("Support")
    question = await guild.create_text_channel("Question", category=support)
    bug = await guild.create_text_channel("Signaler un Bug", category=support)

    removed = await cleanup_legacy_structure(guild)

    removed_names = set(removed)
    assert "Question" in removed_names and "Signaler un Bug" in removed_names
    assert "Support" in removed_names
    assert question.id not in {c.id for c in guild.text_channels}
    assert bug.id not in {c.id for c in guild.text_channels}
    assert support.id not in {c.id for c in guild.categories}


# ------------------------------------------------- realm charte adoption


class _Kingdom:
    def __init__(self, kid: str, name: str, validation: str = "approved") -> None:
        self.id = kid
        self.name = name
        self.validation = validation
        self.is_gaia = False


async def test_legacy_realm_adopted_renamed_never_duplicated() -> None:
    guild = MockGuild()
    await guild.create_role(name="kingdoms_admin")
    # the pre-patch live structure: legacy category, plain salon names
    category = await guild.create_category("Royaume Avalon")
    await guild.create_text_channel("Territoire", category=category)
    await guild.create_text_channel("Église", category=category)
    await guild.create_text_channel("Seigneurs", category=category)  # retired salon

    await ensure_realm_structure(guild, _Kingdom("k-1", "Avalon"), [])

    # ONE Avalon category, renamed to the charte display
    avalons = [c for c in guild.categories if _slug(c.name) == "avalon"]
    assert len(avalons) == 1
    assert avalons[0].name == realm_category_name("Avalon") == "🏰 Avalon"
    names = {c.name for c in avalons[0].channels}
    assert "🗺️ territoire" in names  # renamed, not duplicated
    assert "⛪ paroisse" in names  # legacy Église → charte Paroisse
    assert "Seigneurs" not in names  # the retired salon is purged
    assert len([c for c in guild.text_channels if _slug(c.name) == "territoire"]) == 1


# --------------------------------------------------------- orphan sweep


async def test_orphan_sweep_spares_the_castle_structure() -> None:
    guild = MockGuild()
    # the declared structure carries 🏰 too — it must NEVER be swept
    await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs")
    await guild.create_category("🏰 ɢᴀɪᴀ")
    keep = await guild.create_category("🏰 Avalon")
    orphan = await guild.create_category("🏰 Oublié")
    legacy_orphan = await guild.create_category("Royaume Perdu")
    kingdoms = [_Kingdom("k-1", "Avalon")]

    deleted = await delete_orphan_realm_categories(guild, kingdoms)

    assert deleted == 2
    remaining = {c.id for c in guild.categories}
    assert keep.id in remaining
    assert orphan.id not in remaining
    assert legacy_orphan.id not in remaining
    # the structure categories survive the 🏰-prefixed sweep
    assert {c.name for c in guild.categories} >= {"🏰 ᴋɪɴɢᴅᴏᴍs", "🏰 ɢᴀɪᴀ"}


async def test_orphan_sweep_keeps_legacy_named_gaia() -> None:
    """Gaïa is kept under BOTH its charte and legacy slugs (live
    incident 2026-10-11: the category vanished after a launch)."""
    guild = MockGuild()
    gaia = await guild.create_category("Royaume Gaïa")

    deleted = await delete_orphan_realm_categories(guild, [])

    assert deleted == 0
    assert gaia.id in {c.id for c in guild.categories}


# ------------------------------------------------- player category gating


async def test_player_category_headers_hidden_from_everyone() -> None:
    """permissions-serveur-v1 (20/10): a newcomer sees only Beta and
    Conscription — the four player category HEADERS are denied to
    @everyone and unlocked through the player roles."""
    guild = MockGuild()
    categories = [
        await guild.create_category(name)
        for name in ("👤 ᴘʀᴏꜰɪʟs", "🏰 ᴋɪɴɢᴅᴏᴍs", "🏰 ɢᴀɪᴀ", "⚔️ ᴄʜᴀᴍᴘs ᴅᴇ ʙᴀᴛᴀɪʟʟᴇ")
    ]
    lord = await guild.create_role(name="Kingdoms Lord")
    admin = await guild.create_role(name="Kingdoms Admin")

    await _enforce_player_category_gating(guild)

    for category in categories:
        everyone = guild.default_role
        deny = category.permission_overwrite_for(everyone)
        assert deny is not None and deny.view_channel is False, category.name
        allow = category.permission_overwrite_for(lord)
        assert allow is not None and allow.view_channel is True, category.name
        admin_allow = category.permission_overwrite_for(admin)
        assert admin_allow is not None and admin_allow.view_channel is True
        bot_allow = category.permission_overwrite_for(guild.me)
        assert bot_allow is not None and bot_allow.view_channel is True


async def test_player_category_gating_is_idempotent_and_adopts_plain_names() -> None:
    """The gating re-runs cleanly and catches a category renamed back to
    its plain form (decoration-blind slug, Check 87 bug class)."""
    guild = MockGuild()
    plain = await guild.create_category("Kingdoms")

    await _enforce_player_category_gating(guild)
    await _enforce_player_category_gating(guild)  # idempotent

    deny = plain.permission_overwrite_for(guild.default_role)
    assert deny is not None and deny.view_channel is False


async def test_player_category_gating_skips_absent_categories() -> None:
    """A guild missing the categories (e.g. mid-provision) is a no-op."""
    guild = MockGuild()
    await _enforce_player_category_gating(guild)  # must not raise


# --------------------------------------------- restructuration-gaia-v1


async def test_gaia_migration_moves_territoire_and_deletes_the_category() -> None:
    """The retired Gaïa category's salons join Kingdoms-Gaia BEFORE the
    core provisions — the core adopts by name INSIDE the declared group,
    so moving after would duplicate the Territoire salon (Check 87)."""
    guild = MockGuild()
    kingdoms = await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ")
    await guild.create_text_channel("📜 scriptorium", category=kingdoms)
    gaia = await guild.create_category("🏰 ɢᴀɪᴀ")
    territory = await guild.create_text_channel("🗺️ territoire", category=gaia)

    await _migrate_gaia_into_kingdoms(guild)

    assert territory.category is kingdoms
    assert gaia.id not in {c.id for c in guild.categories}

    # idempotent: a second pass is a silent no-op
    await _migrate_gaia_into_kingdoms(guild)
    assert territory.category is kingdoms


async def test_gaia_migration_keeps_the_category_when_a_move_fails() -> None:
    """A failed move leaves the Gaïa category in place for the next
    pass — the migration NEVER strands a salon in a deleted category."""

    class _StuckChannel:
        name = "🗺️ territoire"

        async def edit(self, **kwargs: object) -> None:
            raise RuntimeError("no permission")

    guild = MockGuild()
    kingdoms = await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ")
    gaia = await guild.create_category("🏰 ɢᴀɪᴀ")
    stuck = _StuckChannel()
    gaia.add_channel(stuck)  # type: ignore[arg-type]

    await _migrate_gaia_into_kingdoms(guild)

    assert gaia.id in {c.id for c in guild.categories}


async def test_gaia_migration_without_gaia_category_is_silent() -> None:
    guild = MockGuild()
    await guild.create_category("🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ")
    await _migrate_gaia_into_kingdoms(guild)  # must not raise
    assert {c.name for c in guild.categories} == {"🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ"}
