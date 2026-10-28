"""Kingdoms mod attack panel Discord surface - unit tests (D77).

The pinned Attaquer panel, the persistent declare/defend buttons, the
declaration modal and the scheduler entry (reveal announcement, pick
& ban DM dispatch, forfeit announcements) behind the in-memory store.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockTextChannel, MockUser

from .test_kingdoms_attack_phases import PhasesBundle
from .test_kingdoms_attack_score import ScoreBundle

_SAFE_NOW = datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
"""Frozen INSIDE the [T-15, T] defense window (fresh attack T = 18:00)."""


@pytest.fixture(autouse=True)
def _freeze_attacks_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _SAFE_NOW)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edits: list[dict[str, Any]] = []
        self.deleted = False

    async def delete(self, *, delay: float | None = None) -> None:
        self.deleted = True
        return None

    async def edit(self, content: str | None = None, *, view: Any = None) -> None:
        self.edits.append({"content": content, "view": view})
        self.content = content if content is not None else self.content
        return None


class _History:
    def __init__(self, items: list[_FakeMessage]) -> None:
        self.items = items

    def __aiter__(self) -> _History:
        return self

    async def __anext__(self) -> _FakeMessage:
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class _FakeChannel:
    def __init__(self, name: str = "attaquer") -> None:
        self.name = name
        self.history_list: list[_FakeMessage] = []
        self.sent: list[Any] = []

    async def edit(self, **kwargs: Any) -> None:
        if "name" in kwargs:
            self.name = str(kwargs["name"])

    def history(self, limit: int = 50) -> _History:
        return _History(list(self.history_list))

    async def send(self, content: str | None = None, *, view: Any = None, **kwargs: Any) -> Any:
        message = type("Sent", (), {"content": content, "view": view})()
        self.sent.append(message)
        return message


class _FakeGuild:
    def __init__(self, *channels: _FakeChannel) -> None:
        self.id = 1
        self.text_channels = list(channels)


class _FakeBot:
    def __init__(self, bundle: PhasesBundle, *channels: _FakeChannel) -> None:
        self.guilds = [_FakeGuild(*channels)]
        self.kingdoms_service = bundle.kingdoms
        self.kingdoms_attacks_service = bundle.attacks
        self.logs_service = None

    def get_user(self, user_id: str) -> Any:
        return None

    async def fetch_user(self, user_id: int) -> Any:
        raise RuntimeError("no user resolution in tests")


def _wiring(bundle: PhasesBundle) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=bundle.kingdoms,
            attacks_service=bundle.attacks,
            territories_service=bundle.territories,
        )
    )


def test_panel_buttons_rebuild_from_the_wire_in_both_locales() -> None:
    """Check 30's lesson: the persistent labels exist in both locales."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        KingdomAttackDeclareButton,
        KingdomAttackDefendButton,
        _room_key,
        _strings,
    )

    for locale in ("fr", "en"):
        strings = _strings(locale)
        declare = KingdomAttackDeclareButton(strings["declare_button"])
        assert declare.item.custom_id == "kingdoms:attack:declare"
        defend = KingdomAttackDefendButton("s1-a-t-king-a", strings["defend_button"])
        # Check 73: the id rides a 16-hex digest (the raw ~64-char attack
        # id blew Discord's 100-char cap and killed the board edit).
        assert defend.item.custom_id == f"kingdoms:attack:defend:{_room_key('s1-a-t-king-a')}"
        assert len(str(defend.item.custom_id)) <= 100
        assert ATTACK_PANEL_MARKER


async def test_defend_button_rebuilds_with_the_attack_id() -> None:
    """The restart-proof rebuild resolves the digest back to the attack."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDefendButton,
        _room_key,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(22)
    attack = await bundle.fresh_attack()
    _wiring(bundle)
    custom_id = f"kingdoms:attack:defend:{_room_key(attack.id)}"
    assert len(custom_id) <= 100
    match: Any = re.match(
        r"kingdoms:attack:defend:(?P<key>[0-9a-f]{16})",
        custom_id,
    )
    assert match is not None
    button = await KingdomAttackDefendButton.from_custom_id(
        MockInteraction(locale="en-US"),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        match,  # type: ignore[arg-type]
    )
    assert button.attack_id == attack.id
    assert button.item.custom_id == custom_id


async def test_deploy_attack_panel_pins_marker_and_buttons() -> None:
    """The declare salon stays CLEAN: marker + the declare button only."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        deploy_attack_panel,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(20)
    attack = await bundle.fresh_attack()
    channel = _FakeChannel()
    # No marker in history: first deployment falls back to send.
    await deploy_attack_panel(channel, "fr", bundle.kingdoms, bundle.attacks)
    assert len(channel.sent) == 1
    assert ATTACK_PANEL_MARKER in (channel.sent[0].content or "")
    ids = [item.custom_id for item in channel.sent[0].view.children]
    # b: the declare + history + plan buttons only (the attacks live
    # in their own salons now — no rows, no defend button here), plus
    # the ⚔️ info button (attaque-ergonomie-v1: it replaced the
    # declaration thread).
    assert ids == [
        "kingdoms:attack:declare",
        "kingdoms:attack:history",
        "kingdoms:attack:plan",
        "kingdoms:attack:info",
    ]
    assert attack.map_key not in (channel.sent[0].content or "")
    # The pointer tells the players where the defenses live.
    assert "PROPRE salon" in (channel.sent[0].content or "")


async def test_declaration_opens_the_attack_salon() -> None:
    """restructuration-serveur-v1 b: every declaration opens a DEDICATED
    salon right under the declare salon — right name, everyone denied
    send (with the explicit guild.me overwrite, Check 75), the 🛡️
    Défendre + ❌ Annuler buttons on the base message, refs stored."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _open_attack_salon_safe,
        _room_key,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    attack = await bundle.fresh_attack()
    _wiring(bundle)
    guild = MockGuild()
    declare = await guild.create_text_channel("Déclarer une attaque", position=3)
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), attack)
    salon = next(c for c in guild.text_channels if c.id != declare.id)
    # battle-ai-rdv-v1: the salon wears a readable 🗡️ duel name —
    # the ATTACKER VS the defender KINGDOM, slugified (the map title
    # lives in the announcement, never in the channel name).
    assert salon.name == "🗡️-king-a-vs-bourgogne"
    assert attack.map_key not in salon.name
    # Positioned right under the declare salon.
    assert salon.position == declare.position + 1
    # Check 75: the bot carries its own overwrite.
    # permissions-serveur-v1 (Drasah 19/10): @everyone is denied VIEW
    # too (a newcomer sees nothing) — the player roles carry the view.
    overwrites = dict(getattr(salon, "overwrites", {}) or {})
    everyone = overwrites.get(guild.default_role)
    assert everyone is not None
    assert everyone.send_messages is False
    assert everyone.view_channel is False
    mine = overwrites.get(guild.me)
    assert mine is not None
    assert mine.send_messages is True
    assert mine.view_channel is True
    # The base message carries the announcement (cover identity — the
    # salon name carries the map) + defend/cancel buttons.
    assert salon.messages
    base = salon.messages[0]
    assert "<@king-a>" in (base.content or "")  # no cover in fresh_attack
    assert "Bourgogne" in (base.content or "")
    ids = [str(c.custom_id) for c in base.view.children]
    assert f"kingdoms:attack:defend:{_room_key(attack.id)}" in ids
    assert f"kingdoms:attack:cancel:{_room_key(attack.id)}" in ids
    assert all(len(custom_id) <= 100 for custom_id in ids)
    # The refs route every later announcement to this salon.
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.score.get("attack_channel_id") == salon.id
    assert fresh.score.get("attack_base_message_id") == base.id


async def test_declaration_names_the_cover_not_the_attacker() -> None:
    """b: with an active cover the salon (and its announcement) shows
    the COVER lord — the real attacker stays masked until the reveal."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_attack_salon_safe, _strings
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(25)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, contre_espionnage=1)
    attack = await bundle.attacks.declare_attack(
        "king-a",
        target,
        "",
        cover_lord_id="lord-a",
        now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
    )
    assert attack.cover_lord_id == "lord-a"
    _wiring(bundle)
    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    # The salon name never leaks the real attacker — the cover identity
    # (lord-a is "Lord A", the masked king-a is "King A"),
    # slugified by the battle-room-id-v2 duel name.
    assert salon.name == "🗡️-lord-a-vs-bourgogne"
    assert "king-a" not in salon.name


async def test_cancel_attack_button_refunds_and_purges_the_salon() -> None:
    """b: the DECLARANT alone, inside the 10-minute window — budgets
    refunded (attacker AND defender when recorded), the salon purged."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackCancelButton,
        _open_attack_salon_safe,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    # A defense is already recorded — its budget is refunded too.
    await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))
    attacker = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "king-a")
    defender = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "lord-b")
    assert attacker.attack_used == 1
    assert defender.defense_used == 1
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 5, tzinfo=UTC)
    _wiring(bundle)
    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    button = KingdomAttackCancelButton(attack.id, _strings("fr")["cancel_attack_button"])
    # A NON-declarant is refused.
    stranger = MockInteraction(user=MockUser(id="king-b"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(stranger)  # type: ignore[arg-type]
    assert stranger.followup.messages
    assert "❌" in str(stranger.followup.messages[0].content)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert str(fresh.state.value) != "cancelled"
    # The declarant cancels inside the window — budgets refunded, salon gone.
    click = MockInteraction(user=MockUser(id="king-a"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(click)  # type: ignore[arg-type]
    assert click.followup.messages
    assert "annulée" in str(click.followup.messages[0].content)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert str(fresh.state.value) == "cancelled"
    assert fresh.resolved_at is not None
    assert fresh.is_over is True
    attacker = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "king-a")
    defender = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "lord-b")
    assert attacker.attack_used == 0
    assert defender.defense_used == 0
    assert guild.get_channel(salon.id) is None  # purged right away
    assert (fresh.score or {}).get("attack_salon_cleaned") is True


async def test_cancel_attack_refuses_after_the_window_and_the_reveal() -> None:
    """b: the 10-minute window closes, and a revealed attack (cover
    dropped, siege engaged) can no longer be cancelled."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.attacks import AttackStateError

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_attack(attack.id, "king-a")
    revealed = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    revealed.revealed = True
    await bundle.store.upsert_attack(revealed.to_mongo())
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_attack(attack.id, "king-a", now=datetime(2026, 10, 11, 12, 5, tzinfo=UTC))


async def test_cancel_defense_withdraws_and_refunds_before_the_draft() -> None:
    """b: the defender withdraws at any moment EXCEPT once the draft
    started (pick_ban_lead_minutes threshold) — budget refunded."""
    from kingdoms.mods.kingdoms.attacks import AttackStateError

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))
    # Only the defender may withdraw.
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_defense(attack.id, "king-a", now=datetime(2026, 10, 11, 12, 35, tzinfo=UTC))
    withdrawn = await bundle.attacks.cancel_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 35, tzinfo=UTC))
    assert str(withdrawn.state.value) == "declared"
    assert withdrawn.defender_lord_id is None
    defender = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "lord-b")
    assert defender.defense_used == 0
    # The defense may be retaken after a withdrawal.
    retaken = await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 13, 0, tzinfo=UTC))
    assert str(retaken.state.value) == "defended"
    # Once the draft lead starts (T - pick_ban_lead_minutes), refused.
    draft_start = attack.expires_at - timedelta(minutes=bundle.attacks._config.attacks.pick_ban_lead_minutes)
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_defense(attack.id, "lord-b", now=draft_start + timedelta(minutes=1))


async def test_defend_button_answers_the_surprise_cases() -> None:
    """b: the 🛡️ button NEVER disappears — wrong kingdom, first defense
    (no public sign) and already-defended answers."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackDefendButton, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    before = len(salon.messages)
    button = KingdomAttackDefendButton(attack.id, _strings("fr")["defend_button"])
    # A lord OUTSIDE the defending kingdom is refused.
    outsider = MockInteraction(user=MockUser(id="lord-a"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(outsider)  # type: ignore[arg-type]
    assert "Vous ne pouvez défendre" in str(outsider.response.message.content)
    # The first lord of the defending kingdom registers the defense —
    # the salon base message does NOT move (no public sign).
    click = MockInteraction(user=MockUser(id="lord-b"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(click)  # type: ignore[arg-type]
    assert "défense de ton royaume" in str(click.followup.messages[0].content)
    assert len(salon.messages) == before
    # Another lord of the same kingdom: « Le royaume est déjà défendu ».
    again = MockInteraction(user=MockUser(id="king-b"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(again)  # type: ignore[arg-type]
    assert "déjà défendu" in str(again.response.message.content)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.defender_lord_id == "lord-b"


async def test_history_button_lists_resolved_confrontations() -> None:
    """b: the 📜 button shows the RESOLVED confrontations (timestamped,
    paginated) — cancelled, won, expired alike."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackHistoryButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 5, tzinfo=UTC)
    await bundle.attacks.cancel_attack(attack.id, "king-a")
    _wiring(bundle)
    button = KingdomAttackHistoryButton(_strings("fr")["history_button"])
    assert button.item.custom_id == "kingdoms:attack:history"
    click = MockInteraction(user=MockUser(id="king-a"), locale="fr")  # type: ignore[arg-type]
    await button.callback(click)  # type: ignore[arg-type]
    content = str(click.response.message.content)
    assert "Historique" in content
    assert "annulée" in content
    assert attack.map_key in content


async def test_deploy_attack_panel_removes_duplicate_markers() -> None:
    """attaques-panel-spam: two markers keep the first, delete the rest."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        deploy_attack_panel,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    await bundle.fresh_attack()
    channel = _FakeChannel()
    first = _FakeMessage(f"panel A\n{ATTACK_PANEL_MARKER}")
    dupes = [_FakeMessage(f"panel B{i}\n{ATTACK_PANEL_MARKER}") for i in range(2)]
    channel.history_list = [first, *dupes]
    await deploy_attack_panel(channel, "fr", bundle.kingdoms, bundle.attacks)
    assert channel.sent == []
    assert len(first.edits) == 1
    assert all(d.deleted for d in dupes)


async def test_declare_flow_declares_the_attack() -> None:
    """The GUIDED flow (kingdom → territory → cover → lobby) declares (recâblage)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    # Step 1 — the kingdom select (Bourgogne visible with its AI level).
    kingdom_view = (click.response.message or None) and click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    assert step.response.message is not None
    # Step 2 — the territory select of the chosen kingdom.
    territory_view = step.response.message.view
    assert territory_view is not None
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    # Step 3 — no contre-espionnage stock: the attack declares
    # DIRECTLY (Drasah 18/10 — no modal: an empty modal is rejected
    # by Discord and killed the flow, live beta 18/10).
    await territory_select.callback(step2)  # type: ignore[arg-type]
    # battle-ai-rdv-v1: the map is chosen — the SELECTOR ephemeral dies
    # instantly (no stale wizard left behind after the declaration).
    assert click.original_deleted is True
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert str(attacks[0].state.value) == "declared"
    assert attacks[0].map_key == target
    assert attacks[0].lobby_url == ""
    assert step2.response.deferred is True
    assert step2.followup.messages
    assert "Attaque déclarée" in (step2.followup.messages[0].content or "")


async def test_declare_flow_asks_the_cover_when_stock_exists() -> None:
    """The cover question appears only when a contre-espionnage charge exists."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(25)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, contre_espionnage=1)
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    kingdom_view = click.response.message and click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    territory_view = step.response.message and step.response.message.view
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    await territory_select.callback(step2)  # type: ignore[arg-type]
    # The cover question (OUI/NON) replaces the territory select.
    assert step2.response.message is not None
    cover_view = step2.response.message.view
    assert cover_view is not None
    ids = [c.custom_id for c in cover_view.children]
    assert "kingdoms:attack:cover:yes" in ids
    assert "kingdoms:attack:cover:no" in ids


async def test_declare_flow_skips_the_cover_when_the_attacker_is_solo() -> None:
    """Drasah 18/10 v5: a lone lord cannot bluff — even WITH a
    contre-espionnage charge the question is skipped, the attack
    declares openly and an explicit note says why (live beta: the OUI
    click jumped to the lobby in silence)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(25)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    # The king is ALONE: the only other Aquitaine lord left the season.
    lord_a = next(item for item in await bundle.kingdoms.lords() if str(item.id) == "lord-a")
    lord_a.left = True
    await bundle.store.upsert_lord(lord_a.to_mongo())
    await bundle.stock(aquitaine, contre_espionnage=1)
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    kingdom_view = click.response.message and click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    territory_view = step.response.message and step.response.message.view
    assert territory_view is not None
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    await territory_select.callback(step2)  # type: ignore[arg-type]
    # Solo: the attack declares DIRECTLY — no OUI/NON question…
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert attacks[0].cover_lord_id is None
    assert step2.response.deferred is True
    assert not step2.response.message
    # …and the note explains WHY (no silent skip, no charge consumed).
    bodies = [str(m.content or "") for m in step2.followup.messages]
    assert any("Attaque déclarée" in body for body in bodies)
    assert any("aucun autre seigneur actif" in body for body in bodies)
    fresh_state = await bundle.attacks.technology_state(aquitaine)
    assert fresh_state.purchases.get("contre_espionnage") == 1


async def test_declare_flow_attacks_gaia_through_the_free_for_all() -> None:
    """Drasah 15/10: Gaïa is attackable via the guided flow (no cover step)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        KingdomAttackLobbyModal,
        _strings,
    )
    from kingdoms.mods.kingdoms.models import AttackKind

    bundle = PhasesBundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(26)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    # Step 1 — Gaïa appears in the kingdom select.
    kingdom_view = click.response.message and click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    values = [o.value for o in kingdom_select.options]
    assert gaia in values
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [gaia]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    # Step 2 — the Gaïa territories show up.
    territory_view = step.response.message and step.response.message.view
    assert territory_view is not None
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    assert gaia_map in [o.value for o in territory_select.options]
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [gaia_map]},
        locale="fr",  # type: ignore[arg-type]
    )
    # Step 3 — a Gaïa target skips the cover question: lobby straight away.
    await territory_select.callback(step2)  # type: ignore[arg-type]
    modal = step2.response.modal
    assert isinstance(modal, KingdomAttackLobbyModal)
    assert modal.is_gaia is True
    modal.lobby_input._value = "aoe2de://gaia"  # type: ignore[attr-defined]
    submit = MockInteraction(user=MockUser(id="king-a"), locale="fr")  # type: ignore[arg-type]
    await modal.on_submit(submit)  # type: ignore[arg-type]
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert attacks[0].kind is AttackKind.GAIA
    assert attacks[0].participants == ["king-a"]
    assert submit.followup.messages
    assert "Attaque déclarée" in (submit.followup.messages[0].content or "")


async def test_join_button_adds_a_lord_to_the_gaia_battle() -> None:
    """Another lord joins the open Gaïa battle from the panel button."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackJoinButton, _strings

    bundle = PhasesBundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(27)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    battle = await bundle.attacks.declare_gaia_attack("king-a", gaia_map, "aoe2de://g")
    assert battle.participants == ["king-a"]
    _wiring(bundle)
    button = KingdomAttackJoinButton(battle.id, _strings("fr")["gaia_join_button"])
    assert len(str(button.item.custom_id)) <= 100  # Check 73: digest, not raw id
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-b"),  # type: ignore[arg-type]
        custom_id=str(button.item.custom_id),
        locale="fr",
    )
    await button.callback(click)  # type: ignore[arg-type]
    assert click.followup.messages
    assert "rejoint la bataille" in (click.followup.messages[0].content or "")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == battle.id)
    assert fresh.participants == ["king-a", "king-b"]
    # A kingdom joins a free-for-all once (D42): king-b cannot rejoin.
    again = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-b"),  # type: ignore[arg-type]
        custom_id=str(button.item.custom_id),
        locale="fr",
    )
    await button.callback(again)  # type: ignore[arg-type]
    assert again.followup.messages
    assert "❌" in (again.followup.messages[0].content or "")


async def test_gaia_declaration_opens_its_salon_and_is_never_cancellable() -> None:
    """restructuration-serveur-v1 b: a Gaïa FFA gets the SAME salon flow —
    🦅 Rejoindre + ❌ Annuler on the base message, the join announces
    IN the salon, and the service REFUSES a Gaïa cancellation (axis
    ouvert Drasah — default followed)."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.attacks import AttackStateError
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackJoinButton,
        _open_attack_salon_safe,
        _room_key,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(28)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    battle = await bundle.attacks.declare_gaia_attack(
        "king-a", gaia_map, "aoe2de://g", now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    )
    _wiring(bundle)
    guild = MockGuild()
    declare = await guild.create_text_channel("Déclarer une attaque")
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), battle)
    salon = next(c for c in guild.text_channels if c.id != declare.id)
    # battle-ai-rdv-v1: the gaia salon wears the 🗡️ duel name — the
    # attacker VS « Gaïa » (the map title stays in the announcement).
    assert salon.name == "🗡️-king-a-vs-gaia"
    ids = [str(c.custom_id) for c in salon.messages[0].view.children]
    assert f"kingdoms:attack:join:{_room_key(battle.id)}" in ids  # 🦅 Rejoindre
    assert f"kingdoms:attack:cancel:{_room_key(battle.id)}" in ids  # ❌ Annuler
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == battle.id)
    assert fresh.score.get("attack_channel_id") == salon.id

    # The join announce lands in the salon.
    button = KingdomAttackJoinButton(battle.id, _strings("fr")["gaia_join_button"])
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-b"),
        guild=guild,
        locale="fr",  # type: ignore[arg-type]
    )
    await button.callback(click)  # type: ignore[arg-type]
    join_announces = [m for m in salon.messages if "rejoint la bataille" in (m.content or "")]
    assert join_announces  # the join is announced in the salon

    # The service REFUSES a Gaïa cancellation — even the declarant,
    # even inside the window (axis ouvert, default followed).
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 5, tzinfo=UTC)
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_attack(battle.id, "king-a")


async def test_defend_button_takes_the_defense() -> None:
    """The persistent defend button runs respond_defense (D77)."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackDefendButton, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(22)
    attack = await bundle.fresh_attack()
    # Drasah 18/10: the defense window opens AT the declaration — the
    # frozen clock sits inside it (12:30, declared at 12:00).
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    _wiring(bundle)
    button = KingdomAttackDefendButton(attack.id, _strings("fr")["defend_button"])
    interaction = MockInteraction(user=MockUser(id="lord-b"), locale="fr")  # type: ignore[arg-type]
    await button.callback(interaction)  # type: ignore[arg-type]
    assert interaction.followup.messages
    assert "défense de ton royaume" in (interaction.followup.messages[0].content or "")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.defender_lord_id == "lord-b"


async def _salon_guild(bundle: PhasesBundle, attack: Any) -> MockGuild:
    """Build a MockGuild with the declare salon + the attack's OWN salon (b)."""
    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    return guild


class _GuildBot:
    """A bot whose guilds are MockGuild instances (salon-based ticks)."""

    def __init__(self, bundle: PhasesBundle, guild: MockGuild) -> None:
        self.guilds = [guild]
        self.kingdoms_service = bundle.kingdoms
        self.kingdoms_attacks_service = bundle.attacks
        self.logs_service = None

    def get_user(self, user_id: str) -> Any:
        return None

    async def fetch_user(self, user_id: int) -> Any:
        raise RuntimeError("no user resolution in tests")


async def test_scheduler_reveals_then_the_ai_takes_over_at_the_meeting_time() -> None:
    """The tick drives the phases: T-15 cover drop, T the AI takeover + RDV."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        run_attack_phases_safe,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    attack = await bundle.fresh_attack()
    guild = await _salon_guild(bundle, attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    declare_channel = guild.text_channels[0]
    bot = _GuildBot(bundle, guild)
    # T-16: nothing is due yet — the tick stays silent.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 44, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert salon.messages == []
    # T-2: the reveal runs IN THE SALON — the cover drops, the defense
    # stays OPEN (no AI).
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert not [m for m in salon.messages if "IA" in (m.content or "")]  # no AI at the cutoff
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.revealed is True
    assert fresh.ai_defender is False
    assert any("Assaut irrésistible" in (m.content or "") for m in salon.messages)
    assert any(
        ATTACK_PANEL_MARKER in (m.content or "") for m in declare_channel.messages
    )  # the panel re-pinned in the declare salon
    # T+1: the meeting time — nobody defended, the bot AI takes the
    # defense (battle-ai-rdv-v1): the draft RUNS even solo — the
    # attacker's DM leaves and the AI arms its random picks + delay.
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 1, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert any("IA" in (m.content or "") for m in salon.messages)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.ai_defender is True
    assert not (fresh.pickban or {}).get("sent")  # the draft leaves on the NEXT tick
    # T+2: the tick sends the attacker's draft DM — the AI arms its 3
    # RANDOM picks of its hand + its delayed validation (the
    # resolution waits for the attacker's ranking AND the AI's mark).
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths")
    await bundle.give_civs("Bourgogne", "huns", "perses", "byzantins")
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 2, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.pickban["sent"] is True  # the draft DM leaves at once
    assert not fresh.pickban.get("resolved_pickban")
    assert len(fresh.pickban["defender_picks"]) == 3
    assert 30 <= fresh.pickban["ai_validate_at"] - int(datetime(2026, 10, 11, 18, 2, tzinfo=UTC).timestamp()) <= 90
    assert not [m for m in salon.messages if "Forfait" in (m.content or "")]


async def test_scheduler_idle_tick_deploys_nothing() -> None:
    """attaques-panel-spam: an idle tick (no phase due) sends NOTHING."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import run_attack_phases_safe

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    attack = await bundle.fresh_attack()
    guild = await _salon_guild(bundle, attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    declare_channel = guild.text_channels[0]
    bot = _GuildBot(bundle, guild)
    # T-16: nothing is due — the tick must be totally silent.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 44, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert declare_channel.messages == []
    assert salon.messages == []


async def test_scheduler_reveal_names_the_defender_in_the_salon() -> None:
    """restructuration-serveur-v1 b: the salon replaces the Attaques board.

    At the T-15 reveal the cover drops and the outcome line lands in the
    attack's own salon: « Assaut irrésistible » when nobody defended,
    « Bataille en vue (défenseur : X) » — NAMING the human defender at
    last (Drasah 19/10) — when one answered.
    """
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import run_attack_phases_safe

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    guild = await _salon_guild(bundle, attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    bot = _GuildBot(bundle, guild)
    # T-2: the reveal runs (cover drop + outcome line) — nobody
    # defended yet, so the salon shows the irresistible assault.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.revealed is True
    assert fresh.ai_defender is False
    assert any("Assaut irrésistible — aucune défense" in (m.content or "") for m in salon.messages)
    # T+1: the meeting time — nobody defended, the bot AI takes the
    # defense; the announce fires in the salon.
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 1, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert any("IA" in (m.content or "") for m in salon.messages)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.ai_defender is True

    # The defended case: another attack, a lord defends right after
    # the declaration, then the reveal tick shows the battle — with
    # the defender NAMED at last (restructuration-serveur-v1 b).
    bundle2 = PhasesBundle()
    await bundle2.launch_season()
    await bundle2.draw(25)
    attack2 = await bundle2.fresh_attack()
    await bundle2.attacks.respond_defense(attack2.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))
    guild2 = await _salon_guild(bundle2, attack2)
    salon2 = next(c for c in guild2.text_channels if c.name != "Déclarer une attaque")
    bot2 = _GuildBot(bundle2, guild2)
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot2)
    battle = [m for m in salon2.messages if "Bataille en vue" in (m.content or "")]
    assert battle
    assert "défenseur : Lord B" in (battle[-1].content or "")


async def test_pickban_view_selects_acknowledge_and_validate() -> None:
    """Live beta 18/10: the pick & ban selects carry a callback — the
    acknowledged interaction stores the values on the view instance."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomPickBanView

    view = KingdomPickBanView(
        "s-20261018-015707-a-t-steppe-king-a-1791720000",
        "king-a",
        "attacker",
        ["francs", "bretons", "goths"],
        "fr",
    )
    selects = [c for c in view.children if getattr(c, "custom_id", "").startswith("kingdoms:attack:pickban:pick:")]
    assert len(selects) == 3
    for select in selects:
        assert callable(select.callback)  # « the application did not respond » fix
    validate = next(c for c in view.children if getattr(c, "custom_id", "") == "kingdoms:attack:pickban:validate")
    assert callable(validate.callback)
    # Drasah 18/10 v2: no sabotage select anymore — the moves ride the
    # battle room, and the resolution is BLIND (no adversary hand here).
    assert not [c for c in view.children if "sabotage" in str(getattr(c, "custom_id", ""))]
    assert len(view.children) == 4  # 3 picks + validate


async def test_room_and_ready_custom_ids_fit_the_discord_limit() -> None:
    """Live beta 18/10: the RDV DM died with 50035 (custom_id > 100) —
    the persistent ids must stay short whatever the attack id length."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomResultButton,
        KingdomRoomUrlButton,
        KingdomSabotageOpenButton,
        KingdomSabotagePassButton,
        _strings,
    )

    attack_id = "s-20261018-015707-a-s-20261018-015707-t-plaine-king-a-1791720000"
    player_id = "242421489536729100"
    strings = _strings("fr")
    ready = KingdomSabotageOpenButton(attack_id, player_id, strings["sab_open_button"])
    passer = KingdomSabotagePassButton(attack_id, player_id, strings["sab_pass_button"])
    url = KingdomRoomUrlButton(attack_id, strings["score_url_button"])
    result = KingdomRoomResultButton(attack_id, strings["result_button"])
    ids = [str(ready.custom_id), str(passer.custom_id), str(url.custom_id), str(result.custom_id)]
    for custom_id in ids:
        assert 1 <= len(custom_id) <= 100
    assert len(set(ids)) == 4
    # Restart-stable: the same inputs rebuild the same ids (bot.add_view
    # re-registration must keep answering).
    twin = [
        KingdomSabotageOpenButton(attack_id, player_id, strings["sab_open_button"]),
        KingdomSabotagePassButton(attack_id, player_id, strings["sab_pass_button"]),
        KingdomRoomUrlButton(attack_id, strings["score_url_button"]),
        KingdomRoomResultButton(attack_id, strings["result_button"]),
    ]
    assert [str(item.custom_id) for item in twin] == ids
    # The key is a 16-hex digest — the raw attack id never rides the id.
    assert all(
        re.fullmatch(r"[0-9a-f]{16}", part)
        for part in (ready.custom_id.split(":")[-1], passer.custom_id.split(":")[-1], url.custom_id.split(":")[-1])
    )


async def test_score_due_opens_the_battle_room_with_shuffled_civs() -> None:
    """Drasah 18/10 v2: at T the meeting opens a battle room — role,
    voice channel under the provisioned « Champs de Bataille » category
    (battle-room-categorie-v1: the category is NEVER created here), a
    text-in-voice message with each player's SHUFFLED civs + the
    URL/result buttons, and the room refs stored for the presence
    tracking."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    _wiring(bundle)
    guild = MockGuild()
    # battle-room-categorie-v1: the room joins the PROVISIONED category
    # — the declare salon lives in the battlefield group, the attack
    # salon sits next to it.
    battlefield = await guild.create_category("Champs de Bataille")
    declare = await guild.create_text_channel("Déclarer une attaque", category=battlefield)
    assert declare is not None
    # restructuration-serveur-v1 b: the meeting message lives in the
    # ATTACK SALON — record it BEFORE the room opens.
    salon = await guild.create_text_channel("salon d'attaque", category=battlefield)
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    # The scheduler passes the FRESH store object (a stale pre-draft
    # snapshot would carry an empty pick & ban).
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    # The fixture declares with a legacy lobby URL — production declares
    # empty (the ID is shared at the RDV, v5): clear it like the real flow.
    attack.lobby_url = ""
    await bundle.store.upsert_attack(attack.to_mongo())
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    room_id = fresh.score.get("room_channel_id")
    assert room_id
    assert fresh.score.get("room_role_id")
    room = guild.get_channel(int(room_id))
    assert room is not None
    assert room.user_limit == 2
    # battle-room-categorie-v1: the room sits in the PROVISIONED
    # category — and NO category was ever created (the legacy flow
    # created a « Champs de bataille » duplicate here).
    assert room.category is battlefield
    assert [c.name for c in guild.categories] == ["Champs de Bataille"]
    # Check 75 live: the @everyone deny locked the bot out of its own
    # room (403 50001 on the text-in-voice message) — the bot must carry
    # its own overwrites on the channel it creates.
    mine = room._overwrites.get(guild.me)
    assert mine is not None
    assert mine.view_channel is True
    assert mine.send_messages is True
    # The meeting single-message lands in the SALON — the voice room
    # keeps only the voice (b). battle-room-id-v2: the open also PINGS
    # the two players (message #2) so they cannot miss the scoreboard.
    assert room.messages == []
    assert len(salon.messages) == 2
    ping = str(salon.messages[1].content or "")
    assert "<@king-a>" in ping and "<@lord-b>" in ping
    body = salon.messages[0].content or ""
    # Drasah 18/10 v4: the meeting shows the FINAL civ of each player only
    # (post-sabotage, first surviving pick) — the full hands stay in DM.
    assert "francs" in body
    assert "huns" in body
    assert "bretons" not in body
    assert "perses" not in body
    # v5 single message: 🔗 ID only at first — ✅ Prêt appears once
    # the ID is set, 🏆 once both players are Prêt.
    view = salon.messages[0].view
    ids = {str(c.custom_id) for c in view.children}
    assert any(cid.startswith("kingdoms:attack:room:url:") for cid in ids)
    assert not any(cid.startswith("kingdoms:attack:room:ready:") for cid in ids)
    assert not any(cid.startswith("kingdoms:attack:room:result:") for cid in ids)
    assert fresh.score.get("meeting_channel_id") == salon.id
    assert fresh.score.get("meeting_message_id") == salon.messages[0].id
    # v4: the sabotage moved to the DMs — no sabotage button in the room.
    assert not any("sabotage" in cid or cid.startswith("kingdoms:attack:room:sab:") for cid in ids)
    # Idempotent: a replayed tick never duplicates the room.
    await _open_battle_room(bot, guild, fresh, bundle.kingdoms, "fr")
    rooms = [c for c in guild.channels if getattr(c, "user_limit", None) == 2]
    assert len(rooms) == 1
    # The ID is shared — the SAME message refreshes: ✅ Prêt unlocks,
    # the ID shows in the body, 🏆 stays hidden until both are Prêt.
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _refresh_room_message_safe

    await bundle.attacks.set_lobby_url(attack.id, "king-a", "4242")
    stamped = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _refresh_room_message_safe(bot, stamped, "fr")
    assert len(salon.messages) == 2  # the SAME salon message refreshes
    refreshed = {str(c.custom_id) for c in salon.messages[0].view.children}
    assert any(cid.startswith("kingdoms:attack:room:ready:") for cid in refreshed)
    assert not any(cid.startswith("kingdoms:attack:room:result:") for cid in refreshed)
    assert "4242" in str(salon.messages[0].content or "")


async def test_battle_room_never_creates_a_category() -> None:
    """battle-room-categorie-v1 (Drasah 19/10): the legacy room flow
    created a « Champs de bataille » category duplicate (its exact-name
    lookup never matched the provisioned « Champs de Bataille »). With
    no category at all the room lands at the guild root — NEVER a new
    category."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    room = guild.get_channel(int(fresh.score["room_channel_id"]))
    assert room is not None
    # No category anywhere: the room sits at the guild root and the
    # flow NEVER created one.
    assert room.category is None
    assert guild.categories == []


async def test_battle_room_joins_the_category_of_the_declare_salon() -> None:
    """battle-room-categorie-v1: the room resolves the battlefield
    category through the declare salon's own category — the two live
    in the same provisioned group, whatever the category's name."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _battlefield_category

    guild = MockGuild()
    assert _battlefield_category(guild) is None
    battlefield = await guild.create_category("Champs de Bataille")
    await guild.create_text_channel("Déclarer une attaque", category=battlefield)
    assert _battlefield_category(guild) is battlefield
    # A lone category without any declare salon still matches by slug.
    guild2 = MockGuild()
    other = await guild2.create_category("Champs de Bataille")
    assert _battlefield_category(guild2) is other


async def test_battle_room_text_post_failure_still_grants_roles_and_dms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check 75: the text-in-voice post is best effort — a failing send
    must not bury the role grants, the room refs nor the room DMs."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room
    from tests.mocks.discord_mock import MockTextChannel

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)

    class _DmUser:
        def __init__(self, uid: str) -> None:
            self.id = uid

        async def send(self, content: str, view: Any = None) -> Any:
            outbox.append((str(self.id), content))
            return None

    class _DmBot(_FakeBot):
        def get_user(self, user_id: str) -> Any:
            return _DmUser(str(user_id))

    outbox: list[tuple[str, str]] = []
    bot = _DmBot(bundle)
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)

    original_send = MockTextChannel.send

    async def failing_send(self: MockTextChannel, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("403 50001 Missing Access (simulated)")

    monkeypatch.setattr(MockTextChannel, "send", failing_send)
    try:
        await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    finally:
        monkeypatch.setattr(MockTextChannel, "send", original_send)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    # The refs are stored BEFORE the message: cleanup can find the room.
    assert fresh.score.get("room_channel_id")
    assert fresh.score.get("room_role_id")
    # Both players still got the room DM pointing at the vocal.
    dm_targets = {uid for uid, _msg in outbox}
    assert dm_targets >= {str(attack.attacker_lord_id), str(attack.defender_lord_id)}


async def test_room_ready_button_announces_the_match_when_both_ready() -> None:
    """Check 76: the vocal's ✅ Prêt button records the readiness and,
    once BOTH players clicked, creates the match scheduled event and
    announces the start in the room (a re-click never re-announces)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomReadyButton,
        _open_battle_room,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    # open_meeting: the score phase must be OPEN before a Prêt click.
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    room = next(c for c in guild.channels if getattr(c, "user_limit", None) == 2)
    strings = _strings("fr")
    # battle-room-id-v1: one Prêt button PER SIDE (attacker/defender).
    button = KingdomRoomReadyButton(
        attack.id, strings["room_ready_attacker_button"].format(player="king-a"), side="attacker"
    )
    other_side = KingdomRoomReadyButton(
        attack.id, strings["room_ready_defender_button"].format(player="lord-b"), side="defender"
    )

    # Drasah 18/10 v4: no lobby URL yet — the Prêt is politely refused.
    raw = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    raw.lobby_url = ""
    await bundle.store.upsert_attack(raw.to_mongo())
    refused = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr")
    # type: ignore[arg-type]
    await button.callback(refused)  # type: ignore[arg-type]
    assert refused.response.message is not None
    assert "ID" in str(refused.response.message.content or "")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert not (fresh.score or {}).get("ready")
    # The attacker shares the lobby URL — the gate opens.
    await bundle.attacks.set_lobby_url(attack.id, "king-a", "aoe2de://lobby/42")

    # battle-room-id-v1: the WRONG side is refused cleanly (each Prêt
    # button belongs to ONE side of the scoreboard).
    wrong = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await button.callback(wrong)  # type: ignore[arg-type]
    assert wrong.response.message is not None
    # battle-room-id-v2: the refusal is worded for the player and
    # self-destructs after 10 s (timer armed, never blocks the test).
    assert "réservé à votre adversaire" in str(wrong.response.message.content or "")
    fresh_wrong = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert not (fresh_wrong.score or {}).get("ready")

    created: list[dict[str, Any]] = []

    async def _fake_create_scheduled_event(**kwargs: Any) -> Any:
        created.append(kwargs)

        class _E:
            id = 987654321

        return _E()

    guild.create_scheduled_event = _fake_create_scheduled_event  # type: ignore[method-assign]
    first = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await button.callback(first)  # type: ignore[arg-type]
    # battle-room-id-v1: NO ephemeral bubble — the interaction defers
    # silently and the SCOREBOARD (the salon's single message) shows
    # the Prêt on the attacker's side, the defender still waiting.
    assert first.response.deferred
    scoreboard = str(salon.messages[0].content or "")
    assert "✅ Prêt" in scoreboard
    assert "en attente du Prêt" in scoreboard
    assert not created
    second = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await other_side.callback(second)  # type: ignore[arg-type]
    # Both ready: the match event is created once, the ref is stored.
    assert len(created) == 1
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (fresh.score or {}).get("room_event_id") == 987654321
    # v5: no extra post — the SAME salon message carries 🏆 and the
    # « bonne game » footer once both are Prêt (no « La partie commence »).
    # v2: the salon counts TWO messages (scoreboard + player ping).
    assert len(salon.messages) == 2
    final_view = {str(c.custom_id) for c in salon.messages[0].view.children}
    assert any(cid.startswith("kingdoms:attack:room:result:") for cid in final_view)
    assert "bonne game" in str(salon.messages[0].content or "")
    assert not any("La partie commence" in (m.content or "") for m in salon.messages)
    # A re-click by the same player never re-announces the match.
    again = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await other_side.callback(again)  # type: ignore[arg-type]
    assert len(created) == 1
    # v2: the 10 s self-destruction timer of the wrong-side refusal
    # never blocks the suite.
    import asyncio

    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_room_url_button_locks_the_entry_then_a_changed_id_resets_the_ready() -> None:
    """battle-room-id-v1: the 🔗 click instantly locks the shared view
    (« saisie de l'ID en cours », disabled) and opens the modal — and a
    CHANGED ID submitted through the modal resets both Prêts with the
    dedicated ephemeral note."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomUrlButton,
        KingdomRoomUrlModal,
        _open_battle_room,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    room = next(c for c in guild.channels if getattr(c, "user_limit", None) == 2)
    strings = _strings("fr")
    # An ID is already shared and both players are Prêt.
    await bundle.attacks.set_lobby_url(attack.id, "king-a", "aoe2de://lobby/1")
    ready = await bundle.attacks.mark_ready(attack.id, "king-a")
    await bundle.attacks.mark_ready(ready.id, "lord-b")
    # The attacker asks for a modification — the shared view LOCKS.
    button = KingdomRoomUrlButton(attack.id, strings["room_id_modify_button"])
