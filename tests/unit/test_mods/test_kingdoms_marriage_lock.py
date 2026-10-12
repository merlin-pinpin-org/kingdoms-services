"""Kingdoms marriage lock livery & royal weddings — unit tests (2026-10-11).

Drasah's rules of the tour:

- the LOCK visibility: a fresh spouse wears the red 💍 Verrouillé
  role (the one livery — a role never notifies), the salon views show
  the lock END TIME (never a stale remaining duration), and the
  scheduler reconciles the role with the live documents every tick;
- the ROYAL wedding (Reine): a King's marriage costs TWO points (or
  TWO arranged tokens), doubles the combat lock, grants +2 tech points
  and stays secured for the whole season — even after a combat defeat.
"""
from __future__ import annotations

import time
from typing import ClassVar

import discord
import pytest

from kingdoms.mods.kingdoms.diplomacy import (
    RoyalMarriagePointsError,
    RoyalMarriageTokenError,
    _pick_promise,
)
from kingdoms.mods.kingdoms.kingdom_panels import _strings as panels_strings
from kingdoms.mods.kingdoms.kingdom_persistent import (
    MARRIAGE_LOCK_ROLE_NAME,
    MARRIAGE_RING_ROLE_NAME,
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
    sync_marriage_lock_roles_safe,
)
from kingdoms.mods.kingdoms.kingdom_realm_content import (
    _strings as realm_strings,
)
from kingdoms.mods.kingdoms.kingdom_realm_content import deploy_realm_content
from kingdoms.mods.kingdoms.kingdom_realms import ensure_realm_structure
from kingdoms.mods.kingdoms.panel_messages import message_text
from tests.mocks.discord_mock import MockGuild, MockMember

from .test_kingdoms_parish import Bundle

# ---------------------------------------------------------------------
# Royal weddings (the Reine mechanic)
# ---------------------------------------------------------------------


async def test_kings_marriage_costs_two_points_and_doubles_the_lock() -> None:
    """A royal wedding consumes TWO marriage points, doubles the palier
    lock, grants +2 tech points and flags the lord royal."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.give("Aquitaine", "celtes")
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.marriage_points = 2
    await bundle.store.upsert_kingdom(kingdom.to_mongo())

    now = __import__("datetime").datetime(2026, 10, 11, 12, 0, tzinfo=__import__("datetime").UTC)
    assert await bundle.diplomacy.marry("king-a", "celtes", now=now) == "celtes"

    kingdom = await bundle.kingdom_of("Aquitaine")
    assert kingdom.marriage_points == 0  # TWO points consumed
    assert kingdom.tech_points_bank == 2  # the royal reward
    king = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert king.royal_marriage is True
    assert king.marriage_locked_until == int(now.timestamp() + 24 * 2 * 3600)  # Chapelle doubled


async def test_royal_wedding_refuses_one_point() -> None:
    """One point cannot buy a Reine — the doubled price is enforced."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.give("Aquitaine", "celtes")
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.marriage_points = 1
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    with pytest.raises(RoyalMarriagePointsError):
        await bundle.diplomacy.marry("king-a", "celtes")


async def test_royal_arranged_marriage_costs_two_tokens() -> None:
    """A royal arranged wedding consumes TWO tokens and locks 12h."""
    bundle = Bundle()
    await bundle.launch_season()
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.arranged_marriage_stock = 1
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    with pytest.raises(RoyalMarriageTokenError):
        await bundle.diplomacy.arranged_marriage("king-a", "shu")
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.arranged_marriage_stock = 2
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    await bundle.diplomacy.arranged_marriage("king-a", "shu")
    kingdom = await bundle.kingdom_of("Aquitaine")
    assert kingdom.arranged_marriage_stock == 0
    assert kingdom.tech_points_bank == 2  # the royal reward applies too
    king = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert king.royal_marriage is True


async def test_the_queen_survives_a_defeat_but_a_lords_marriage_drops() -> None:
    """D34/Drasah: a combat defeat drops the lord marriages of the
    kingdom at the next cycle — a royal marriage NEVER drops."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.give("Aquitaine", "celtes", "shu")
    kingdom = await bundle.kingdom_of("Aquitaine")
    kingdom.marriage_points = 3
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    await bundle.diplomacy.marry("king-a", "celtes")
    await bundle.diplomacy.marry("lord-a", "shu")
    aquitaine = await bundle.kingdom_id("Aquitaine")

    await bundle.diplomacy.record_defeat(aquitaine)
    await bundle.diplomacy.recalculate()

    lords = {item.id: item for item in await bundle.kingdoms.lords()}
    assert lords["king-a"].married_civilization == "celtes"  # the Reine stays
    assert lords["king-a"].royal_marriage is True
    assert lords["lord-a"].married_civilization is None  # the lord drops
    kingdom = await bundle.kingdom_of("Aquitaine")
    assert "celtes" in kingdom.secured_civilizations
    assert "shu" not in kingdom.secured_civilizations


# ---------------------------------------------------------------------
# The strings (both locales, wire-proof)
# ---------------------------------------------------------------------


def test_church_strings_exist_in_both_locales() -> None:
    """Regression (Drasah 2026-10-11): every new Paroisse key must live
    in BOTH locales or a from-the-wire rebuild crashes silently."""
    keys = (
        "church_title",
        "church_badge_locked",
        "church_badge_free",
        "church_badge_royal_locked",
        "church_badge_royal_free",
        "church_household",
        "church_royal_lock_line",
        "church_ladder_sacree",
        "church_legend_lock",
        "church_legend_offering",
        "church_legend_token",
        "church_legend_sacree",
        "lock_days",
        "lock_time_fmt",
    )
    for locale in ("fr", "en"):
        strings = realm_strings(locale)
        for key in keys:
            assert strings.get(key), f"missing {key} in {locale}"
        # the gear icon replaced the old flask everywhere in the views
        assert "🔬" not in "".join(strings.values())
        assert "⚙️" in strings["church_tier_line"]
    for locale in ("fr", "en"):
        strings = panels_strings(locale)
        for key in (
            "church_marry_royal_ok",
            "church_marry_royal_arranged_ok",
            "church_err_royal_points",
            "church_err_royal_token",
            "royal_wedding_announce",
            "royal_wedding_couple",
            "royal_wedding_more_title",
            "royal_wedding_more_body",
        ):
            assert strings.get(key), f"missing {key} in {locale}"
        assert "🔬" not in "".join(str(v) for v in strings.values())


def test_promises_are_stable_flavoured_and_case_insensitive() -> None:
    """PROMISES.md (D34 RP): every wedding names a historical spouse —
    « Thérèse de León (Portugais) » — and the pick is SEEDED so the
    same lord with the same civilization always shows the same bride
    everywhere, without persisting anything on the lord document."""
    first = _pick_promise("king-a:Celtes", "Celtes")
    assert first in ("Boudicca", "Cartimandua", "Medb", "Macha Mong Ruad")
    assert _pick_promise("king-a:Celtes", "Celtes") == first  # stable
    assert _pick_promise("king-a:Celtes", "celtes") == first  # key casefold
    assert _pick_promise("x:Portugais", "Portugais") in (
        "Thérèse de León",
        "Isabelle de Portugal",
        "Jeanne de Portugal",
        "Béatrice de Portugal",
    )
    assert _pick_promise("x:Inconnus", "Inconnus") is None  # outside the table


# ---------------------------------------------------------------------
# The lock livery — the red 💍 Verrouillé role
# ---------------------------------------------------------------------


class _LockLord:
    def __init__(self, **kwargs: object) -> None:
        self.id = str(kwargs.get("id", "1"))
        self.kingdom_id = str(kwargs.get("kingdom_id", "k-1"))
        self.role = "lord"
        self.display_name = str(kwargs.get("display_name", "L"))
        self.left = bool(kwargs.get("left", False))
        self.married_civilization = kwargs.get("married_civilization", "azteques")
        self.marriage_locked_until = kwargs.get("marriage_locked_until")


class _LockService:
    def __init__(self, lords: list[_LockLord]) -> None:
        self._lords = lords

    async def lords(self) -> list[_LockLord]:
        return list(self._lords)


def _wired(lords: list[_LockLord]) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(kingdoms_service=_LockService(lords))
    )


def _member_with_role(guild: MockGuild, name: str, has_role: bool, id: int) -> MockMember:
    member = MockMember(id=id, name=name, guild=guild)
    guild._members[member.id] = member
    if has_role:
        role = next(
            (r for r in guild.roles if r.name == MARRIAGE_LOCK_ROLE_NAME),
            None,
        )
        if role is None:
            from tests.mocks.discord_mock import MockRole

            role = MockRole(name=MARRIAGE_LOCK_ROLE_NAME, guild=guild)
            guild._roles[role.id] = role
        member._roles.append(role)
    return member


async def test_lock_sync_grants_and_strips_the_red_role() -> None:
    """One idempotent pass: an active lock wears the role, an expired
    lock loses it, a departed player is stripped."""
    guild = MockGuild()
    active = _LockLord(id="111", marriage_locked_until=int(time.time()) + 3600)
    expired = _LockLord(id="222", marriage_locked_until=int(time.time()) - 10)
    gone = _LockLord(id="333", left=True, marriage_locked_until=int(time.time()) + 3600)
    _wired([active, expired, gone])

    m_active = _member_with_role(guild, "Actif", has_role=False, id=111)
    m_expired = _member_with_role(guild, "Expiré", has_role=False, id=222)
    m_gone = _member_with_role(guild, "Parti", has_role=True, id=333)

    changed = await sync_marriage_lock_roles_safe(guild)

    assert any(
        r.name == MARRIAGE_LOCK_ROLE_NAME for r in m_active.roles
    ), "an active lock must wear the red role"
    assert not any(
        r.name == MARRIAGE_LOCK_ROLE_NAME for r in m_expired.roles
    ), "an expired lock must not wear the role"
    assert not any(
        r.name == MARRIAGE_LOCK_ROLE_NAME for r in m_gone.roles
    ), "a departed player is stripped"
    assert changed == {"k-1"}
    # idempotent: a second pass changes nothing
    assert await sync_marriage_lock_roles_safe(guild) == set()


async def test_lock_sync_survives_a_restart_mid_honeymoon() -> None:
    """The pass doubles as the boot resync: a member whose role drifted
    during a downtime is reconciled from the live documents."""
    guild = MockGuild()
    lord = _LockLord(id="111", marriage_locked_until=int(time.time()) + 3600)
    _wired([lord])
    member = _member_with_role(guild, "Drasah", has_role=True, id=111)

    await sync_marriage_lock_roles_safe(guild)
    assert any(r.name == MARRIAGE_LOCK_ROLE_NAME for r in member.roles)

    # the lock expires while the bot is down: the next tick strips it
    # and swaps in the married ring badge (Drasah's rule, 2026-10-12)
    lord.marriage_locked_until = int(time.time()) - 1
    await sync_marriage_lock_roles_safe(guild)
    assert not any(r.name == MARRIAGE_LOCK_ROLE_NAME for r in member.roles)
    assert any(r.name == MARRIAGE_RING_ROLE_NAME for r in member.roles)


async def test_lock_sync_swaps_red_for_the_ring_and_never_both() -> None:
    """Drasah's full livery rule (2026-10-12): an ACTIVE lock wears the
    red pseudo and NOT the ring; an EXPIRED lock on a still-married
    lord swaps red for the colour-neutral 💍 ring; a broken marriage
    (defeat) or a departure loses the ring entirely."""
    guild = MockGuild()
    active = _LockLord(id="111", marriage_locked_until=int(time.time()) + 3600)
    wed = _LockLord(id="222", marriage_locked_until=int(time.time()) - 10)
    single = _LockLord(id="333", marriage_locked_until=int(time.time()) - 10)
    single.married_civilization = None
    gone = _LockLord(id="444", marriage_locked_until=int(time.time()) - 10)
    gone.left = True
    _wired([active, wed, single, gone])

    m_active = _member_with_role(guild, "Actif", has_role=False, id=111)
    m_wed = _member_with_role(guild, "Marié", has_role=False, id=222)
    m_single = _member_with_role(guild, "Célibataire", has_role=False, id=333)
    # a departed lord still wearing a stale ring loses it
    m_gone = _member_with_role(guild, "Parti", has_role=True, id=444)
    ring = discord.utils.get(guild.roles, name=MARRIAGE_RING_ROLE_NAME)
    if ring is None:
        from tests.mocks.discord_mock import MockRole

        ring = MockRole(name=MARRIAGE_RING_ROLE_NAME, guild=guild)
        guild._roles[ring.id] = ring
    m_gone._roles.append(ring)

    changed = await sync_marriage_lock_roles_safe(guild)

    assert any(r.name == MARRIAGE_LOCK_ROLE_NAME for r in m_active.roles)
    assert not any(r.name == MARRIAGE_RING_ROLE_NAME for r in m_active.roles), (
        "no ring while the lock is red — the red pseudo is the one state"
    )
    assert not any(r.name == MARRIAGE_LOCK_ROLE_NAME for r in m_wed.roles)
    assert any(r.name == MARRIAGE_RING_ROLE_NAME for r in m_wed.roles), (
        "an expired lock on a married lord swaps red for the ring badge"
    )
    assert not any(r.name == MARRIAGE_RING_ROLE_NAME for r in m_single.roles)
    assert not any(r.name == MARRIAGE_RING_ROLE_NAME for r in m_gone.roles)
    assert changed == {"k-1"}
    # idempotent
    assert await sync_marriage_lock_roles_safe(guild) == set()


# ---------------------------------------------------------------------
# The Paroisse view — end-time badges, Fidèles, legend
# ---------------------------------------------------------------------


class _Civ:
    def __init__(self, key: str, display_name: str) -> None:
        self.key = key
        self.display_name = display_name


class _ViewConfig:
    civilizations = (_Civ("azteques", "Aztèques"), _Civ("shu", "Shu"))
    maps = (_Civ("arabia", "Arabie"),)


class _ViewLord:
    def __init__(self, **kwargs: object) -> None:
        self.id = str(kwargs.get("id", "p1"))
        self.kingdom_id = "k-1"
        self.role = "lord"
        self.display_name = str(kwargs.get("display_name", "Luc"))
        self.left = False
        self.attack_used = 0
        self.defense_used = 0
        self.married_civilization = kwargs.get("married_civilization")
        self.marriage_locked_until = kwargs.get("marriage_locked_until")
        self.royal_marriage = bool(kwargs.get("royal_marriage", False))


class _ViewKingdomsService:
    config = _ViewConfig()

    def __init__(self, lords: list[_ViewLord]) -> None:
        self._lords = lords

    async def lords(self) -> list[_ViewLord]:
        return list(self._lords)

    async def kingdoms(self) -> list:
        return []

    async def territories(self) -> list:
        return []

    async def wallet(self, kingdom_id: str) -> int:
        return 7

    async def technology_state(self, kingdom_id: str) -> object:
        class _T:
            purchases: ClassVar[dict[str, int]] = {}

        return _T()


class _ViewKingdom:
    id: ClassVar[str] = "k-1"
    name: ClassVar[str] = "Avalon"
    validation: ClassVar[str] = "approved"
    is_gaia: ClassVar[bool] = False
    civilizations: ClassVar[list[str]] = ["azteques", "shu"]
    secured_civilizations: ClassVar[list[str]] = []
    marriage_capacity: ClassVar[int] = 3
    marriage_points: ClassVar[int] = 2
    arranged_marriage_stock: ClassVar[int] = 1
    parish_tier: ClassVar[int] = 2
    tech_points_bank: ClassVar[int] = 5


async def _deployed_view(lords: list[_ViewLord]) -> str:
    class _Wiring:
        kingdoms_service = _ViewKingdomsService(lords)
        territories_service = None
        economy_service = None
        attacks_service = None

    guild = MockGuild()
    await ensure_realm_structure(guild, _ViewKingdom(), [])
    await deploy_realm_content(guild, _ViewKingdom(), _Wiring())
    channel = next(
        c
        for c in guild.categories[0].channels
        if c.name.lower() == "paroisse"
    )
    live = [m for m in channel.messages if not getattr(m, "deleted", False)]
    return message_text(live[0])


async def test_church_badges_show_end_times_not_durations() -> None:
    """The lock END TIME replaces the remaining duration (Drasah: an
    end time never goes stale) and a lifted lock says so."""
    now = int(time.time())
    content = await _deployed_view(
        [
            _ViewLord(
                id="a",
                role="king",
                display_name="Arthur",
                married_civilization="shu",
                marriage_locked_until=now + 7200,
                royal_marriage=True,
            ),
            _ViewLord(
                id="b",
                display_name="Luc",
                married_civilization="azteques",
                marriage_locked_until=now - 60,
            ),
            _ViewLord(id="c", display_name="Baudouin"),
        ]
    )
    # the royal marriage wears its Queen badge — the promise herself
    # (Drasah 2026-10-12) — with the lock end time
    reine = _pick_promise("a:Shu", "Shu")
    assert f"👑 Reine {reine} (Shu) · 🔒 verrou jusqu'à" in content
    # a lifted lock says so — no more ambiguous "0h restant"
    epouse = _pick_promise("b:Aztèques", "Aztèques")
    assert f"💍 Luc — {epouse} (Aztèques) · ✅ verrou levé" in content
    # the stocks live in the title, not in a block
    assert "⛪ Paroisse — Avalon · ⛪ Église · 💍 2 · 💰 1" in content
    assert "Les Fidèles du Royaume" in content


# ---------------------------------------------------------------------
# The « En savoir plus » thread — discord.py 2.6 PartialMessage seam
# ---------------------------------------------------------------------


async def test_royal_wedding_thread_is_created_bare_then_filled() -> None:
    """Live regression (2026-10-12): channel.send() hands back a
    PartialMessage whose create_thread() takes NO content kwarg — the
    TypeError killed the thread silently. The thread is created bare,
    then the detail message is sent INSIDE it."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        _announce_royal_wedding_safe,
        _strings,
    )
    from tests.mocks.discord_mock import MockGuild

    names: list[str] = []

    class _Thread:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, content: str | None = None, **kwargs: object) -> None:
            self.sent.append(str(content or ""))

    class _PartialMessage:
        def __init__(self) -> None:
            self.thread = _Thread()

        async def create_thread(self, *, name: str | None = None, **kwargs: object) -> _Thread:
            if "content" in kwargs:
                raise TypeError(
                    "PartialMessage.create_thread() got an unexpected"
                    " keyword argument 'content'"
                )
            names.append(str(name or ""))
            return self.thread

    partial = _PartialMessage()
    guild = MockGuild()
    channel = await guild.create_text_channel("Géopolitique")

    async def _send(*args: object, **kwargs: object) -> _PartialMessage:
        return partial

    channel.send = _send  # type: ignore[method-assign]

    await _announce_royal_wedding_safe(
        guild,
        _strings("fr"),
        "Drasah",
        "HeN",
        "Bourguignons",
        24,
        king_id="1",
        lock_until=int(time.time()) + 3600,
    )

    assert names == ["En savoir plus"], "the thread carries its own name only"
    assert len(partial.thread.sent) == 1, "the detail message lives inside the thread"
    body = partial.thread.sent[0]
    epouse = _pick_promise("1:Bourguignons", "Bourguignons")
    assert epouse in body
    assert "HeN" in body
    assert "24" in body


async def test_ring_is_painted_in_the_pseudo_once_the_lock_expires() -> None:
    """Drasah's follow-up (2026-10-12): the ring must show IN the pseudo,
    not only in the profile role list — a role name emoji never renders
    in a nickname, so the sync pass edits the nickname itself."""
    guild = MockGuild()
    active = _LockLord(id="111", marriage_locked_until=int(time.time()) + 3600)
    wed = _LockLord(id="222", marriage_locked_until=int(time.time()) - 10)
    _wired([active, wed])
    m_active = _member_with_role(guild, "Actif", has_role=False, id=111)
    m_wed = _member_with_role(guild, "Drasah", has_role=False, id=222)

    await sync_marriage_lock_roles_safe(guild)

    assert m_active.nick is None, "no ring in the pseudo while the lock is red"
    assert m_wed.nick == "Drasah 💍", "the married lord wears the ring in the pseudo"
    # idempotent: the suffix never stacks
    await sync_marriage_lock_roles_safe(guild)
    assert m_wed.nick == "Drasah 💍"

    # the marriage breaks (a defeat drops a lord's marriage): the ring goes
    wed.married_civilization = None
    await sync_marriage_lock_roles_safe(guild)
    assert m_wed.nick == "Drasah"
