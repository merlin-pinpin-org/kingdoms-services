"""Kingdoms mod attack phases (D77) - unit tests.

Reference spec ``phases-attaque-spec`` behind an in-memory store: the
T-15 defense cutoff, the exceptional Embuscade (stock, +1h meeting, no
weekly budget), the Contre-espionnage cover, the T-15 reveal with the
bot-AI fallback, the T-5 pick & ban (ranked picks, sabotages, mutual
validation) and the meeting-time forfeits.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import (
    AttackError,
    DefenseCutoffError,
    EmbuscadeError,
    PickBanStateError,
)
from kingdoms.mods.kingdoms.models import AttackModel, AttackState, TechnologyState

from .test_kingdoms_attacks import Bundle

_SAFE_NOW = datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
"""A Sunday 19:50 Paris moment — outside the Lord's Day window.

FROZEN INSIDE the [T-15, T] defense window of a fresh attack (declared
12:00, T = 18:00, cutoff 17:45 — Drasah 17/10): the defense answers
land without any per-call clock dance."""


@pytest.fixture(autouse=True)
def _freeze_attacks_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _SAFE_NOW)


class PhasesBundle(Bundle):
    """The attack bundle extended with the D77 helpers."""

    async def stock(self, kingdom_id: str, **purchases: int) -> None:
        """Give bought technologies to a kingdom."""
        state = TechnologyState(
            _id=kingdom_id, season_id="s", tech_points=0, purchases=dict(purchases)
        )
        await self.store.upsert_technology(state.to_mongo())

    async def give_civs(self, kingdom_name: str, *civs: str) -> None:
        """Hand civilizations to a kingdom (the pick & ban hands)."""
        kingdom = next(k for k in await self.kingdoms.kingdoms() if k.name == kingdom_name)
        kingdom.civilizations = [*kingdom.civilizations, *civs]
        await self.store.upsert_kingdom(kingdom.to_mongo())

    async def fresh_attack(self) -> AttackModel:
        """A declared attack on Bourgogne, T = 2026-10-11 18:00 UTC."""
        bourgogne = await self.kingdom_id("Bourgogne")
        target = (await self.owner_maps(bourgogne))[0]
        return await self.attacks.declare_attack(
            "king-a", target, "aoe2de://lobby", now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        )


async def test_defense_opens_at_declaration_and_closes_at_t_minus_15() -> None:
    """Drasah 18/10: the defense window is [declaration, T-15] — open the
    second the attack is declared, closed by the T-15 reveal."""
    from kingdoms.mods.kingdoms import attacks as attacks_module

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    attack = await bundle.fresh_attack()
    assert attack.expires_at == datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
    # Past the 17:45 cutoff the shield is closed (the reveal locked it).
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
    try:
        with pytest.raises(DefenseCutoffError):
            await bundle.attacks.respond_defense(attack.id, "lord-b")
        assert attack.state is AttackState.DECLARED
    finally:
        attacks_module._now = lambda: _SAFE_NOW
    # 12:30 — right after the 12:00 declaration: the defense lands.
    defended = await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    assert defended.state is AttackState.DEFENDED


async def test_defense_at_the_meeting_time_raises() -> None:
    """Drasah 17/10: at T the defense closes — the bot AI takes over."""
    from kingdoms.mods.kingdoms import attacks as attacks_module

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    attack = await bundle.fresh_attack()
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
    try:
        with pytest.raises(DefenseCutoffError):
            await bundle.attacks.respond_defense(attack.id, "lord-b")
        assert attack.state is AttackState.DECLARED
    finally:
        attacks_module._now = lambda: _SAFE_NOW


async def test_embuscade_consumes_the_stock_and_skips_the_weekly_budget() -> None:
    """D77: one bought Embuscade per use, T = declaration + 1h, budget intact."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(3)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.stock(aquitaine, embuscade=1)
    now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    attack = await bundle.attacks.declare_embuscade(
        "king-a", (await bundle.owner_maps(bourgogne))[0], "aoe2de://x", now=now
    )
    assert attack.embuscade is True
    assert attack.expires_at == datetime(2026, 10, 11, 13, 0, tzinfo=UTC)
    assert attack.cover_lord_id is None  # the Embuscade strikes in the open
    state = await bundle.attacks.technology_state(aquitaine)
    assert state.purchases["embuscade"] == 0
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    assert lord.attack_used == 0  # the weekly budget stays intact
    # The stock is spent: the next Embuscade is refused.
    with pytest.raises(EmbuscadeError):
        await bundle.attacks.declare_embuscade(
            "king-a", (await bundle.owner_maps(bourgogne))[1], "aoe2de://y", now=now
        )


async def test_embuscade_without_stock_is_refused() -> None:
    """D77: no bought Embuscade, no exceptional attack."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(3)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    with pytest.raises(EmbuscadeError):
        await bundle.attacks.declare_embuscade(
            "king-a", (await bundle.owner_maps(bourgogne))[0], "aoe2de://x"
        )


async def test_cover_consumes_one_contre_espionnage() -> None:
    """D77: the cover hides the attacker until the T-15 reveal."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(4)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.stock(aquitaine, contre_espionnage=1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    attack = await bundle.attacks.declare_attack(
        "king-a", (await bundle.owner_maps(bourgogne))[0], "aoe2de://lobby", cover_lord_id="lord-a"
    )
    assert attack.cover_lord_id == "lord-a"
    state = await bundle.attacks.technology_state(aquitaine)
    assert state.purchases["contre_espionnage"] == 0
    # The cover cannot be the attacker himself, and the stock is spent.
    await bundle.stock(aquitaine, contre_espionnage=1)
    with pytest.raises(AttackError):
        await bundle.attacks.declare_attack(
            "king-a",
            (await bundle.owner_maps(bourgogne))[1],
            "aoe2de://x",
            cover_lord_id="king-a",
        )
    with pytest.raises(AttackError):
        await bundle.attacks.declare_attack(
            "king-a",
            (await bundle.owner_maps(bourgogne))[1],
            "aoe2de://y",
            cover_lord_id="lord-a",
        )


async def test_reveal_unmasks_and_the_ai_engages_at_the_meeting_time() -> None:
    """Drasah 17/10: the reveal only unmasks — the AI fallback is at T."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(5)
    attack = await bundle.fresh_attack()
    cutoff = datetime(2026, 10, 11, 17, 45, tzinfo=UTC)
    assert await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 44, tzinfo=UTC)) == []
    revealed = await bundle.attacks.reveal_due(now=cutoff)
    assert [item.id for item in revealed] == [attack.id]
    assert revealed[0].revealed is True
    # The defense stays OPEN at the cutoff: no AI, no state change.
    assert revealed[0].ai_defender is False
    assert revealed[0].state is AttackState.DECLARED
    # Idempotent: a scheduler replay after a restart changes nothing.
    assert await bundle.attacks.reveal_due(now=cutoff) == []
    # At the meeting time nobody answered — the bot AI takes the defense
    # and the RDV opens directly (no pick & ban ever left).
    expired = await bundle.attacks.expire_stale(now=attack.expires_at)
    assert [item.id for item in expired] == [attack.id]
    assert expired[0].ai_defender is True
    assert expired[0].state is AttackState.DEFENDED
    # battle-ai-rdv-v1: the draft runs even vs the AI — nothing is
    # resolved at T, the tick sends the attacker's draft DM next.
    assert not (expired[0].pickban or {}).get("resolved_pickban")


async def test_reveal_keeps_a_locked_human_defense() -> None:
    """D77: a human defense stays human — the reveal only unmasks."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(6)
    attack = await bundle.fresh_attack()
    # The defense lands right after the declaration (new window).
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    revealed = await bundle.attacks.reveal_due(now=_SAFE_NOW)
    assert [item.id for item in revealed] == [attack.id]
    assert revealed[0].ai_defender is False
    assert revealed[0].state is AttackState.DEFENDED


async def test_ai_defense_runs_the_draft_and_opens_the_meeting() -> None:
    """battle-ai-rdv-v1 (Drasah 22/10): vs the AI the draft RUNS at T —
    the AI picks 3 RANDOM civs of its hand and validates after its
    30-90 s delay; the RDV opens once the draft resolved."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(7)
    attack = await bundle.fresh_attack()
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths")
    await bundle.give_civs("Bourgogne", "huns", "perses", "byzantins")
    await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    # The defense window [17:45, 18:00) closes with no human answer.
    assert await bundle.attacks.pick_ban_due(now=datetime(2026, 10, 11, 17, 55, tzinfo=UTC)) == []
    expired = await bundle.attacks.expire_stale(now=attack.expires_at)
    assert [item.id for item in expired] == [attack.id]
    assert expired[0].ai_defender is True
    assert not (expired[0].pickban or {}).get("sent")  # the draft leaves next tick
    # T: the draft leaves — the attacker ranks, the AI arms its delay.
    sent = await bundle.attacks.pick_ban_due(now=attack.expires_at)
    assert [item.id for item in sent] == [attack.id]
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert len(fresh.pickban["defender_picks"]) == 3
    assert 30 <= fresh.pickban["ai_validate_at"] - int(attack.expires_at.timestamp()) <= 90
    # The RDV NEVER opens before the draft resolves.
    assert await bundle.attacks.score_due(now=attack.expires_at) == []
    await bundle.attacks.set_picks(
        attack.id, "king-a", ["francs", "bretons", "goths"], now=attack.expires_at
    )
    await bundle.attacks.validate_pickban(attack.id, "king-a", now=attack.expires_at)
    validated = await bundle.attacks.ai_draft_due(
        now=attack.expires_at + timedelta(seconds=90)
    )
    assert [item.id for item in validated] == [attack.id]
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.pickban["resolved_pickban"] is True
    # The next tick opens the RDV: the attacker plays the game (D20).
    due = await bundle.attacks.score_due(now=attack.expires_at + timedelta(seconds=95))
    assert [item.id for item in due] == [attack.id]
    assert due[0].state is AttackState.MEETING
    # The meeting keeps waiting for the game result (D20).
    assert await bundle.attacks.expire_stale(now=attack.expires_at + timedelta(minutes=5)) == []


async def test_forgotten_draft_defaults_to_random_picks_no_sabotage() -> None:
    """Drasah 18/10 — anti-blocking default: nobody ranks/validates in the
    draft → 3 RANDOM civs per hand, no sabotage, no faucon, RDV proceeds."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(12)
    attack = await bundle.fresh_attack()
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths", "huns")
    await bundle.give_civs("Bourgogne", "perses", "byzantins", "celtes", "maya")
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    # The reveal (T-15) locks the defense and the draft DMs leave.
    await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    due = await bundle.attacks.pick_ban_due(now=datetime(2026, 10, 11, 17, 46, tzinfo=UTC))
    assert [item.id for item in due] == [attack.id]
    # Nobody picks or validates — the meeting time applies the default.
    expired = await bundle.attacks.expire_stale(now=attack.expires_at)
    assert expired == []  # never a forfeit — the RDV opens instead
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.pickban["resolved_pickban"] is True
    assert sorted(fresh.pickban["default_picks"]) == ["attacker", "defender"]
    assert len(fresh.pickban["attacker_picks"]) == 3  # random, from the hand
    assert len(fresh.pickban["defender_picks"]) == 3
    assert fresh.pickban["attacker_sabotages"] == []
    assert fresh.pickban["defender_sabotages"] == []
    assert fresh.state is AttackState.DEFENDED  # the meeting still decides
    meetings = await bundle.attacks.score_due(now=attack.expires_at)
    assert [item.id for item in meetings] == [attack.id]
    assert meetings[0].state is AttackState.MEETING


async def test_pick_ban_opens_at_the_reveal() -> None:
    """Drasah 18/10: the draft DMs leave AT the T-15 reveal — no pick
    & ban before it, live picks after it."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(8)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths")
    # Before the reveal the draft never left — picking is refused.
    with pytest.raises(PickBanStateError):
        await bundle.attacks.set_picks(
            attack.id,
            "king-a",
            ["francs", "bretons", "goths"],
            now=datetime(2026, 10, 11, 17, 30, tzinfo=UTC),
        )
    # The reveal (17:45) locks the defense and the DMs leave.
    await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    assert await bundle.attacks.pick_ban_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    await bundle.attacks.set_picks(
        attack.id, "king-a", ["francs", "bretons", "goths"], now=datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
    )


async def test_pick_ban_ranks_picks_and_resolves_on_mutual_validation() -> None:
    """D77 (Drasah 18/10 v2): 3 ranked picks from one's hand, BLIND
    resolution — no sabotage anymore, the ranked picks stay intact."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    await bundle.attacks.pick_ban_due(now=datetime(2026, 10, 11, 17, 46, tzinfo=UTC))
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths")
    await bundle.give_civs("Bourgogne", "huns", "perses", "byzantins")
    pick_now = datetime(2026, 10, 11, 17, 56, tzinfo=UTC)
    # Malformed submissions are refused one by one.
    with pytest.raises(PickBanStateError):  # not distinct
        await bundle.attacks.set_picks(
            attack.id, "king-a", ["francs", "francs", "goths"], now=pick_now
        )
    with pytest.raises(PickBanStateError):  # outside one's hand
        await bundle.attacks.set_picks(
            attack.id, "king-a", ["francs", "huns", "goths"], now=pick_now
        )
    with pytest.raises(PickBanStateError):  # outsiders cannot pick
        await bundle.attacks.set_picks(
            attack.id, "king-b", ["huns", "perses", "byzantins"], now=pick_now
        )
    # Both sides rank their picks — no sabotage param anymore.
    await bundle.attacks.set_picks(
        attack.id, "king-a", ["francs", "bretons", "goths"], now=pick_now
    )
    await bundle.attacks.set_picks(attack.id, "lord-b", ["huns", "perses", "byzantins"], now=pick_now)
    attack = await bundle.attacks.validate_pickban(attack.id, "king-a", now=pick_now)
    assert attack.pickban.get("resolved_pickban") is not True  # one side is not enough
    attack = await bundle.attacks.validate_pickban(attack.id, "lord-b", now=pick_now)
    assert attack.pickban["resolved_pickban"] is True
    # Drasah 18/10 v2: the draft is sabotage-free — the ranked picks
    # stay intact (the battle room shows them SHUFFLED, never ranked).
    assert attack.pickban["attacker_picks"] == ["francs", "bretons", "goths"]
    assert attack.pickban["defender_picks"] == ["huns", "perses", "byzantins"]
    assert attack.pickban["attacker_sabotages"] == []
    # A resolved pick & ban is closed for further edits.
    with pytest.raises(PickBanStateError):
        await bundle.attacks.set_picks(
            attack.id, "king-a", ["francs", "bretons", "goths"], now=pick_now
        )


async def test_forgotten_draft_by_one_side_defaults_only_that_side() -> None:
    """Drasah 18/10: only the side that missed its validation gets the
    random default — the present side keeps its ranked picks, no
    sabotage, no forfait, the RDV decides."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(10)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    await bundle.attacks.pick_ban_due(now=datetime(2026, 10, 11, 17, 46, tzinfo=UTC))
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths", "huns")
    await bundle.give_civs("Bourgogne", "perses", "byzantins", "celtes", "maya")
    # The defender ranks and validates; the attacker never does.
    await bundle.attacks.set_picks(
        attack.id, "lord-b", ["perses", "byzantins", "celtes"], now=datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
    )
    await bundle.attacks.validate_pickban(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 17, 51, tzinfo=UTC)
    )
    expired = await bundle.attacks.expire_stale(now=attack.expires_at)
    assert expired == []  # the default applies — no forfait at all
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.pickban["default_picks"] == ["attacker"]
    assert len(fresh.pickban["attacker_picks"]) == 3  # random from the hand
    assert fresh.pickban["defender_picks"] == ["perses", "byzantins", "celtes"]
    assert fresh.state is AttackState.DEFENDED  # the meeting still decides
    assert fresh.winner_kingdom_id is None


async def test_meeting_time_still_waits_for_the_game_result_after_default() -> None:
    """Drasah 18/10: after the anti-blocking default the meeting opens —
    the game result (not the draft) decides the territory."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(11)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    await bundle.attacks.pick_ban_due(now=datetime(2026, 10, 11, 17, 46, tzinfo=UTC))
    assert await bundle.attacks.expire_stale(now=attack.expires_at) == []
    meetings = await bundle.attacks.score_due(now=attack.expires_at)
    assert [item.id for item in meetings] == [attack.id]
    assert meetings[0].state is AttackState.MEETING
    assert await bundle.attacks.expire_stale(now=attack.expires_at + timedelta(minutes=5)) == []


async def test_meeting_time_catches_up_an_unrevealed_attack_with_the_ai() -> None:
    """D77 default vs_ai: an expiry the reveal never reached engages the AI."""
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(12)
    attack = await bundle.fresh_attack()
    # No reveal ever ran (restart gap): the expiry catches up at T.
    expired = await bundle.attacks.expire_stale(now=attack.expires_at)
    assert [item.id for item in expired] == [attack.id]
    assert expired[0].ai_defender is True
    assert expired[0].state is AttackState.DEFENDED
    # battle-ai-rdv-v1: the draft leaves on the next tick (nothing
    # resolved at the catch-up itself).
    assert not (expired[0].pickban or {}).get("sent")
