"""Postulation v1 unit tests — single ephemeral bubble + role sheets.

The regression this guards against (postulation-v1 spec, 2026-10-13):
the enrollment flow used to stack one new ephemeral message per step
(select -> modal -> summary -> confirmation) below the pinned panel,
and the panel carried no role information. The contract now is:

- ONE ephemeral bubble, created by the Enroll button and edited at
  every step, deleted ~30 s after the final confirmation;
- the panel shows one line per role with a restart-proof
  “View the role” button opening a rights & obligations sheet.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from kingdoms.mods.kingdoms.kingdom_panels import (
    _ApplicationContext,
    _send_summary,
    _strings,
    _summary_view,
    build_apply_panel,
)
from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomRoleInfoButton,
)
from kingdoms.mods.kingdoms.kingdom_persistent import (
    _strings as persistent_strings,
)
from tests.mocks.discord_mock import (
    MockInteraction,
    MockMessage,
    MockTextChannel,
    MockUser,
)

ROLEINFO_TEMPLATE = r"kingdoms:apply:roleinfo:(?P<role>king|lord)"


def _context(
    *,
    locale: str = "fr",
    candidatures: MockTextChannel | None = None,
    anchor: MockInteraction | None = None,
    kingdoms_service: Any = None,
) -> _ApplicationContext:
    return _ApplicationContext(
        locale=locale,
        candidatures_channel=candidatures,
        anchor=anchor,
        kingdoms_service=kingdoms_service,
    )


def _walk_custom_ids(root: Any) -> list[str]:
    ids: list[str] = []
    for child in getattr(root, "walk_children", lambda: [])():
        custom_id = getattr(child, "custom_id", None)
        if custom_id:
            ids.append(custom_id)
    return ids


async def test_apply_panel_carries_the_role_lines_and_sheet_buttons() -> None:
    view = await build_apply_panel("fr")
    flat = "\n".join(
        str(getattr(item, "content", "") or "")
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )
    strings = _strings("fr")
    assert strings["apply_role_king_line"] in flat
    assert strings["apply_role_lord_line"] in flat
    ids = _walk_custom_ids(view)
    assert "kingdoms:apply:open" in ids
    assert "kingdoms:apply:roleinfo:king" in ids
    assert "kingdoms:apply:roleinfo:lord" in ids


async def test_roleinfo_button_rebuilds_from_the_wire_fr_and_en() -> None:
    for locale, key in (("fr", "roleinfo_king_button"), ("en-US", "roleinfo_king_button")):
        interaction = MockInteraction(locale=locale)
        match = re.fullmatch(ROLEINFO_TEMPLATE, "kingdoms:apply:roleinfo:king")
        assert match is not None
        button = await KingdomRoleInfoButton.from_custom_id(interaction, None, match)
        assert button.role == "king"
        assert button.item.label == _strings("fr" if locale == "fr" else "en")[key][:80]
        assert button.custom_id == "kingdoms:apply:roleinfo:king"


async def test_roleinfo_callback_sends_the_matching_sheet_ephemeral() -> None:
    for role, key in (("king", "role_sheet_king"), ("lord", "role_sheet_lord")):
        interaction = MockInteraction(locale="fr")
        label_key = "roleinfo_king_button" if role == "king" else "roleinfo_lord_button"
        button = KingdomRoleInfoButton(role, _strings("fr")[label_key])
        await button.callback(interaction)
        assert interaction.response.ephemeral is True
        assert interaction.response.message is not None
        assert interaction.response.message.content == _strings("fr")[key]


async def test_summary_step_edits_the_same_bubble_instead_of_stacking() -> None:
    interaction = MockInteraction(locale="fr", user=MockUser(name="Drasah"))
    context = _context()
    await _send_summary(
        interaction,
        context,
        is_king=True,
        kingdom_name="",
        queued=False,
        insight="https://www.aoe2insight.com/players/123",
        game_id="12345678",
    )
    # One edit, no second bubble: the flow stays a single message.
    assert interaction.response.sent is True
    assert interaction.response.message is not None
    content = interaction.response.message.content or ""
    assert "Drasah" not in content  # not the confirmation — the review step
    assert "Kingdoms" in content or "candidature" in content.lower() or "Roi" in content
    assert interaction.response.message.view is not None


async def test_invalid_form_opens_its_own_short_bubble() -> None:
    interaction = MockInteraction(locale="fr")
    context = _context()
    await _send_summary(
        interaction,
        context,
        is_king=True,
        kingdom_name="",
        queued=False,
        insight="not-a-url",
        game_id="12345678",
    )
    assert interaction.response.ephemeral is True
    assert interaction.response.message is not None
    assert interaction.response.message.content == _strings("fr")["bad_insight"]


async def test_submit_rewrites_the_bubble_and_edits_it_via_the_anchor() -> None:
    strings = _strings("fr")
    candidatures = MockTextChannel(name="candidatures")
    anchor = MockInteraction(locale="fr")
    context = _context(candidatures=candidatures, anchor=anchor)
    view = _summary_view(
        42,
        "Drasah",
        is_king=True,
        kingdom_name="",
        queued=False,
        insight="https://www.aoe2insight.com/players/123",
        game_id="12345678",
        smurfs=(),
        context=context,
    )
    submit_button = next(b for b in view.children if getattr(b, "custom_id", None) == "kingdoms:apply:submit")
    # The single flow bubble lives on the anchor's followup webhook.
    bubble = MockMessage(content=strings["summary_title"])
    anchor.followup.messages.append(bubble)
    interaction = MockInteraction(locale="fr", user=MockUser(id=42, name="Drasah"))
    interaction.message = bubble  # type: ignore[method-assign]
    await submit_button.callback(interaction)
    # 1. The bubble is rewritten into its sending state first (ack).
    assert interaction.response.message is not None
    assert interaction.response.message.content == strings["apply_sending"]
    # 2. The candidature reached the #candidatures channel.
    assert len(candidatures.messages) == 1
    assert "Drasah" in (candidatures.messages[0].content or "")
    # 3. The final confirmation rewrites the SAME bubble via the anchor.
    assert anchor.followup.messages[0] is bubble
    assert strings["sent"] in (bubble.content or "")


async def test_submit_falls_back_to_a_followup_without_an_anchor() -> None:
    strings = _strings("fr")
    candidatures = MockTextChannel(name="candidatures")
    context = _context(candidatures=candidatures)
    view = _summary_view(
        42,
        "Drasah",
        is_king=True,
        kingdom_name="",
        queued=False,
        insight="https://www.aoe2insight.com/players/123",
        game_id="12345678",
        smurfs=(),
        context=context,
    )
    submit_button = next(b for b in view.children if getattr(b, "custom_id", None) == "kingdoms:apply:submit")
    interaction = MockInteraction(locale="fr", user=MockUser(id=42, name="Drasah"))
    await submit_button.callback(interaction)
    assert len(interaction.followup.messages) == 1
    assert strings["sent"] in (interaction.followup.messages[0].content or "")


async def test_submit_refuses_a_second_pending_candidature() -> None:
    """Drasah's rule (2026-10-18): ONE candidature per person.

    A player whose candidature message still carries its decision
    buttons must not stack a second application.
    """
    strings = _strings("fr")
    candidatures = MockTextChannel(name="candidatures")
    pending_view = discord.ui.View(timeout=None)
    pending_view.add_item(discord.ui.Button(custom_id="kingdoms:candidature:approve"))
    pending = MockMessage(
        content=f"{strings['candidature_title']}\n<@42>", view=pending_view
    )
    candidatures.messages.append(pending)
    context = _context(candidatures=candidatures)
    view = _summary_view(
        42,
        "Drasah",
        is_king=True,
        kingdom_name="",
        queued=False,
        insight="https://www.aoe2insight.com/players/123",
        game_id="12345678",
        smurfs=(),
        context=context,
    )
    submit_button = next(b for b in view.children if getattr(b, "custom_id", None) == "kingdoms:apply:submit")
    interaction = MockInteraction(locale="fr", user=MockUser(id=42, name="Drasah"))
    await submit_button.callback(interaction)
    # No new candidature was stacked, and the refusal is explicit.
    assert len(candidatures.messages) == 1
    assert interaction.followup.messages, "the refusal must reach the player"
    assert strings["apply_already_pending"] in (
        interaction.followup.messages[0].content or ""
    )


class _EnrolledLord:
    id = "42"
    kingdom_id = "k-1"
    left = False


class _EnrolledService:
    async def lords(self) -> list[Any]:
        return [_EnrolledLord()]


async def test_submit_refuses_a_player_already_enrolled() -> None:
    """An enrolled player must not re-apply (one application per person)."""
    strings = _strings("fr")
    candidatures = MockTextChannel(name="candidatures")
    context = _context(candidatures=candidatures, kingdoms_service=_EnrolledService())
    view = _summary_view(
        42,
        "Drasah",
        is_king=True,
        kingdom_name="",
        queued=False,
        insight="https://www.aoe2insight.com/players/123",
        game_id="12345678",
        smurfs=(),
        context=context,
    )
    submit_button = next(b for b in view.children if getattr(b, "custom_id", None) == "kingdoms:apply:submit")
    interaction = MockInteraction(locale="fr", user=MockUser(id=42, name="Drasah"))
    await submit_button.callback(interaction)
    assert len(candidatures.messages) == 0
    assert interaction.followup.messages, "the refusal must reach the player"
    assert strings["apply_already_enrolled"] in (
        interaction.followup.messages[0].content or ""
    )


def test_confirmation_ttl_is_the_short_postulation_one() -> None:
    from kingdoms.mods.kingdoms.kingdom_panels import _CONFIRM_TTL_SECONDS

    assert _CONFIRM_TTL_SECONDS == 30


def test_sheets_exist_in_both_locales_and_stay_under_the_2000_limit() -> None:
    for locale in ("fr", "en"):
        strings = persistent_strings(locale)
        assert "DROITS & OBLIGATIONS" in strings["role_sheet_king"] or "RIGHTS & OBLIGATIONS" in strings[
            "role_sheet_king"
        ]
        assert len(strings["role_sheet_king"]) < 2000
        assert len(strings["role_sheet_lord"]) < 2000
        assert strings["apply_sending"]
        assert strings["sent_ttl"]

async def test_roleinfo_second_sheet_closes_the_first_window() -> None:
    """fenetre-unique-v2 (Drasah 27/10): ONE live ephemeral window per
    player — clicking Seigneur after Roi closes the Roi sheet instead of
    stacking both below the panel (his 27/10 live report)."""
    user = MockUser(id=777, name="blank")
    first = MockInteraction(locale="fr", user=user)
    second = MockInteraction(locale="fr", user=user)

    king_button = KingdomRoleInfoButton("king", _strings("fr")["roleinfo_king_button"])
    lord_button = KingdomRoleInfoButton("lord", _strings("fr")["roleinfo_lord_button"])

    await king_button.callback(first)
    assert first.response.message is not None  # the Roi sheet is open

    await lord_button.callback(second)
    # the first window was closed, the second is the live one
    assert getattr(first, "original_deleted", False) is True
    assert first.response.message is None
    assert second.response.message is not None
    assert second.response.message.content == _strings("fr")["role_sheet_lord"]

    # bounded registry: one entry max per player
    from kingdoms.mods.kingdoms.kingdom_persistent import _EPHEMERAL_WINDOWS

    assert list(_EPHEMERAL_WINDOWS) == ["777"]