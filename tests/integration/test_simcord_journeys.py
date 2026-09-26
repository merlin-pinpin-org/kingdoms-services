"""Behavioral (journey) tests on SimCord — kingdoms-services#2, #22, #24.

These tests drive the real discord.py machinery (command tree, dispatch,
converters, interaction lifecycle) against SimCord's in-memory virtual
Discord. Since kingdoms-services#12 the shared ``simcord_bot`` fixture (in
``tests/conftest.py``) builds the **real bot** through the production
factory ``create_bot``: journeys exercise the actual dispatch, command tree
and service wiring. Every user action goes through an actor, every
assertion targets observable state — never a command callback called
directly, never a token, never the network.
"""

from __future__ import annotations

import discord
import pytest
from simcord.asserts import assert_message

CONFIRM_ID = "test:button:confirm"
IS_COMPONENTS_V2 = 1 << 15


def _iter_components(message: discord.Message):
    # type: ignore[no-untyped-def]
    """Yield every component of a message, flattening ActionRow wrappers.

    ``Message.components`` returns wire-model components
    (``discord.components.*``), not UI items (``discord.ui.*``): the same
    names live in both namespaces, so assertions use the wire classes.
    """
    for top in message.components or []:
        if isinstance(top, discord.components.ActionRow):
            yield from top.children
        else:
            yield top


class TestRealBotJourneys:
    """Journeys through the real bot built by create_bot (kingdoms-services#12)."""

    @pytest.mark.simcord(strict_sync=False)
    async def test_status_answers_with_report_embed(self, simcord_env) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(simcord_env.create_user("alice"))

        result = await alice.slash(channel, "status")

        assert result.ephemeral
        assert_message(result.response, embed_title="Kingdoms — Status")


class TestUIPatterns:
    """UI patterns (buttons, modals, Components V2) on the real bot's tree.

    These register ad-hoc commands on the real bot's command tree so the
    dispatch machinery, the tree and the wiring are the production ones;
    only the command bodies are test doubles (ADR-0009 patterns).
    """

    @pytest.fixture
    def simcord_bot(self, kingdoms_bot):  # type: ignore[no-untyped-def]
        tree = kingdoms_bot.tree

        @tree.command(name="panel")
        async def panel(interaction: discord.Interaction) -> None:
            view = discord.ui.View()
            button = discord.ui.Button(label="Confirm", custom_id=CONFIRM_ID)

            async def confirm(inter: discord.Interaction) -> None:
                await inter.response.send_message("confirmed", ephemeral=True)

            button.callback = confirm
            view.add_item(button)
            await interaction.response.send_message("Choose:", view=view)

        @tree.command(name="form")
        async def form(interaction: discord.Interaction) -> None:
            class Form(discord.ui.Modal, title="Form"):
                answer = discord.ui.TextInput(label="Answer")

                async def on_submit(self, inter: discord.Interaction) -> None:
                    await inter.response.send_message(f"got {self.answer.value}")

            await interaction.response.send_modal(Form())

        @tree.command(name="v2panel")
        async def v2panel(interaction: discord.Interaction) -> None:
            layout = discord.ui.LayoutView()
            container = discord.ui.Container(
                discord.ui.TextDisplay("Card A"),
                discord.ui.TextDisplay("Card B"),
            )
            layout.add_item(container)
            await interaction.response.send_message(view=layout)

        return kingdoms_bot

    @pytest.mark.simcord(strict_sync=False)
    async def test_click_fires_callback_through_dispatch(self, simcord_env) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(simcord_env.create_user("alice"))

        await alice.slash(channel, "panel")
        message = channel.last_message
        buttons = [c for c in _iter_components(message) if isinstance(c, discord.components.Button)]
        assert any(b.custom_id == CONFIRM_ID for b in buttons)

        result = await alice.click(message, custom_id=CONFIRM_ID)
        assert result.response.content == "confirmed"
        assert result.ephemeral

    @pytest.mark.simcord(strict_sync=False)
    async def test_submission_fires_on_submit(self, simcord_env) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(simcord_env.create_user("alice"))

        shown = await alice.slash(channel, "form")
        assert shown.modal is not None
        field_id = shown.modal["components"][0]["components"][0]["custom_id"]
        submitted = await alice.submit_modal(shown, {field_id: "hello"})
        assert submitted.response.content == "got hello"

    @pytest.mark.simcord(strict_sync=False)
    async def test_layout_lands_with_v2_flag_and_tree(self, simcord_env) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(simcord_env.create_user("alice"))

        await alice.slash(channel, "v2panel")
        message = channel.last_message
        assert message.flags.value & IS_COMPONENTS_V2, "message must carry IS_COMPONENTS_V2"
        containers = [c for c in _iter_components(message) if isinstance(c, discord.components.Container)]
        assert len(containers) == 1
        texts = [c for c in containers[0].children if isinstance(c, discord.components.TextDisplay)]
        assert [t.content for t in texts] == ["Card A", "Card B"]
