"""Kingdoms — the 2026-10-11 late-creation lot.

Drasah's live reports: (1) the AI kingdom Gaïa drew its 8 territories
but its Patrouille/Territoire salon views were never pinned — the
content deployment skipped every ``is_gaia`` kingdom (Gaïa has no
Alliances view and never draws civilizations); (2) a King enrolled
while no kingdom existed was stuck forever — an admin kingdom created
afterwards never pinged the waiting heirs, so they could never claim
the new throne.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.mods.kingdoms.service import KingdomsService

from .test_kingdoms_events import MemoryStore, _config
from .test_kingdoms_leave_flow import _FakeMemberlessGuild

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def service() -> KingdomsService:
    return KingdomsService(MemoryStore(), _config())


def _wire(a_service: KingdomsService) -> None:
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=a_service))


async def test_repin_deploys_the_state_views_of_gaia(service: KingdomsService) -> None:
    """Gaïa is part of the anti-stale refresh (its slice has no civs)."""
    import kingdoms.mods.kingdoms.kingdom_persistent as persistent

    _wire(service)
    await service.launch(["Aquitaine"])
    deployed: list[str] = []

    async def _fake_deploy(guild: Any, kingdom: Any) -> None:
        deployed.append(str(kingdom.id))

    original = persistent._deploy_realm_content_safe
    persistent._deploy_realm_content_safe = _fake_deploy  # type: ignore[assignment]
    try:
        await persistent._repin_realm_views_safe(_FakeMemberlessGuild())
    finally:
        persistent._deploy_realm_content_safe = original  # type: ignore[assignment]

    assert "gaia" in deployed, "Gaïa's salon state views must deploy with the others"
    assert "k-1" in deployed


async def test_refresh_single_kingdom_covers_gaia(service: KingdomsService) -> None:
    """A Gaïa-targeted refresh deploys too (the per-kingdom path)."""
    import kingdoms.mods.kingdoms.kingdom_persistent as persistent

    _wire(service)
    await service.launch(["Aquitaine"])
    deployed: list[str] = []

    async def _fake_deploy(guild: Any, kingdom: Any) -> None:
        deployed.append(str(kingdom.id))

    original = persistent._deploy_realm_content_safe
    persistent._deploy_realm_content_safe = _fake_deploy  # type: ignore[assignment]
    try:
        await persistent._refresh_realm_views_for_kingdom_safe(_FakeMemberlessGuild(), "gaia")
    finally:
        persistent._deploy_realm_content_safe = original  # type: ignore[assignment]

    assert deployed == ["gaia"]


async def test_add_kingdom_pings_the_waiting_heirs(service: KingdomsService) -> None:
    """A kingdom created after the enrollments notifies the queued Kings."""
    import kingdoms.mods.kingdoms.kingdom_persistent as persistent
    from kingdoms.mods.kingdoms.kingdom_persistent import KingdomAddKingdomModal

    _wire(service)
    await service.launch([])  # the admin forgot to create the kingdoms
    await service.enroll_king_awaiting_kingdom("king-a", "King A")
    await service.enroll_king_awaiting_kingdom("king-b", "King B")

    pinged: list[str] = []

    async def _fake_claim_request(
        guild: Any, applicant_id: str, locale: str, kingdoms: list[Any]
    ) -> None:
        pinged.append(applicant_id)

    async def _fake_provision(guild: Any, locale: str) -> None:
        return None

    original_claim = persistent._send_king_claim_request
    original_provision = persistent._ensure_realms_after_launch
    persistent._send_king_claim_request = _fake_claim_request  # type: ignore[assignment]
    persistent._ensure_realms_after_launch = _fake_provision  # type: ignore[assignment]
    try:
        modal = KingdomAddKingdomModal("fr")
        modal.kingdom._value = "Aquitaine"
        interaction: Any = persistent_discord_interaction()
        await modal.on_submit(interaction)
    finally:
        persistent._send_king_claim_request = original_claim  # type: ignore[assignment]
        persistent._ensure_realms_after_launch = original_provision  # type: ignore[assignment]

    assert pinged == ["king-a", "king-b"], "every waiting King must receive the claim DM"


def persistent_discord_interaction() -> Any:
    """A minimal interaction for the add-kingdom modal (memberless guild)."""
    from tests.mocks.discord_mock import MockInteraction, MockUser

    interaction = MockInteraction(user=MockUser(id=999), locale="fr")
    interaction._guild = _FakeMemberlessGuild()
    return interaction
