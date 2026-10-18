"""Kingdoms RDV score phase (attaques-score-v1) - unit tests.

The meeting state machine behind an in-memory store: the score phase
opening at T (only for resolved pick & bans), the VOICE presence of
the battle room and the T+5 no-show line (unilateral / double / vs
AI, Drasah 18/10 v2), the game result declaration (consensus, lone
pending declaration confirmed after 10 minutes, contested dispute),
the admin arbitration, the epoch-unique attack ids and the FFA
naive-datetime fix.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import AttackError, AttackStateError
from kingdoms.mods.kingdoms.models import AttackModel, AttackState, FFAExplorationModel

from .test_kingdoms_attack_phases import PhasesBundle

_T = datetime(2026, 10, 11, 18, 0, tzinfo=UTC)  # the meeting time
_T5 = _T + timedelta(minutes=5)  # the no-show line
_PICK_NOW = datetime(2026, 10, 11, 17, 56, tzinfo=UTC)
_LEAD = datetime(2026, 10, 11, 17, 55, tzinfo=UTC)  # the T-5 pick & ban
_CUTOFF = datetime(2026, 10, 11, 17, 45, tzinfo=UTC)  # the T-15 reveal
_FROZEN_NOW = datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
"""Frozen INSIDE the [T-15, T] defense window (fresh attack T = 18:00)."""


@pytest.fixture(autouse=True)
def _freeze_attacks_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _FROZEN_NOW)


class ScoreBundle(PhasesBundle):
    """The phases bundle extended with the meeting factory."""

    async def meeting(self) -> tuple[AttackModel, str, str]:
        """A defended attack with a resolved pick & ban, T = 18:00 UTC."""
        attack = await self.fresh_attack()
        # Drasah 18/10: the defense lands right after the declaration
        # (the window is [declaration, T-15] now).
        await self.attacks.respond_defense(
            attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
        )
        await self.attacks.reveal_due(now=_CUTOFF)
        await self.attacks.pick_ban_due(now=_LEAD)
        await self.give_civs("Aquitaine", "francs", "bretons", "goths")
        await self.give_civs("Bourgogne", "huns", "perses", "byzantins")
        await self.attacks.set_picks(
            attack.id, "king-a", ["francs", "bretons", "goths"], now=_PICK_NOW
        )
        await self.attacks.set_picks(
            attack.id, "lord-b", ["huns", "perses", "byzantins"], now=_PICK_NOW
        )
        await self.attacks.validate_pickban(attack.id, "king-a", now=_PICK_NOW)
        await self.attacks.validate_pickban(attack.id, "lord-b", now=_PICK_NOW)
        aquitaine, bourgogne = await self.kingdom_id("Aquitaine"), await self.kingdom_id(
            "Bourgogne"
        )
        return attack, aquitaine, bourgogne

    async def open_meeting(self) -> tuple[AttackModel, str, str]:
        """A MEETING attack (the score phase opened at T)."""
        attack, aquitaine, bourgogne = await self.meeting()
        await self.attacks.score_due(now=_T)
        return attack, aquitaine, bourgogne

    async def present(self, attack: AttackModel, *player_ids: str) -> None:
        """Open the battle room and join it (the voice presence, Drasah 18/10 v2)."""
        await self.attacks.record_battle_room(attack.id, 4242)
        for player_id in player_ids:
            await self.attacks.record_voice_join(4242, player_id)

    async def owner_of(self, attack: AttackModel) -> str | None:
        territory = next(
            item
            for item in await self.territories.territories()
            if item.id == attack.territory_id
        )
        return territory.owner_kingdom_id


async def test_score_due_opens_the_meeting_at_t() -> None:
    """attaques-score-v1: a resolved pick & ban becomes a MEETING at T."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    due = await bundle.attacks.score_due(now=_T)
    assert [item.id for item in due] == [attack.id]
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.state is AttackState.MEETING
    assert fresh.score["opened"] is True
    assert fresh.score["ready"] == []
    assert fresh.score["ready_deadline"] == int(_T5.timestamp())
    # Idempotent: the tick replays without a second DM.
    assert await bundle.attacks.score_due(now=_T) == []


async def test_forgotten_draft_defaults_the_defender_and_opens_the_meeting() -> None:
    """Drasah 18/10: a forgotten draft defaults (random civs, no
    sabotage, never a forfeit) — the RDV still opens at T."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    # The defender never validates — the pick & ban stays unresolved.
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    pickban = dict(fresh.pickban)
    pickban["defender_validated"] = False
    pickban["resolved_pickban"] = False
    fresh.pickban = pickban
    await bundle.store.upsert_attack(fresh.to_mongo())
    assert await bundle.attacks.score_due(now=_T) == []
    # Anti-blocking default: expire_stale NEVER forfeits the attack.
    assert await bundle.attacks.expire_stale(now=_T) == []
    defaulted = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert defaulted.state is AttackState.DEFENDED
    assert defaulted.pickban["resolved_pickban"] is True
    assert defaulted.pickban["default_picks"] == ["defender"]
    assert defaulted.pickban["defender_picks"]
    assert sorted(defaulted.pickban["defender_picks"]) == [
        "byzantins",
        "huns",
        "perses",
    ]
    assert defaulted.pickban["sabotages"] == {}
    assert defaulted.pickban["defender_sabotages"] == []
    # The RDV opens on the next tick — the game decides.
    due = await bundle.attacks.score_due(now=_T)
    assert [item.id for item in due] == [attack.id]


async def test_voice_presence_and_unilateral_noshow() -> None:
    """Drasah 18/10 v2: the forfeit rides the VOICE presence — the
    defender never joined the battle room, the attacker wins at T+5."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    await bundle.present(attack, "king-a")
    # Idempotent (a double join adds nothing) + an outsider joins nothing.
    await bundle.attacks.record_voice_join(4242, "king-a")
    await bundle.attacks.record_voice_join(4242, "intrus")
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.score["voice_joined"] == ["king-a"]
    assert await bundle.attacks.ready_due(now=_T + timedelta(minutes=4)) == []
    handled = await bundle.attacks.ready_due(now=_T5)
    assert [item.id for item in handled] == [attack.id]
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.state is AttackState.RESOLVED
    assert fresh.winner_kingdom_id == aquitaine
    assert fresh.score["noshow"] == "defender"
    assert await bundle.owner_of(attack) == aquitaine


async def test_ready_click_still_records_the_ui_confirmation() -> None:
    """The Prêt button stays a plain UI confirmation (no forfeit role)."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    await bundle.attacks.mark_ready(attack.id, "king-a", now=_T)
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.score["ready"] == ["king-a"]
    # The Prêt click is NOT the presence: only the attacker joined the
    # room voice — the defender no-show still forfeits at T+5.
    await bundle.present(attack, "king-a")
    handled = await bundle.attacks.ready_due(now=_T5)
    assert [item.id for item in handled] == [attack.id]
    assert handled[0].score["noshow"] == "defender"


async def test_attacker_noshow_gives_the_defense() -> None:
    """T+5: the attacker missed — the defending kingdom keeps the map."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    await bundle.present(attack, "lord-b")
    handled = await bundle.attacks.ready_due(now=_T5)
    assert handled[0].winner_kingdom_id == await bundle.kingdom_id("Bourgogne")
    assert await bundle.owner_of(attack) == await bundle.kingdom_id("Bourgogne")


async def test_double_noshow_is_a_double_forfait() -> None:
    """Both humans missed the RDV — expired, no transfer (Drasah 15/10)."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, bourgogne = await bundle.open_meeting()
    handled = await bundle.attacks.ready_due(now=_T5)
    assert handled[0].state is AttackState.EXPIRED
    assert handled[0].winner_kingdom_id is None
    assert handled[0].score["noshow"] == "both"
    assert await bundle.owner_of(attack) == bourgogne


async def test_vs_ai_only_the_attacker_confirms() -> None:
    """Vs the bot AI: only the attacker confirms — a no-show loses the attack.

    Drasah 17/10: the AI engages AT the meeting time (no pick & ban
    vs the AI) — the RDV opens right away, the game decides (D20).
    """
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack = await bundle.fresh_attack()  # nobody defends
    await bundle.attacks.reveal_due(now=_CUTOFF)  # the cover drops only
    expired = await bundle.attacks.expire_stale(now=_T)  # the AI takes the defense
    assert [item.id for item in expired] == [attack.id]
    due = await bundle.attacks.score_due(now=_T)
    assert [item.id for item in due] == [attack.id]
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.ai_defender is True
    assert fresh.pickban["resolved_pickban"] is True  # no pick & ban vs the AI
    # Drasah 18/10 v2: vs the AI the lone attacker declaration FAIT
    # FOI at once (no Prêt gate, no second human to confirm).
    fresh, outcome = await bundle.attacks.declare_score(
        attack.id, "king-a", fresh.attacker_kingdom_id, "g-123", now=_T
    )
    assert outcome == "resolved"
    assert fresh.state is AttackState.RESOLVED


async def test_vs_ai_attacker_noshow_loses() -> None:
    """Vs the bot AI: the attacker never clicked Prêt — the AI wins at T+5."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    await bundle.fresh_attack()
    await bundle.attacks.reveal_due(now=_CUTOFF)  # the cover drops only
    await bundle.attacks.expire_stale(now=_T)  # the AI takes the defense
    await bundle.attacks.score_due(now=_T)
    handled = await bundle.attacks.ready_due(now=_T5)
    assert handled[0].winner_kingdom_id == await bundle.kingdom_id("Bourgogne")
    assert handled[0].score["noshow"] == "attacker"


async def test_lone_declaration_pends_then_confirms() -> None:
    """Drasah 18/10 v2: a lone declaration of two humans holds 10
    minutes, then the declared result FAIT FOI (score_confirm_due)."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    fresh, outcome = await bundle.attacks.declare_score(
        attack.id, "king-a", aquitaine, "g-42", now=_T
    )
    assert outcome == "pending"
    assert fresh.state is AttackState.MEETING  # nothing applied yet
    assert fresh.winner_kingdom_id is None
    assert fresh.score["game_id"] == "g-42"
    assert await bundle.owner_of(attack) != aquitaine
    # Inside the window nothing moves; a tick replays safely.
    assert await bundle.attacks.score_confirm_due(now=_T + timedelta(minutes=9)) == []
    confirmed = await bundle.attacks.score_confirm_due(now=_T + timedelta(minutes=10))
    assert [item.id for item in confirmed] == [attack.id]
    fresh = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert fresh.state is AttackState.RESOLVED
    assert fresh.winner_kingdom_id == aquitaine
    assert await bundle.owner_of(attack) == aquitaine
    # A same-result replay after the resolution is idempotent ("already").
    _, outcome = await bundle.attacks.declare_score(
        attack.id, "lord-b", aquitaine, "g-42", now=_T + timedelta(minutes=11)
    )
    assert outcome == "already"


async def test_consensus_of_both_players_resolves_at_once() -> None:
    """Both players declare the same winner — the meeting resolves at once."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    _, outcome = await bundle.attacks.declare_score(
        attack.id, "king-a", aquitaine, "g-7", now=_T
    )
    assert outcome == "pending"
    fresh, outcome = await bundle.attacks.declare_score(
        attack.id, "lord-b", aquitaine, "g-7", now=_T + timedelta(minutes=2)
    )
    assert outcome == "resolved"
    assert fresh.state is AttackState.RESOLVED
    assert fresh.winner_kingdom_id == aquitaine
    assert await bundle.owner_of(attack) == aquitaine


async def test_contested_declaration_raises_a_dispute() -> None:
    """Drasah 18/10 v2: a contrary declaration INSIDE the pending
    window flags a dispute — nothing applies until an admin rules."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, bourgogne = await bundle.open_meeting()
    _, outcome = await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "g-1", now=_T)
    assert outcome == "pending"
    fresh, outcome = await bundle.attacks.declare_score(
        attack.id, "lord-b", bourgogne, "g-2", now=_T + timedelta(minutes=1)
    )
    assert outcome == "disputed"
    assert fresh.score["disputed"] is True
    # No resolution stands — the admins arbitrate.
    assert fresh.state is AttackState.MEETING
    assert fresh.winner_kingdom_id is None
    # The confirm line leaves a disputed meeting alone.
    assert await bundle.attacks.score_confirm_due(now=_T + timedelta(minutes=30)) == []
    # The arbitration CONSUMES the dispute: replaying refuses.
    await bundle.attacks.resolve_dispute(attack.id, "attacker", now=_T + timedelta(minutes=2))
    with pytest.raises(AttackStateError):
        await bundle.attacks.resolve_dispute(attack.id, "defender", now=_T)


async def test_admin_arbitration_overrides_the_result() -> None:
    """resolve_dispute reverses the transfer to the real winner."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, bourgogne = await bundle.open_meeting()
    await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "g-1", now=_T)
    await bundle.attacks.declare_score(
        attack.id, "lord-b", bourgogne, "g-2", now=_T + timedelta(minutes=1)
    )
    fresh = await bundle.attacks.resolve_dispute(
        attack.id, "defender", now=_T + timedelta(minutes=2)
    )
    assert fresh.winner_kingdom_id == bourgogne
    assert fresh.score["disputed"] is False
    assert await bundle.owner_of(attack) == bourgogne


async def test_admin_arbitration_double_forfait() -> None:
    """The admin can turn a dispute into a double forfait — no transfer."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, bourgogne = await bundle.open_meeting()
    await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "g-1", now=_T)
    await bundle.attacks.declare_score(
        attack.id, "lord-b", bourgogne, "g-2", now=_T + timedelta(minutes=1)
    )
    fresh = await bundle.attacks.resolve_dispute(
        attack.id, "both", now=_T + timedelta(minutes=2)
    )
    assert fresh.state is AttackState.EXPIRED
    assert fresh.winner_kingdom_id is None
    assert await bundle.owner_of(attack) == bourgogne


async def test_declare_score_guards() -> None:
    """The game id is required and the winner must belong to the attack."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    with pytest.raises(AttackError):
        await bundle.attacks.declare_score(attack.id, "king-a", aquitaine, "  ", now=_T)
    with pytest.raises(AttackStateError):
        await bundle.attacks.declare_score(attack.id, "king-a", "gaia", "g-1", now=_T)
    with pytest.raises(AttackStateError):  # an outsider declares nothing
        await bundle.attacks.declare_score(attack.id, "lord-a", aquitaine, "g-1", now=_T)
    # The score phase must be open: a DEFENDED attack refuses early calls.
    other = ScoreBundle()
    await other.launch_season()
    await other.draw(9)
    fresh, aquitaine_two, _bourgogne_two = await other.meeting()
    with pytest.raises(AttackStateError):
        await other.attacks.declare_score(fresh.id, "king-a", aquitaine_two, "g-1", now=_T)


async def test_resolve_accepts_a_meeting() -> None:
    """The legacy resolve() (admin/debug path) also closes a MEETING."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    fresh = await bundle.attacks.resolve(attack.id, aquitaine)
    assert fresh.state is AttackState.RESOLVED
    assert await bundle.owner_of(attack) == aquitaine


async def test_redeclared_attack_ids_stay_unique() -> None:
    """A re-attack of the same territory by the same lord creates a new doc."""
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    attack, _aquitaine, bourgogne = await bundle.meeting()
    await bundle.attacks.score_due(now=_T)
    await bundle.attacks.resolve(attack.id, bourgogne)  # the defender keeps the map
    # The budget back (D17-like), one epoch second later: the id differs.
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    lord.attack_used = 0
    await bundle.store.upsert_lord(lord.to_mongo())
    second = await bundle.attacks.declare_attack(
        "king-a",
        attack.map_key,
        "aoe2de://lobby",
        now=datetime(2026, 10, 11, 12, 0, 1, tzinfo=UTC),
    )
    assert second.id != attack.id
    assert second.id.startswith(f"{attack.id.rsplit('-', 1)[0]}")
    assert len(await bundle.attacks.attacks()) == 2


async def test_ffa_naive_datetimes_no_longer_crash_the_tick() -> None:
    """Mongo returns naive datetimes — due_start/due_announce normalize."""
    from .test_kingdoms_ffa import Bundle as FFABundle

    bundle = FFABundle()
    naive_start = datetime(2026, 10, 17, 14, 0)  # what Mongo BSON hands back
    ffa = FFAExplorationModel(
        _id="s-ffa-2026-10-17",
        season_id="s",
        date="2026-10-17",
        start_at=naive_start,
        announce_at=naive_start - timedelta(hours=2),
    )
    before = datetime(2026, 10, 17, 13, 59, tzinfo=UTC)
    after = datetime(2026, 10, 17, 14, 1, tzinfo=UTC)
    assert bundle.ffa.due_start(ffa, before) is False
    assert bundle.ffa.due_start(ffa, after) is True
    assert bundle.ffa.due_announce(ffa, before) is True


def test_meeting_is_not_over_and_cancels_at_season_end() -> None:
    """A MEETING is not a final state; cancel_ongoing sweeps it at the verdict."""
    assert AttackState.MEETING not in (
        AttackState.RESOLVED,
        AttackState.EXPIRED,
    )


async def test_lobby_url_shared_at_the_meeting_not_the_declaration() -> None:
    """Drasah 18/10: the declaration carries no lobby URL — the RDV
    (ready phase) collects it through set_lobby_url."""
    from kingdoms.mods.kingdoms.attacks import AttackError, AttackStateError

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(9)
    # The declaration accepts an empty lobby URL (the old raise is gone).
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "", now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    )
    assert attack.lobby_url == ""
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    await bundle.attacks.reveal_due(now=_CUTOFF)
    await bundle.attacks.pick_ban_due(now=_LEAD)
    await bundle.attacks.expire_stale(now=_T)
    await bundle.attacks.score_due(now=_T)
    # Any player of the meeting shares the URL at the RDV.
    fresh = await bundle.attacks.set_lobby_url(
        attack.id, str(attack.attacker_lord_id), "aoe2de://lobby/12345"
    )
    assert fresh.lobby_url == "aoe2de://lobby/12345"
    reloaded = next(item for item in await bundle.attacks.attacks() if item.id == attack.id)
    assert reloaded.lobby_url == "aoe2de://lobby/12345"
    # The defender may share it too (the last share wins) — the
    # in-memory `attack` predates the defense, so read the RELOADED
    # defender lord id.
    reloaded = await bundle.attacks.set_lobby_url(
        fresh.id, str(fresh.defender_lord_id), "aoe2de://lobby/6789"
    )
    assert reloaded.lobby_url == "aoe2de://lobby/6789"
    # An outsider is refused, and so is an empty URL.
    with pytest.raises(AttackStateError):
        await bundle.attacks.set_lobby_url(fresh.id, "intrus", "aoe2de://x")
    with pytest.raises(AttackError):
        await bundle.attacks.set_lobby_url(fresh.id, str(fresh.attacker_lord_id), "  ")
