"""Persistent-view journeys on SimCord — kingdoms-services#122.

The §3b contract, proven end to end with a **real restart**: the bot
sends a persistent pager panel, ``restart_bot()`` drains the old
generation and attaches a fresh one (every in-memory view is gone),
and a click on the pre-restart message must still dispatch — discord.py
reconstructs the item through the registered ``DynamicItem`` and the
callback renders the page encoded in the custom_id.
"""

from __future__ import annotations

import discord
import pytest

from kingdoms.discord.ui.persistent import (
    PersistentPagerButton,
    register_page_renderer,
)

PAGE_BUTTON = "ladder:page:3"


class TestPersistentJourneys:
    @pytest.fixture
    def simcord_bot(self, kingdoms_bot):  # type: ignore[no-untyped-def]
        rendered: list[int] = []

        async def render_page(interaction: discord.Interaction, page: int) -> None:
            rendered.append(page)
            await interaction.response.send_message(f"ladder page {page}", ephemeral=True)

        register_page_renderer("ladder", render_page)
        kingdoms_bot._journey_rendered = rendered  # type: ignore[attr-defined]
        return kingdoms_bot

    @pytest.mark.simcord(strict_sync=False)
    async def test_click_after_a_real_restart_reconstructs_the_state(self, simcord_env, simcord_bot) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("leaderboard")
        alice = guild.add_member(simcord_env.create_user("alice"))

        button = PersistentPagerButton("ladder", 3)
        view = discord.ui.View(timeout=None)
        view.add_item(button.item)
        with simcord_env._bot_scope():
            bot_channel = simcord_bot.get_channel(channel.id)
            panel = await bot_channel.send(content="Leaderboard", view=view)  # type: ignore[union-attr]

        await simcord_env.restart_bot()

        result = await alice.click(panel, custom_id=PAGE_BUTTON)
        assert result.ephemeral
        assert "ladder page 3" in result.response.content
        assert simcord_bot._journey_rendered == [3]  # type: ignore[attr-defined]
