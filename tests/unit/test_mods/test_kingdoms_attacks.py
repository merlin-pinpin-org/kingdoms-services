"""Kingdoms mod attacks & defenses - unit tests (kingdoms-services#158, T4).

Reference §11-§13 behind an in-memory store: the attack state
machine (declaration, defense, expiry, resolution with the idempotent
territory transfer), the weekly budgets recharged at the cycle switch
(D1), the Gaïa free-for-all (D6/D42, §13.2), and the six combat
technologies (D9-D12/D18/D28/D36/D43).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import (
    AttackNotFoundError,
    AttackService,
    GaiaAttackFullError,
    InsufficientTechPointsError,
    LordsDayAttackError,
    NoBudgetError,
    SabotageError,
    SeasonClosedError,
    TechnologyLimitReachedError,
    TerritoryBusyError,
    TerritoryNotAttackableError,
    patrouille_active_at,
)
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    AttackKind,
    AttackState,
    LordRole,
    TechnologyState,
)
from kingdoms.mods.kingdoms.service import KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService

from .test_kingdoms_service import MemoryStore as _BaseStore

_SAFE_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
"""A Sunday 14:00 Paris moment — OUTSIDE the Lord's Day window.

The attacks tests that pass no ``now`` used to read the real clock,
which made them flaky every Sunday 23:30 → Monday 10:00 Paris (the
real window refuses the declarations). Freezing the module clock keeps
the suite green at any hour; the window behaviours keep their own
explicit ``now=`` / monkeypatch overrides."""


@pytest.fixture(autouse=True)
def _freeze_attacks_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _SAFE_NOW)


class MemoryStore(_BaseStore):
    """The enrollment store extended with the T3/T4 collections."""

    def __init__(self) -> None:
        super().__init__()
        self.territories: dict[str, dict] = {}
        self.attacks: dict[str, dict] = {}
        self.technologies: dict[str, dict] = {}

    async def upsert_territory(self, document: dict) -> None:
        self.territories[document["_id"]] = document

    async def find_territories(self) -> list[dict]:
        return list(self.territories.values())

    async def delete_territory(self, territory_id: str) -> None:
        self.territories.pop(territory_id, None)

    async def upsert_attack(self, document: dict) -> None:
        self.attacks[document["_id"]] = document

    async def find_attacks(self) -> list[dict]:
        return list(self.attacks.values())

    async def delete_attack(self, attack_id: str) -> None:
        self.attacks.pop(attack_id, None)

    async def upsert_technology(self, document: dict) -> None:
        self.technologies[document["_id"]] = document

    async def find_technologies(self) -> list[dict]:
        return list(self.technologies.values())


class Bundle:
    """Everything one attack test needs, rebuilt per test."""

    def __init__(self, config=None) -> None:
        self.store = MemoryStore()
        self.config = config or default_season_config()
        self.kingdoms = KingdomsService(self.store, self.config)  # type: ignore[arg-type]
        self.territories = TerritoryService(self.store, self.config, self.kingdoms)  # type: ignore[arg-type]
        self.attacks = AttackService(self.store, self.config, self.kingdoms, self.territories)  # type: ignore[arg-type]

    async def launch_season(self) -> tuple[str, str, str]:
        """Launch a free-mode season with two kingdoms and four lords."""
        await self.kingdoms.launch()
        await self.kingdoms.enroll("king-a", "King A", LordRole.KING, proposed_name="Aquitaine")
        await self.kingdoms.enroll("lord-a", "Lord A", LordRole.LORD, kingdom_name="Aquitaine")
        await self.kingdoms.enroll("king-b", "King B", LordRole.KING, proposed_name="Bourgogne")
        await self.kingdoms.enroll("lord-b", "Lord B", LordRole.LORD, kingdom_name="Bourgogne")
        gaia = next(k.id for k in await self.kingdoms.kingdoms() if k.name == GAIA_KINGDOM_KEY)
        return "Aquitaine", "Bourgogne", gaia

    async def kingdom_id(self, name: str) -> str:
        """Resolve a kingdom id by display name."""
        return next(k.id for k in await self.kingdoms.kingdoms() if k.name == name)

    async def owner_maps(self, kingdom_id: str) -> list[str]:
        """All map keys owned by a kingdom (requires a prior draw)."""
        return [
            t.map_key
            for t in await self.territories.territories()
            if t.owner_kingdom_id == kingdom_id
        ]

    async def draw(self, seed: int) -> None:
        """Draw the initial territories deterministically."""
        await self.territories.draw_initial(seed=seed)


async def test_cover_can_be_a_foreign_lord_but_never_of_the_attacked_kingdom() -> None:
    """bluff-externe-v1 (Drasah 29/10): the counter-espionage cover may
    be a lord of a FOREIGN kingdom (the false flag adds tension) — but
    NEVER a lord of the ATTACKED kingdom, and the charge survives a
    refused cover."""
    from kingdoms.mods.kingdoms.attacks import AttackError

    bundle = Bundle()
    await bundle.launch_season()
    # A third, foreign kingdom: Provence (king-c) owns no territory —
    # it only supplies a COVER lord.
    await bundle.kingdoms.enroll("king-c", "King C", LordRole.KING, proposed_name="Provence")
    await bundle.draw(31)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    # The Bundle has no stock helper (it lives on PhasesBundle) — seed
    # the purchase document directly.
    from kingdoms.mods.kingdoms.models import TechnologyState

    state = TechnologyState(_id=aquitaine, season_id="s", tech_points=0, purchases={"contre_espionnage": 1})
    await bundle.store.upsert_technology(state.to_mongo())
    # A FOREIGN cover lord (Provence) is valid — the false flag paints.
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", cover_lord_id="king-c"
    )
    assert attack.cover_lord_id == "king-c"
    assert (await bundle.attacks.technology_state(aquitaine)).purchases["contre_espionnage"] == 0
    # A cover of the ATTACKED kingdom is refused — the charge survives
    # and no attack document is created.
    await bundle.store.upsert_technology(
        TechnologyState(
            _id=aquitaine, season_id="s", tech_points=0, purchases={"contre_espionnage": 1}
        ).to_mongo()
    )
    before = len(await bundle.attacks.attacks())
    with pytest.raises(AttackError):
        await bundle.attacks.declare_attack(
            "king-a", target, "aoe2de://x", cover_lord_id="lord-b"
        )
    assert len(await bundle.attacks.attacks()) == before
    assert (await bundle.attacks.technology_state(aquitaine)).purchases["contre_espionnage"] == 1


async def test_declare_defend_resolve_captures_the_territory() -> None:
    """Nominal 1v1: declare, defend, resolve for the attacker, transfer."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://1.2.3.4/lobby")
    assert attack.state is AttackState.DECLARED
    assert attack.defender_kingdom_id == bourgogne
    # Any lord of the defending kingdom defends (D7).
    defended = await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=attack.declared_at + timedelta(minutes=10)
    )
    assert defended.state is AttackState.DEFENDED
    assert defended.defender_lord_id == "lord-b"
    resolved = await bundle.attacks.resolve(attack.id, aquitaine)
    assert resolved.state is AttackState.RESOLVED
    # Capture: the territory moved; resolving twice is idempotent.
    again = await bundle.attacks.resolve(attack.id, aquitaine)
    assert again.state is AttackState.RESOLVED
    assert target in await bundle.owner_maps(aquitaine)


async def test_defense_survives_the_naive_mongo_round_trip() -> None:
    """Mongo BSON reloads every stored datetime naive (UTC) — the T-15
    cutoff of ``respond_defense`` compares ``expires_at`` with the
    AWARE game clock and must answer, never raise (live 17/10: « can't
    compare offset-naive and offset-aware datetimes » for the
    defender; normalized at the ``from_mongo`` seam, Check 69)."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(7)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby")
    # The BSON wire shape: the stored document reloads naive (UTC).
    document = bundle.store.attacks[attack.id]
    for key in ("declared_at", "expires_at"):
        document[key] = document[key].astimezone(UTC).replace(tzinfo=None)
    defended = await bundle.attacks.respond_defense(
        attack.id, "king-b", now=attack.declared_at + timedelta(minutes=10)
    )
    assert defended.state is AttackState.DEFENDED
    # The seam re-attached UTC: every consumer reads an aware value.
    reloaded = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert reloaded.expires_at.tzinfo is not None


async def test_defense_keeps_the_territory_on_defender_win() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(2)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby")
    await bundle.attacks.respond_defense(attack.id, "king-b", now=attack.declared_at + timedelta(minutes=10))
    resolved = await bundle.attacks.resolve(attack.id, bourgogne)
    assert resolved.winner_kingdom_id == bourgogne
    assert target in await bundle.owner_maps(bourgogne)


async def test_declare_rejects_own_and_gaia_territories() -> None:
    bundle = Bundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(3)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    own = (await bundle.owner_maps(aquitaine))[0]
    with pytest.raises(TerritoryNotAttackableError):
        await bundle.attacks.declare_attack("king-a", own, "aoe2de://x")
    gaia_map = (await bundle.owner_maps(gaia))[0]
    with pytest.raises(TerritoryNotAttackableError):
        await bundle.attacks.declare_attack("king-a", gaia_map, "aoe2de://x")


async def test_double_target_is_refused_d42() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(4)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    with pytest.raises(TerritoryBusyError):
        await bundle.attacks.declare_attack("lord-a", target, "aoe2de://y")


async def test_weekly_budgets_and_recharge_d1() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(5)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    maps_b = await bundle.owner_maps(bourgogne)
    await bundle.attacks.declare_attack("king-a", maps_b[0], "aoe2de://x")
    with pytest.raises(NoBudgetError):
        await bundle.attacks.declare_attack("king-a", maps_b[1], "aoe2de://y")
    await bundle.attacks.recharge_weekly_budgets()
    await bundle.attacks.declare_attack("king-a", maps_b[1], "aoe2de://z")  # budget back


async def test_no_defense_auto_victory_captures_d8_legacy() -> None:
    """D77: auto_victory is legacy — explicit config only, never default."""
    config = default_season_config().model_copy(deep=True)
    assert config.attacks.no_defense_outcome == "vs_ai"  # the D77 default
    config.attacks.no_defense_outcome = "auto_victory"
    bundle = Bundle(config)
    await bundle.launch_season()
    await bundle.draw(6)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    later = attack.expires_at + timedelta(minutes=1)
    expired = await bundle.attacks.expire_stale(now=later)
    assert len(expired) == 1
    assert expired[0].state is AttackState.RESOLVED
    assert expired[0].winner_kingdom_id == aquitaine
    assert target in await bundle.owner_maps(aquitaine)


async def test_no_defense_vs_ai_waits_for_the_result_d8() -> None:
    """D77: nobody answers → the defending kingdom's bot AI takes the defense."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(7)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    expired = await bundle.attacks.expire_stale(now=attack.expires_at + timedelta(minutes=1))
    assert len(expired) == 1
    assert expired[0].state is AttackState.DEFENDED
    assert expired[0].ai_defender is True
    # The result still comes through the game contract (D20).
    resolved = await bundle.attacks.resolve(attack.id, bourgogne)
    assert resolved.winner_kingdom_id == bourgogne
    assert target in await bundle.owner_maps(bourgogne)


async def test_attacker_absence_consume_and_admin_restitution_d17() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(8)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.attack_used == 1  # consumed
    restituted = await bundle.attacks.restitute(attack.id)
    assert restituted.restituted is True
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.attack_used == 0  # admin exception


async def test_gaia_free_for_all_one_per_kingdom_and_winner_d6_d42() -> None:
    bundle = Bundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(9)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    gaia_map = (await bundle.owner_maps(gaia))[0]
    first = await bundle.attacks.declare_gaia_attack("king-a", gaia_map, "aoe2de://ffa")
    assert first.participants == ["king-a"]
    # Same kingdom twice: refused (D42).
    with pytest.raises(GaiaAttackFullError):
        await bundle.attacks.declare_gaia_attack("lord-a", gaia_map, "aoe2de://ffa2")
    second = await bundle.attacks.declare_gaia_attack("king-b", gaia_map, "aoe2de://ffb")
    assert second.participants == ["king-a", "king-b"]
    resolved = await bundle.attacks.resolve(second.id, aquitaine)
    assert resolved.winner_kingdom_id == aquitaine
    assert gaia_map in await bundle.owner_maps(aquitaine)


async def test_gaia_free_for_all_capacity_d6() -> None:
    config = default_season_config().model_copy(deep=True)
    config.attacks.gaia_max_participants = 2
    bundle = Bundle(config)
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(10)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    await bundle.attacks.declare_gaia_attack("king-a", gaia_map, "aoe2de://1")
    await bundle.attacks.declare_gaia_attack("king-b", gaia_map, "aoe2de://2")
    with pytest.raises(GaiaAttackFullError):
        await bundle.attacks.declare_gaia_attack("lord-b", gaia_map, "aoe2de://3")


async def test_technology_purchase_costs_and_limits_d9_d36() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(11)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    state = TechnologyState(_id=aquitaine, season_id="s", tech_points=2)
    await bundle.store.upsert_technology(state.to_mongo())
    # Jeu d'armes costs 1, limit 1 per season (default reference).
    await bundle.attacks.buy_technology(aquitaine, "jeu_d_armes")
    with pytest.raises(TechnologyLimitReachedError):
        await bundle.attacks.buy_technology(aquitaine, "jeu_d_armes")
    # 1 point left: embuscade (2) is unaffordable.
    with pytest.raises(InsufficientTechPointsError):
        await bundle.attacks.buy_technology(aquitaine, "embuscade")


async def test_defense_ai_level_traquenard_and_reinforced_patrols_marche_v2() -> None:
    """marche-v2 art. 2/5: defence bot level (cap 5), Traquenard at the
    declaration, +2h marriage locks on the target, Renforcées patrols."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(12)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    base = await bundle.attacks.base_ai_level()
    assert base >= 1
    assert await bundle.attacks.defense_ai_level(bourgogne) == base
    # Jeu d'armes: each purchase adds one PERMANENT level, ceiling 5.
    state = TechnologyState(
        _id=bourgogne,
        season_id="s",
        tech_points=10,
        purchases={"jeu_d_armes": 1},
    )
    await bundle.store.upsert_technology(state.to_mongo())
    assert await bundle.attacks.defense_ai_level(bourgogne) == min(5, base + 1)
    state.purchases["jeu_d_armes"] = 10  # far beyond the ceiling
    await bundle.store.upsert_technology(state.to_mongo())
    assert await bundle.attacks.defense_ai_level(bourgogne) == 5
    # At the ceiling the patrols become Renforcées: the 18h slot now
    # covers 18h→21h instead of 18h→20h (3h instead of 2h).
    patrol = TechnologyState(
        _id=bourgogne,
        season_id="s",
        tech_points=0,
        purchases={"patrouille_slot_1": 18},
    )
    await bundle.store.upsert_technology(patrol.to_mongo())
    twenty_thirty_paris = datetime(2026, 10, 12, 18, 30, tzinfo=UTC)  # 20h30 in Paris
    assert not patrouille_active_at(
        patrol, twenty_thirty_paris, base_level=base
    )
    assert patrouille_active_at(
        patrol, twenty_thirty_paris, base_level=5
    )
    # The Traquenard is INDEPENDENT of the declaration (recâblage v2):
    # any lord triggers it from the salon — a 6h Facile window opens on
    # the target kingdom and every ALREADY-SEALED marriage gains +2h.
    monday = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    target = (await bundle.owner_maps(bourgogne))[0]
    with pytest.raises(TechnologyLimitReachedError):
        await bundle.attacks.activate_traquenard("king-a", bourgogne, now=monday)
    funded = TechnologyState(_id=aquitaine, season_id="s", tech_points=10)
    await bundle.store.upsert_technology(funded.to_mongo())
    await bundle.attacks.buy_technology(aquitaine, "traquenard")
    bought = (await bundle.attacks.technology_state(aquitaine)).purchases["traquenard"]
    lord_b = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-b")
    lord_b.married_civilization = "celtes"
    lord_b.marriage_locked_until = int(monday.timestamp())
    await bundle.store.upsert_lord(lord_b.to_mongo())
    victim = await bundle.attacks.activate_traquenard("king-a", bourgogne, now=monday)
    assert victim.id == bourgogne
    assert victim.traquenard_until == int((monday + timedelta(hours=6)).timestamp())
    # One charge was consumed by the activation itself.
    left = (await bundle.attacks.technology_state(aquitaine)).purchases["traquenard"]
    assert left == bought - 1
    # The target kingdom's sealed marriage gained exactly +2h.
    locked = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-b")
    assert locked.marriage_locked_until == int((monday + timedelta(hours=2)).timestamp())
    # An attack declared inside the window faces the Facile AI (1/5).
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://t", now=monday
    )
    assert attack.traquenard is True
    # No Traquenard window, no traquenard flag on a later declaration.
    other = (await bundle.owner_maps(bourgogne))[1]
    king_a = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    king_a.attack_used = 0
    await bundle.store.upsert_lord(king_a.to_mongo())
    plain = await bundle.attacks.declare_attack(
        "king-a", other, "aoe2de://t", now=monday + timedelta(hours=7)
    )
    assert plain.traquenard is False


async def test_sabotage_and_counter_espionage_d12_d28() -> None:
    bundle = Bundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(13)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    sabotaged = await bundle.attacks.sabotage_civilizations(attack.id, aquitaine, ["Britons", "Franks"])
    assert sabotaged.effects["sabotage"] == ["Britons", "Franks"]
    with pytest.raises(SabotageError):  # max 2 per game
        await bundle.attacks.sabotage_civilizations(attack.id, aquitaine, ["Celts"])
    countered = await bundle.attacks.counter_espionage(attack.id, bourgogne)
    assert "sabotage" not in countered.effects
    # Gaïa free-for-alls cannot be sabotaged (kingdom-vs-kingdom only).
    gaia_map = (await bundle.owner_maps(gaia))[0]
    gaia_attack = await bundle.attacks.declare_gaia_attack("lord-a", gaia_map, "aoe2de://g")
    with pytest.raises(SabotageError):
        await bundle.attacks.sabotage_civilizations(gaia_attack.id, aquitaine, ["Britons"])
    # Only the defending kingdom counters.
    with pytest.raises(SabotageError):
        await bundle.attacks.counter_espionage(attack.id, aquitaine)


async def test_resolve_rejects_outside_kingdoms() -> None:
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(14)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x")
    with pytest.raises(AttackNotFoundError):
        await bundle.attacks.resolve("no-such-attack", bourgogne)
    # An unknown winner kingdom is refused before the state check.
    await bundle.attacks.respond_defense(attack.id, "king-b", now=attack.declared_at + timedelta(minutes=10))
    from kingdoms.mods.kingdoms.service import KingdomNotFoundError

    with pytest.raises(KingdomNotFoundError):
        await bundle.attacks.resolve(attack.id, "no-such-kingdom")


def test_attack_states_are_final_states() -> None:
    from kingdoms.mods.kingdoms.models import AttackModel

    attack = AttackModel(
        _id="a",
        season_id="s",
        kind=AttackKind.PLAYER,
        territory_id="t",
        map_key="arabia",
        defender_kingdom_id="k-2",
        attacker_lord_id="p1",
        attacker_kingdom_id="k-1",
        declared_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    assert attack.state is AttackState.DECLARED
    assert attack.is_over is False
    attack.state = AttackState.RESOLVED
    assert attack.is_over is True


async def test_declare_inside_lords_day_with_early_expiry_is_refused() -> None:
    """The Lord's Day window: a declaration landing inside is refused."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    # Sunday 2026-10-11 23:40 Paris (21:40 UTC): the window is open and
    # a 6-hour execution lands at 03:40 UTC — before Monday 10:00.
    now = datetime(2026, 10, 11, 21, 40, tzinfo=UTC)
    with pytest.raises(LordsDayAttackError):
        await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby", now=now)


async def test_declare_inside_lords_day_with_late_expiry_is_allowed() -> None:
    """A declaration inside the window is fine when it plays after it."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    # Monday 2026-10-12 07:00 Paris (05:00 UTC): the window is open but
    # the 6-hour execution lands at 11:00 UTC — after Monday 10:00.
    now = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby", now=now)
    assert attack.state is AttackState.DECLARED


async def test_expire_stale_is_deferred_during_lords_day() -> None:
    """The auto no-defense resolution waits until the window closes."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    # Declared before the window: expires Monday 02:00 UTC, inside it.
    await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", now=datetime(2026, 10, 11, 20, 0, tzinfo=UTC)
    )
    inside = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    assert await bundle.attacks.expire_stale(now=inside) == []
    after = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    assert await bundle.attacks.expire_stale(now=after) != []


async def test_resolve_is_blocked_during_lords_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A game result cannot be applied inside the window."""
    from kingdoms.mods.kingdoms import attacks as attacks_module

    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    )
    await bundle.attacks.respond_defense(attack.id, "lord-b", now=attack.declared_at + timedelta(minutes=10))
    inside = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    monkeypatch.setattr(attacks_module, "_now", lambda: inside)
    with pytest.raises(LordsDayAttackError):
        await bundle.attacks.resolve(attack.id, await bundle.kingdom_id("Aquitaine"))


async def test_forced_lords_day_window_blocks_executions() -> None:
    """The manual test window behaves like the calendar one."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    # A forced window on a Saturday afternoon (no calendar window).
    season = await bundle.kingdoms.current_season()
    assert season is not None
    season.lords_day_forced_start = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    season.lords_day_forced_end = datetime(2026, 10, 10, 22, 30, tzinfo=UTC)
    await bundle.store.upsert_season(season.to_mongo())
    # A declaration whose execution lands inside the forced window is refused.
    now = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)
    with pytest.raises(LordsDayAttackError):
        await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby", now=now)
    # A declaration that plays after the forced window closes is accepted.
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://lobby", now=datetime(2026, 10, 10, 17, 0, tzinfo=UTC)
    )
    assert attack.state is AttackState.DECLARED
    # An attack declared BEFORE the window and expiring INSIDE it keeps
    # its resolution deferred until the forced window closes.
    early = await bundle.attacks.declare_attack(
        "lord-a", (await bundle.owner_maps(bourgogne))[1], "aoe2de://lobby",
        now=datetime(2026, 10, 10, 11, 0, tzinfo=UTC),
    )
    assert early.expires_at == datetime(2026, 10, 10, 17, 0, tzinfo=UTC)
    assert await bundle.attacks.expire_stale(now=datetime(2026, 10, 10, 20, 0, tzinfo=UTC)) == []
    # Resolve is blocked while the window is open, then works after it.
    from kingdoms.mods.kingdoms import attacks as attacks_module

    attacks_module._now = lambda: datetime(2026, 10, 10, 18, 0, tzinfo=UTC)  # type: ignore[assignment]
    try:
        await bundle.attacks.respond_defense(attack.id, "lord-b", now=attack.declared_at + timedelta(minutes=10))
        with pytest.raises(LordsDayAttackError):
            await bundle.attacks.resolve(attack.id, await bundle.kingdom_id("Aquitaine"))
    finally:
        attacks_module._now = lambda: _SAFE_NOW
    resolved = await bundle.attacks.resolve(
        attack.id, await bundle.kingdom_id("Aquitaine")
    )
    assert resolved.state is AttackState.RESOLVED
    # Once the forced window is over, the stale expiry is picked up again.
    expired = await bundle.attacks.expire_stale(now=datetime(2026, 10, 10, 23, 0, tzinfo=UTC))
    assert [item.id for item in expired] == [early.id]


async def test_a_finished_season_refuses_every_combat_action() -> None:
    """Drasah's endcycle rule: after the 23:59 verdict nothing combat
    related happens until the next season launch."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    aquitaine = await bundle.kingdom_id("Aquitaine")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby")
    season = await bundle.kingdoms.current_season()
    assert season is not None
    season.finished = True
    await bundle.store.upsert_season(season.to_mongo())
    with pytest.raises(SeasonClosedError):
        await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby2")
    with pytest.raises(SeasonClosedError):
        await bundle.attacks.respond_defense(attack.id, "lord-b", now=attack.declared_at + timedelta(minutes=10))
    with pytest.raises(SeasonClosedError):
        await bundle.attacks.resolve(attack.id, aquitaine)
    # The stale expiry quietly does nothing on a finished season.
    assert await bundle.attacks.expire_stale() == []


async def test_cancel_ongoing_expires_the_ongoing_attacks() -> None:
    """The season end cancels every declared/defended attack first."""
    bundle = Bundle()
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    maps = await bundle.owner_maps(bourgogne)
    first = await bundle.attacks.declare_attack("king-a", maps[0], "aoe2de://lobby")
    second = await bundle.attacks.declare_attack("lord-a", maps[1], "aoe2de://lobby")
    await bundle.attacks.respond_defense(first.id, "lord-b", now=first.declared_at + timedelta(minutes=10))
    cancelled = await bundle.attacks.cancel_ongoing()
    assert {item.id for item in cancelled} == {first.id, second.id}
    states = {a.id: a.state for a in await bundle.attacks.attacks()}
    assert states[first.id] is AttackState.EXPIRED
    assert states[second.id] is AttackState.EXPIRED
    # Idempotent: a second pass finds nothing ongoing.
    assert await bundle.attacks.cancel_ongoing() == []
