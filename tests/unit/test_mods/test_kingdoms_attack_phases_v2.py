"""Kingdoms attack phase taxonomy + presence line (attaques-phases-v2) - unit tests.

The player-facing phases (Siège → Draft → RDV/Ready → Score) behind the
engine's internal T-15/T-5/T/T+5 clock, the forfeit line riding the
REAL voice presence (Drasah 18/10 v2 — the Prêt click stays a plain UI
confirmation), the early close of the Ready phase (everyone Prêt opens
the Score phase right away — T+5 stays the forfeit deadline only) and
the 3h no-result reminder line.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import (
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
_FROZEN_NOW = datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
"""Frozen at 17:50 (past the T-15 cutoff, fresh attack T = 18:00) — the
score-phase clock: the defense itself lands pre-cutoff (new window)."""


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
    # Drasah 18/10: the defense window is [declaration, T-15] — the
    # defense lands right after the declaration, not at the end.
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
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


async def test_declare_score_rides_the_room_not_the_pret_gate() -> None:
    """Drasah 18/10 v2: no Prêt gate — a lone declaration pends, then
    FAIT FOI after ``score_confirm_minutes`` unless contested."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    # No Prêt click at all — the declaration is still accepted.
    attack, outcome = await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "game-1", now=_T)
    assert outcome == "pending"
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.state is AttackState.MEETING  # waiting for the confirm window
    # Inside the window: still pending, nothing applied.
    assert await bundle.attacks.score_confirm_due(now=_T + timedelta(minutes=9)) == []
    # Past the 10-minute window: the lone result FAIT FOI.
    confirmed = await bundle.attacks.score_confirm_due(now=_T + timedelta(minutes=10))
    assert [item.id for item in confirmed] == [attack.id]
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.state is AttackState.RESOLVED
    assert fresh.winner_kingdom_id == aquitaine


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
    # The T+5 line never forfeits a meeting where everyone showed up
    # (the voice presence, Drasah 18/10 v2).
    await bundle.present(attack, "king-a", "lord-b")
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
    # battle-ai-rdv-v1: vs the AI the attacker's Prêt arms the bot's
    # OWN 30-90 s confirmation — all_ready lands on the tick, not the
    # click (the timing never leaks the bot).
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    refreshed = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert not refreshed.score.get("all_ready")
    assert 30 <= refreshed.score["ai_ready_at"] - int(_T.timestamp()) <= 90
    confirmed = await bundle.attacks.ai_ready_due(now=_T + timedelta(seconds=90))
    assert [item.id for item in confirmed] == [attack.id]
    refreshed = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert refreshed.score.get("all_ready") == int((_T + timedelta(seconds=90)).timestamp())
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
    # A declared result freezes the reminder — the meeting now waits
    # for the confirm window, not for a DM (Drasah 18/10 v2).
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
    # The lone declaration pends, then FAIT FOI after the confirm window.
    confirmed = await bundle.attacks.score_confirm_due(
        now=_T3H + timedelta(minutes=10)
    )
    assert [item.id for item in confirmed] == [attack.id]
    assert await bundle.attacks.dispute_due(now=_T15H) == []
    fresh = await _get(bundle, attack.id)
    assert fresh.state is AttackState.RESOLVED
    assert fresh.score.get("disputed") is None


async def test_auto_dispute_ignores_meetings_without_a_declaration() -> None:
    """A meeting whose players never declared anything is settled by the
    presence lines (ready_due) or the 15h dispute — never both; a
    DECLARATION-less meeting is the only one the 15h line can hang."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    # The attacker showed up in the battle room; the defender no-showed
    # at T+5 — the voice presence line settles it, not the 15h dispute.
    await bundle.present(attack, "king-a")
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
