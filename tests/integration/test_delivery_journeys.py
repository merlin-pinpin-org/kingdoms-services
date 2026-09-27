"""Delivery journeys on SimCord — kingdoms-services#56.

A DM panel rendered for a member without the clan role carries the
role-gated button **disabled** (visible, teaching the unlock); the
same panel rendered for a channel keeps only the public components.
The rendering mirrors the click-time checks (#55): a channel broadcast
never even carries the gated component, whatever the recipient's
roles.
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord.ui import (
    ButtonBuilder,
    Container,
    MessageDestination,
    Row,
    Text,
    UILayout,
    render_for,
)
from tests.integration.test_permission_journeys import wire_services

GATED = "clans:join:confirm"
OPEN_DM = "core:profile:me"
PUBLIC = "core:rules:open"
COMPONENTS_V2_FLAG = 1 << 15
BOT_ID = 1


def _bot_dm_message(env: Any, alice: Any, view: discord.ui.LayoutView) -> Any:
    """Deliver a bot DM through the SimCord backend (raw component dicts).

    ``send_dm`` on the actor side is user-to-bot only; a bot DM posts
    the serialized view into the user's DM channel with the V2 flag.
    """
    channel = env.backend.get_dm_channel(alice.id)
    return env.backend.create_message(
        channel.id,
        env.bot.user.id if env.bot.user else BOT_ID,
        "",
        components=view.to_components(),
        flags=COMPONENTS_V2_FLAG,
    )


async def _noop(_interaction: discord.Interaction) -> None:
    return None


def _panel() -> discord.ui.LayoutView:
    gated = ButtonBuilder.primary("Join", GATED, _noop, required_roles=("clan_member",), dm_allowed=True)
    profile = ButtonBuilder.success("My profile", OPEN_DM, _noop, dm_allowed=True)
    rules = ButtonBuilder.secondary("Rules", PUBLIC, _noop)
    return UILayout().add(Container().add(Text("Clan recruitment")).add(Row(gated, profile, rules))).build()


def _walk_components(components: list[Any]) -> list[Any]:
    """Collect every component object, nested children included."""
    out: list[Any] = []
    for component in components or []:
        out.append(component)
        out.extend(_walk_components(list(getattr(component, "children", []) or [])))
    return out


def _buttons(message: Any) -> dict[str, Any]:
    """Map every button's custom_id to the button object on the message."""
    payloads: dict[str, Any] = {}
    for component in _walk_components(list(message.components or [])):
        custom_id = getattr(component, "custom_id", None)
        if custom_id and getattr(component, "type", None) == discord.ComponentType.button:
            payloads[str(custom_id)] = component
    return payloads


def _raw_buttons(components: list[dict[str, Any]]) -> dict[str, bool]:
    """Map every button's custom_id to its disabled state (raw dicts)."""
    out: dict[str, bool] = {}

    def walk(node: dict[str, Any]) -> None:
        for component in node.get("components", []):
            custom_id = component.get("custom_id")
            if component.get("type") == 2 and custom_id:
                out[str(custom_id)] = bool(component.get("disabled", False))
            walk(component)

    for row in components:
        walk(row)
    return out


class TestDeliveryJourneys:
    @pytest.fixture
    def simcord_bot(self, kingdoms_bot):  # type: ignore[no-untyped-def]
        bot = kingdoms_bot
        wire_services(bot)

        @bot.tree.command(name="clanspanel2")
        async def clanspanel2(interaction: discord.Interaction) -> None:
            view = _panel()
            render_for(view, MessageDestination.CHANNEL)
            await interaction.response.send_message(view=view)

        return bot

    @pytest.mark.simcord(strict_sync=False)
    async def test_channel_panel_is_public_only(self, simcord_env, simcord_bot) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        guild.create_role("Clan Member")
        alice = guild.add_member(simcord_env.create_user("alice"))
        await alice.slash(channel, "clanspanel2")
        message = channel.last_message
        assert message is not None
        buttons = _buttons(message)
        assert set(buttons) == {PUBLIC, OPEN_DM}, "a channel broadcast carries only public components"
        assert buttons[PUBLIC].disabled is False

    @pytest.mark.simcord(strict_sync=False)
    async def test_dm_panel_disables_the_gated_button(self, simcord_env, simcord_bot) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        guild.create_role("Clan Member")
        alice = guild.add_member(simcord_env.create_user("alice"))
        view = _panel()
        render_for(view, MessageDestination.DM, frozenset())
        raw = _bot_dm_message(simcord_env, alice, view)
        buttons = _raw_buttons(raw.components)
        assert buttons[GATED] is True, "a DM teaches: the gated button is visible but disabled"
        assert buttons[OPEN_DM] is False
        assert PUBLIC not in buttons, "an open non-dm_allowed component is omitted from a DM"

    @pytest.mark.simcord(strict_sync=False)
    async def test_dm_panel_is_usable_once_the_role_is_held(self, simcord_env, simcord_bot) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        guild.create_role("Clan Member")
        alice = guild.add_member(simcord_env.create_user("alice"))
        view = _panel()
        render_for(view, MessageDestination.DM, frozenset({"clan_member"}))
        raw = _bot_dm_message(simcord_env, alice, view)
        buttons = _raw_buttons(raw.components)
        assert buttons[GATED] is False
