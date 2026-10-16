"""Kingdoms attack phase taxonomy + Prêt gate (attaques-phases-v2) - unit tests.

The player-facing phases (Siège → Draft → RDV/Ready → Score) behind the
engine's internal T-15/T-5/T/T+5 clock, the FIX gate Prêt (no result
declaration without a presence confirmation), the early close of the
Ready phase (everyone Prêt opens the Score phase right away — T+5 stays
the forfeit deadline only) and the 3h no-result reminder line.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import (
    AttackStateError,
    ScoreNotReadyError,
    expected_score_players,
    phase_label,
)
from kingdoms.mods.kingdoms.models import AttackState

from .test_kingdoms_attack_score import (
    _CUTOFF,
    _LEAD,
    _PICK_NOW,
    _T,
    _T5,
    ScoreBundle,
)

_T3H = _T + timedelta(hours=3)  # the 3h reminder line
_BEFORE3H = _T + timedelta(hours=3) - timedelta(minutes=1)
_T15H = _T + timedelta(hours=15)  # the auto-dispute line (3h reminder + 12h)
_BEFORE15H = _T15H - timedelta(minutes=1)
_FROZEN_NOW = datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
"""Frozen between the T declaration (12:00) and the T-15 cutoff."""


@pytest.fixture(autouse=True)
def _freeze_attacks_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _FROZEN_NOW)


async def _get(bundle: ScoreBundle, attack_id: str):
    return next(item for item in await bundle.attacks.attacks() if item.id == attack_id)


async def test_phase_labels_follow_the_timeline() -> None:
    """The engine phases: siege → draft → ready → score (internal clock hidden)."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack = await bundle.fresh_attack()
    assert phase_label(attack) == "siege"  # declaration + defense, pre-reveal
    # The same attack walks the whole timeline (one territory, one attack).
    aquitaine, bourgogne = await bundle.kingdom_id("Aquitaine"), await bundle.kingdom_id("Bourgogne")
    del bourgogne
    await bundle.attacks.respond_defense(attack.id, "lord-b")
    await bundle.attacks.reveal_due(now=_CUTOFF)
    assert phase_label(await _get(bundle, attack.id)) == "draft"  # revealed, pick & ban
    await bundle.attacks.pick_ban_due(now=_LEAD)
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths")
    await bundle.give_civs("Bourgogne", "huns", "perses", "byzantins")
    await bundle.attacks.set_picks(attack.id, "king-a", ["francs", "bretons", "goths"], now=_PICK_NOW)
    await bundle.attacks.set_picks(attack.id, "lord-b", ["huns", "perses", "byzantins"], now=_PICK_NOW)
    await bundle.attacks.validate_pickban(attack.id, "king-a", now=_PICK_NOW)
    await bundle.attacks.validate_pickban(attack.id, "lord-b", now=_PICK_NOW)
    await bundle.attacks.score_due(now=_T)
    assert phase_label(await _get(bundle, attack.id)) == "ready"  # the RDV window
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    await bundle.attacks.mark_ready(attack.id, "lord-b", now=_T)
    assert phase_label(await _get(bundle, attack.id)) == "score"  # everyone Prêt
    await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "game-1", now=_T3H)
    assert phase_label(await _get(bundle, attack.id)) == "score"  # finished


async def test_declare_score_refuses_a_player_without_pret() -> None:
    """attaques-phases-v2 FIX: no result declaration without « Prêt »."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    with pytest.raises(ScoreNotReadyError):
        await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "game-1", now=_T)
    # Also an AttackStateError (the shared state-machine family).
    with pytest.raises(AttackStateError):
        await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "game-1", now=_T)
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.state is AttackState.MEETING  # nothing applied, no short-circuit
    # After the Prêt click the declaration goes through (fait-foi intact).
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    attack, outcome = await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "game-1", now=_T)
    assert outcome == "resolved"


async def test_early_close_opens_score_as_soon_as_everyone_is_ready() -> None:
    """Everyone Prêt → the Score phase opens at once; T+5 stays the forfeit line."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    attack = await _get(bundle, attack.id)
    assert expected_score_players(attack) == ["king-a", "lord-b"]
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert not fresh.score.get("all_ready")  # one Prêt is not everyone
    await bundle.attacks.mark_ready(attack.id, "lord-b", now=_T)
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.score["all_ready"] == int(_T.timestamp())
    assert phase_label(fresh) == "score"
    # The T+5 line never forfeits a meeting where everyone showed up.
    handled = await bundle.attacks.ready_due(now=_T5)
    assert handled == []
    still = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert still.state is AttackState.MEETING


async def test_vs_ai_all_ready_needs_only_the_attacker() -> None:
    """Vs AI the attacker alone completes the RDV — his Prêt is the presence proof."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    fresh.ai_defender = True
    fresh.defender_lord_id = None
    await bundle.store.upsert_attack(fresh.to_mongo())
    await bundle.attacks.score_due(now=_T)
    assert expected_score_players(fresh) == ["king-a"]
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    refreshed = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert refreshed.score.get("all_ready") == int(_T.timestamp())
    assert phase_label(refreshed) == "score"


async def test_score_reminder_due_at_three_hours_without_a_result() -> None:
    """The 3h reminder: no result → both players flagged once; result → never."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    # Before the 3h line: nothing.
    assert await bundle.attacks.score_reminder_due(now=_BEFORE3H) == []
    # At the 3h line: the meeting is flagged (the DM leaves once).
    due = await bundle.attacks.score_reminder_due(now=_T3H)
    assert [item.id for item in due] == [attack.id]
    assert due[0].score.get("reminded") is True
    # Idempotent: the tick replays without a second DM.
    assert await bundle.attacks.score_reminder_due(now=_T3H + timedelta(minutes=5)) == []
    # A declared result ends the meeting — the reminder never fires again
    # (a resolved attack left the MEETING state; the gate Prêt holds too).
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "game-9", now=_T3H)
    assert await bundle.attacks.score_reminder_due(now=_T3H + timedelta(hours=3)) == []


async def test_score_due_records_the_opened_moment_for_the_reminder() -> None:
    """score_due stamps ``opened_at`` — the 3h reminder base (Mongo-proof epoch)."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    await bundle.attacks.score_due(now=_T)
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.score["opened_at"] == int(_T.timestamp())
    assert isinstance(fresh.score["opened_at"], int)
    assert datetime.fromtimestamp(fresh.score["opened_at"], tz=UTC) == _T


async def test_auto_dispute_opens_fifteen_hours_after_the_opening() -> None:
    """bug n°2 (Check 59): everyone Prêt, nobody declares → the dispute
    auto-opens 15h after the score phase (3h reminder + 12h, Drasah 16/10)."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    await bundle.attacks.mark_ready(attack.id, "lord-b", now=_T)
    # Before the 15h line: nothing, even long after the reminder.
    assert await bundle.attacks.dispute_due(now=_BEFORE15H) == []
    # At the 15h line: the dispute auto-opens (the admins arbitrate).
    due = await bundle.attacks.dispute_due(now=_T15H)
    assert [item.id for item in due] == [attack.id]
    fresh = await _get(bundle, attack.id)
    assert fresh.score.get("disputed") is True
    assert fresh.score.get("auto_dispute") is True
    assert fresh.state is AttackState.MEETING  # still awaiting arbitration
    # Idempotent: the tick replays without re-flagging.
    assert await bundle.attacks.dispute_due(now=_T15H + timedelta(minutes=5)) == []


async def test_auto_dispute_leaves_a_declared_result_alone() -> None:
    """A declared result resolves the meeting — no dispute ever fires."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    await bundle.attacks.mark_ready(attack.id, "lord-b", now=_T)
    await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "game-7", now=_T3H)
    assert await bundle.attacks.dispute_due(now=_T15H) == []
    fresh = await _get(bundle, attack.id)
    assert fresh.state is AttackState.RESOLVED
    assert fresh.score.get("disputed") is None


async def test_auto_dispute_ignores_meetings_without_everyone_ready() -> None:
    """A meeting that never reached all-Prêt is forfeited by ready_due,
    not by the 15h line — only the all-Prêt meetings can hang."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    # Only the attacker clicked Prêt; the defender no-showed at T+5.
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    handled = await bundle.attacks.ready_due(now=_T5)
    assert [item.id for item in handled] == [attack.id]
    assert await bundle.attacks.dispute_due(now=_T15H) == []


async def test_auto_dispute_is_arbitrable_by_the_admin_path() -> None:
    """The auto-opened dispute goes through the production resolve_dispute."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    await bundle.attacks.mark_ready(attack.id, "lord-b", now=_T)
    due = await bundle.attacks.dispute_due(now=_T15H)
    assert [item.id for item in due] == [attack.id]
    resolved = await bundle.attacks.resolve_dispute(attack.id, "both", now=_T15H)
    assert resolved.state is AttackState.EXPIRED
    assert resolved.winner_kingdom_id is None
    fresh = await _get(bundle, attack.id)
    assert fresh.score.get("disputed") is False  # cleared by the arbitration
