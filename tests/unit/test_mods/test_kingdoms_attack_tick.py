"""Attack-phases-tick-v1 - unit tests.

Drasah 22/10: « fais ce qu'il faut pour respecter les timers » — the
scheduler loop sleeps AT the next attack-phase deadline instead of the
stock 300 s heartbeat. Two halves: the service probe
(``next_phase_deadline`` — the nearest upcoming player-facing deadline
across the live attacks) and the loop clamp (``_loop_sleep_seconds``
— sleep exactly the gap, bounded by the stock cadence).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kingdoms.mods.kingdoms import kingdom_events_loop as events_loop
from kingdoms.mods.kingdoms.kingdom_events_loop import _loop_sleep_seconds
from kingdoms.mods.kingdoms.models import AttackState

from .test_kingdoms_attacks import Bundle

_T = datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
"""The meeting time of an attack declared at 12:00 (cutoff 17:45)."""


class _PhaseBundle(Bundle):
    """The attack bundle with a declared attack (T = 18:00 UTC)."""

    async def declared(self) -> Any:
        bourgogne = await self.kingdom_id("Bourgogne")
        target = (await self.owner_maps(bourgogne))[0]
        return await self.attacks.declare_attack(
            "king-a", target, "aoe2de://lobby", now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        )


async def _launch(bundle: _PhaseBundle) -> None:
    await bundle.launch_season()
    await bundle.draw(2)


async def test_declared_attack_deadlines_are_the_cutoff_then_the_meeting() -> None:
    """A DECLARED attack tracks the T-15 reveal, then T itself."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    attack = await bundle.declared()
    assert attack.state is AttackState.DECLARED
    # At the declaration: the next deadline is the 17:45 cutoff.
    assert await bundle.attacks.next_phase_deadline(now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC)) == (
        _T - timedelta(minutes=15)
    )
    # Past the cutoff (not revealed yet): the meeting time T.
    assert await bundle.attacks.next_phase_deadline(now=datetime(2026, 10, 11, 17, 50, tzinfo=UTC)) == _T


async def test_no_live_deadline_returns_none() -> None:
    """No attack (or only over ones) — nothing to wake up for."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    # A board with no attack at all.
    assert await bundle.attacks.next_phase_deadline(now=_T) is None
    # An over attack carries no upcoming deadline either.
    attack = await bundle.declared()
    stored = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    stored.state = AttackState.RESOLVED
    stored.resolved_at = _T
    await bundle.store.upsert_attack(stored.to_mongo())
    assert await bundle.attacks.next_phase_deadline(now=_T) is None


async def test_ai_engaged_at_the_cutoff_tracks_validate_then_the_default() -> None:
    """real-timeline-v1 (Drasah 23/10): the AI engages AT the T-15
    cutoff — the probe tracks the AI's ``ai_validate_at`` think time,
    then the anti-blocking default at T (no post-T grace anymore)."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    await bundle.declared()
    cutoff = _T - timedelta(minutes=15)
    # Nobody defended: the AI takes over at the cutoff.
    await bundle.attacks.reveal_due(now=cutoff)
    flipped = await bundle.attacks.pick_ban_due(now=cutoff)
    assert len(flipped) == 1
    validate_at = float(flipped[0].pickban["ai_validate_at"])
    probe = cutoff + timedelta(seconds=5)
    assert validate_at > probe.timestamp()
    assert await bundle.attacks.next_phase_deadline(now=probe) == datetime.fromtimestamp(validate_at, tz=UTC)
    # Past the AI's validation (draft still unresolved): the T default.
    grace = datetime.fromtimestamp(validate_at, tz=UTC) + timedelta(seconds=1)
    assert await bundle.attacks.next_phase_deadline(now=grace) == _T


async def test_meeting_tracks_the_no_show_line_and_the_ai_ready() -> None:
    """A MEETING tracks the AI's ``ai_ready_at`` and the T+5 no-show line."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    attack = await bundle.declared()
    # Force the resolved pick & ban, then open the room at T.
    stored = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    stored.state = AttackState.DEFENDED
    stored.ai_defender = True  # vs the bot AI: mark_ready arms ``ai_ready_at``.
    stored.pickban = {
        "sent": True,
        "attacker_validated": True,
        "defender_validated": True,
        "resolved_pickban": True,
    }
    await bundle.store.upsert_attack(stored.to_mongo())
    opened = await bundle.attacks.score_due(now=_T)
    assert len(opened) == 1
    meeting = opened[0]
    assert meeting.state is AttackState.MEETING
    assert meeting.score["ready_deadline"] == int((_T + timedelta(minutes=5)).timestamp())
    # The nearest deadline: the T+5 no-show line (no AI ready armed yet).
    assert await bundle.attacks.next_phase_deadline(now=_T) == _T + timedelta(minutes=5)
    # The attacker clicks Prêt: the AI's armed ``ai_ready_at`` takes over.
    ready = await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    ai_ready_at = float(ready.score["ai_ready_at"])
    assert await bundle.attacks.next_phase_deadline(
        now=datetime.fromtimestamp(ai_ready_at, tz=UTC) - timedelta(seconds=10)
    ) == datetime.fromtimestamp(ai_ready_at, tz=UTC)


# ---------------------------------------------------------------------------
# The loop clamp
# ---------------------------------------------------------------------------


class _FakeSeason:
    beta_name = None
    beta_start_at = None
    frozen = False
    phase = "started"


class _FakeKingdoms:
    def __init__(self) -> None:
        self._season: Any = _FakeSeason()
        self.config: Any = None

    async def current_season(self) -> Any:
        return self._season


class _FakeAttacks:
    def __init__(self, deadline: datetime | None, error: bool = False) -> None:
        self._deadline = deadline
        self._error = error

    async def next_phase_deadline(self, *, now: datetime | None = None) -> datetime | None:
        if self._error:
            raise RuntimeError("probe exploded")
        return self._deadline


class _FakeBot:
    def __init__(self, attacks: _FakeAttacks | None) -> None:
        self.kingdoms_service = _FakeKingdoms()
        self.kingdoms_attacks_service = attacks


_FROZEN_NOW = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)
"""The probe clock — the loop's ``game_now`` is pinned so the gap is
deterministic (a real clock makes the expected seconds a race)."""


def _in(seconds: float) -> datetime:
    return _FROZEN_NOW + timedelta(seconds=seconds)


@pytest.fixture()
def _pinned_loop_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events_loop, "game_now", lambda: _FROZEN_NOW)


async def test_no_deadline_or_no_service_keeps_the_stock_cadence() -> None:
    assert await _loop_sleep_seconds(_FakeBot(None), 300) == 300
    assert await _loop_sleep_seconds(_FakeBot(_FakeAttacks(None)), 300) == 300


@pytest.mark.usefixtures("_pinned_loop_clock")
async def test_the_loop_sleeps_exactly_the_gap_to_the_deadline() -> None:
    bot = _FakeBot(_FakeAttacks(_in(30)))
    assert await _loop_sleep_seconds(bot, 300) == 32
    # The cadence stays the upper bound — a far deadline never shortens it.
    far = _FakeBot(_FakeAttacks(_in(900)))
    assert await _loop_sleep_seconds(far, 300) == 300
    # A shorter default (the beta 60 s) caps the same way.
    assert await _loop_sleep_seconds(bot, 60) == 32
    assert await _loop_sleep_seconds(far, 60) == 60


@pytest.mark.usefixtures("_pinned_loop_clock")
async def test_a_deadline_at_the_door_runs_now_and_the_floor_bounds_the_sleep() -> None:
    overdue = _FakeBot(_FakeAttacks(_in(-10)))
    assert await _loop_sleep_seconds(overdue, 300) == 1
    imminent = _FakeBot(_FakeAttacks(_in(2)))
    assert await _loop_sleep_seconds(imminent, 300) == 5


async def test_a_probe_failure_keeps_the_stock_cadence() -> None:
    bot = _FakeBot(_FakeAttacks(_in(30), error=True))
    assert await _loop_sleep_seconds(bot, 300) == 300


# ---------------------------------------------------------------------------
# attack-phases-tick-v2 — the ACTION-triggered steps are urgent
# ---------------------------------------------------------------------------


async def test_the_cutoff_ai_engagement_is_urgent_the_draft_dm_leaves_now() -> None:
    """v2 + real-timeline-v1: at the T-15 cutoff the AI engagement
    leaves the DEFENDED+unsent draft DUE NOW — the probe returns
    ``now`` so the DM leaves within the second instead of waiting the
    base tick (the live beta report)."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    await bundle.declared()
    cutoff = _T - timedelta(minutes=15)
    # Nobody defended: the AI engages at the cutoff — the draft DM is
    # now due and carries no clock of its own.
    await bundle.attacks.reveal_due(now=cutoff)
    assert await bundle.attacks.next_phase_deadline(now=cutoff) == cutoff
    # One tick clears it: the DMs are sent, the AI's validate takes over.
    await bundle.attacks.pick_ban_due(now=cutoff)
    probe = cutoff + timedelta(seconds=5)
    assert await bundle.attacks.next_phase_deadline(now=probe) != probe


async def test_the_attacker_validation_is_urgent_the_reveal_resolves_now() -> None:
    """v2: once BOTH sides validated (the AI first, the attacker after)
    the unresolved pick & ban is DUE NOW — the resolution and the
    reveal DMs leave within the second."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    attack = await bundle.declared()
    cutoff = _T - timedelta(minutes=15)
    await bundle.attacks.reveal_due(now=cutoff)
    await bundle.attacks.pick_ban_due(now=cutoff)
    # The AI validates its 3 random picks at ``ai_validate_at``.
    flipped = (await bundle.attacks.ai_draft_due(now=cutoff + timedelta(minutes=2)))[0]
    assert flipped.pickban.get("defender_validated")
    assert not flipped.pickban.get("resolved_pickban")
    # Not urgent while the attacker has not ranked his civs: the probe
    # keeps tracking the T anti-blocking default.
    assert await bundle.attacks.next_phase_deadline(now=cutoff + timedelta(minutes=2)) == _T
    # The attacker validates — the resolution is now due NOW.
    validated = await bundle.attacks.validate_pickban(
        attack.id, "king-a", now=cutoff + timedelta(minutes=2)
    )
    assert validated.pickban.get("resolved_pickban")
    # Pre-T the resolution is NOT urgent — the room opens at T.
    assert await bundle.attacks.next_phase_deadline(now=cutoff + timedelta(minutes=2)) == _T


async def test_a_resolved_draft_past_t_is_urgent_the_room_opens_now() -> None:
    """v2: a pick & ban resolved past T with the room still closed is
    DUE NOW — the vocal opens within the second."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    attack = await bundle.declared()
    stored = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    stored.state = AttackState.DEFENDED
    stored.revealed = True
    stored.pickban = {
        "sent": True,
        "attacker_validated": True,
        "defender_validated": True,
        "resolved_pickban": True,
    }
    await bundle.store.upsert_attack(stored.to_mongo())
    past_t = _T + timedelta(minutes=1)
    assert await bundle.attacks.next_phase_deadline(now=past_t) == past_t
    # Pre-T the same attack is NOT urgent — the room opens at T.
    assert await bundle.attacks.next_phase_deadline(now=_T - timedelta(minutes=1)) == _T


async def test_a_pre_reveal_human_defense_is_never_urgent() -> None:
    """v2: a DEFENDED human attack before the reveal keeps its cutoff
    deadline — its draft DMs leave at the reveal, not now."""
    bundle = _PhaseBundle()
    await _launch(bundle)
    attack = await bundle.declared()
    defended = await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))
    assert defended.state is AttackState.DEFENDED
    probe = datetime(2026, 10, 11, 13, 0, tzinfo=UTC)
    assert await bundle.attacks.next_phase_deadline(now=probe) == _T - timedelta(minutes=15)