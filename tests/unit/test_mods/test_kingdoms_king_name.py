"""Kingdom-name flow (drasah D70 v2): DM button + modal, never free text.

Regression guarded (drasah live incident 2026-10-10): a plain DM reply
was silently ignored once the King's application had been wiped by a
relaunch — the new flow always answers, with the reason when the state
moved on (``king_name_state_invalid``) or the outcome (received/error).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomKingNameButton,
    KingdomKingNameModal,
    KingdomsPanelWiring,
    _king_name_strings,
    _send_king_name_request,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import (
    MockClient,
    MockGuild,
    MockInteraction,
    MockMember,
    MockUser,
)

pytestmark = pytest.mark.asyncio


@dataclass
class FakeKingLord:
    id: str
    role: str = "king"
    kingdom_id: str | None = None
    left: bool = False
    in_queue: bool = True
    display_name: str = "Arthur"


@dataclass
class FakeKingdom:
    id: str
    name: str
    validation: str = "pending"
    is_gaia: bool = False


class FakeKingdomsService:
    """Just the surface the king-name flow needs."""

    def __init__(self, lords: list[FakeKingLord] | None = None) -> None:
        self._lords = lords or []
        self.founded: list[tuple[str, str]] = []

    async def lords(self) -> list[FakeKingLord]:
        return self._lords

    async def kingdoms(self) -> list[FakeKingdom]:
        return []

    async def found_kingdom(self, player_id: str, name: str) -> FakeKingdom:
        self.founded.append((player_id, name))
        return FakeKingdom("k-new", name)


def _wire(service: FakeKingdomsService) -> None:
    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))


def _button() -> KingdomKingNameButton:
    return KingdomKingNameButton(_king_name_strings("en")["king_name_button"][:80])


async def test_button_opens_the_naming_modal_for_a_queued_king() -> None:
    service = FakeKingdomsService([FakeKingLord("111")])
    _wire(service)
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _button().callback(interaction)
    assert isinstance(interaction.response.modal, KingdomKingNameModal)


async def test_button_answers_when_the_king_state_is_gone() -> None:
    """The incident: the application was wiped by a relaunch — the click
    must answer with the localized reason, not stay silent."""
    service = FakeKingdomsService([])  # no lord record left
    _wire(service)
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _button().callback(interaction)
    assert interaction.response.modal is None
    assert interaction.response.sent
    message = interaction.response.message
    assert message is not None
    assert _king_name_strings("en")["king_name_state_invalid"] in (message.content or "")


async def test_button_answers_for_a_king_that_already_founded() -> None:
    service = FakeKingdomsService([FakeKingLord("111", kingdom_id="k-1", in_queue=False)])
    _wire(service)
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await _button().callback(interaction)
    assert interaction.response.modal is None
    assert interaction.response.sent


async def test_modal_on_submit_founds_the_kingdom_and_confirms() -> None:
    service = FakeKingdomsService([FakeKingLord("111")])
    _wire(service)
    modal = KingdomKingNameModal("en-US")
    modal.name._value = "Aquitaine"
    guild = MockGuild()
    interaction: Any = MockInteraction(
        user=MockUser(id=111),
        locale="en-US",
        client=MockClient(guilds=[guild]),
    )
    await modal.on_submit(interaction)
    assert service.founded == [("111", "Aquitaine")]
    assert interaction.followup.messages
    content = interaction.followup.messages[-1].content or ""
    assert "Aquitaine" in content
    assert _king_name_strings("en")["king_name_dm_received"].format("Aquitaine").split("\n")[0] in content


async def test_modal_on_submit_reports_the_service_error() -> None:
    service = FakeKingdomsService([FakeKingLord("111")])

    async def _boom(player_id: str, name: str) -> FakeKingdom:
        raise RuntimeError("season is imposed")

    service.found_kingdom = _boom  # type: ignore[method-assign]
    _wire(service)
    modal = KingdomKingNameModal("en-US")
    modal.name._value = "Aquitaine"
    interaction: Any = MockInteraction(user=MockUser(id=111), locale="en-US")
    await modal.on_submit(interaction)
    assert interaction.followup.messages
    content = interaction.followup.messages[-1].content or ""
    assert "RuntimeError" in content


async def test_send_king_name_request_dms_a_button_not_free_text() -> None:
    guild = MockGuild()
    member = MockMember(id=111, name="Arthur", guild=guild)
    guild.add_member(member)
    await _send_king_name_request(guild, "111", "en")
    assert len(member.dm_messages) == 1
    message = member.dm_messages[0]
    assert message.view is not None
    custom_ids = [
        getattr(item, "custom_id", None)
        for item in message.view.children
    ]
    assert "kingdoms:king:name-request" in custom_ids


async def test_send_king_name_request_is_a_silent_noop_for_a_gone_member() -> None:
    guild = MockGuild()  # nobody: the DM is skipped without raising
    await _send_king_name_request(guild, "404", "en")
    assert guild.text_channels == []
