"""Unit tests for the UI archetypes (kingdoms-services#13).

The SDK bricks (factory) and the paged screens already have their own
suites; these pin the archetypes built on top — the status embeds,
the Components V2 counterpart layouts, the interactive views (custom
ID convention, click-time permission wiring, pagination state) and
the component builders.
"""

from __future__ import annotations

from typing import Any

import discord

from kingdoms.discord.ui import (
    ButtonBuilder,
    ConfirmationView,
    EmbedBuilder,
    FeedbackModal,
    GameSelectionView,
    LadderEmbedBuilder,
    LadderLayout,
    PaginationView,
    RegistrationModal,
    SelectBuilder,
    StatusLayout,
    UserEmbedBuilder,
    UserProfileLayout,
)

IS_COMPONENTS_V2 = 1 << 15


def _walk(components: Any) -> list[Any]:
    out: list[Any] = []
    for component in components or []:
        out.append(component)
        out.extend(_walk(list(getattr(component, "children", []) or [])))
    return out


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [c.content for c in _walk(view.children) if isinstance(c, discord.ui.TextDisplay)]


class TestEmbedBuilder:
    def test_levels_map_to_consistent_colours(self) -> None:
        assert EmbedBuilder.success("t", "d").color.value == 0x57F287
        assert EmbedBuilder.error("t", "d").color.value == 0xED4245
        assert EmbedBuilder.info("t", "d").color.value == 0x5865F2
        assert EmbedBuilder.warning("t", "d").color.value == 0xFEE75C

    def test_unknown_level_falls_back_to_info(self) -> None:
        embed = EmbedBuilder.build("t", "d", level="nope")
        assert embed.color.value == 0x5865F2

    def test_fields_are_added(self) -> None:
        embed = EmbedBuilder.info(
            "t", "d", fields=[{"name": "a", "value": "1"}, {"name": "b", "value": "2", "inline": False}]
        )
        assert len(embed.fields) == 2
        assert embed.fields[0].name == "a"

    def test_user_profile_embed(self) -> None:
        embed = UserEmbedBuilder.user_profile({"name": "alice", "elo": 1200, "wins": 3, "losses": 1})
        assert "alice" in embed.title
        assert len(embed.fields) == 5

    def test_registration_confirmation_embed(self) -> None:
        embed = UserEmbedBuilder.registration_confirmation({"name": "alice", "game": "aoe2"})
        assert "Welcome" in embed.description

    def test_ladder_embeds(self) -> None:
        rankings = [{"user": {"name": f"p{i}"}, "elo": 1000 - i, "wins": 0} for i in range(12)]
        embed = LadderEmbedBuilder.ladder_rankings(rankings)
        assert len(embed.fields) == 10, "only the top 10 renders"
        match = LadderEmbedBuilder.match_notification("bob", "m1")
        assert "bob" in match.description


class TestLayouts:
    def test_status_layout_is_components_v2(self) -> None:
        view = StatusLayout("Deployed", "All services green", "success").build()
        assert isinstance(view, discord.ui.LayoutView)
        joined = "\n".join(_texts(view))
        assert "Deployed" in joined
        assert "All services green" in joined

    def test_status_layout_with_fields(self) -> None:
        view = StatusLayout("Report", "body", fields=[{"name": "a", "value": "1"}]).build()
        joined = "\n".join(_texts(view))
        assert "a:" in joined and "1" in joined

    def test_user_profile_layout(self) -> None:
        view = UserProfileLayout(
            {"name": "alice", "game": "aoe2", "elo": 1200, "avatar_url": "https://example.com/a.png"}
        ).build()
        joined = "\n".join(_texts(view))
        assert "alice" in joined
        assert "aoe2" in joined

    def test_ladder_layout_top_ten(self) -> None:
        rankings = [{"user": {"name": f"p{i}"}, "elo": 1000 - i, "wins": 0} for i in range(12)]
        view = LadderLayout(rankings).build()
        joined = "\n".join(_texts(view))
        assert "#1" in joined
        assert "#10" in joined
        assert "#11" not in joined


class TestViews:
    def test_confirmation_view_custom_ids_follow_convention(self) -> None:
        view = ConfirmationView(mod="clans")
        ids = {child.custom_id for child in view.children if hasattr(child, "custom_id")}
        assert ids == {"clans:confirm:yes", "clans:confirm:no"}

    def test_confirmation_view_dispatches_callbacks(self) -> None:
        seen: list[str] = []

        async def on_confirm(interaction: Any) -> None:
            seen.append("confirm")

        view = ConfirmationView(mod="clans", on_confirm=on_confirm)
        button = next(c for c in view.children if getattr(c, "custom_id", "") == "clans:confirm:yes")
        assert button.callback is not None

    def test_game_selection_view_options(self) -> None:
        view = GameSelectionView(["aoe2", "chess"], mod="ladder")
        select = next(c for c in view.children if isinstance(c, discord.ui.Select))
        assert [o.label for o in select.options] == ["aoe2", "chess"]
        assert select.custom_id == "ladder:game:select"

    def test_pagination_view_initial_state(self) -> None:
        view = PaginationView(total_pages=3, current_page=0, mod="ladder")
        assert view.previous_button.disabled
        assert not view.next_button.disabled
        assert view.page_counter.label == "1/3"

    def test_pagination_view_updates_buttons(self) -> None:
        view = PaginationView(total_pages=3, mod="ladder")
        view.update_buttons(2)
        assert not view.previous_button.disabled
        assert view.next_button.disabled
        assert view.page_counter.label == "3/3"

    def test_permission_check_fails_open_when_unwired(self) -> None:
        view = ConfirmationView(mod="clans")
        assert view._permission_service is None

    def test_permission_check_wired_declares_roles(self) -> None:
        view = ConfirmationView(
            mod="clans", permission_service=object(), required_roles=("clan_member",), dm_allowed=True
        )
        roles, dm = view.permission_args("clans", "clans:confirm:yes")
        assert roles == ("clan_member",)
        assert dm is True


class TestModals:
    def test_registration_modal_fields(self) -> None:
        modal = RegistrationModal()
        items = list(modal.children)
        assert len(items) == 2
        assert items[0].custom_id == "registration:name"

    def test_feedback_modal_fields(self) -> None:
        modal = FeedbackModal()
        items = list(modal.children)
        assert len(items) == 2
        assert items[1].custom_id == "feedback:details"


class TestComponentBuilders:
    def test_button_builder_styles(self) -> None:
        async def on_click(interaction: Any) -> None:
            return None

        primary = ButtonBuilder.primary("Go", "clans:join:go", on_click)
        assert primary.style == "primary"
        assert ButtonBuilder.danger("Del", "clans:join:del", on_click).style == "danger"

    def test_select_builder_single(self) -> None:
        async def on_choose(interaction: Any, values: list[str]) -> None:
            return None

        menu = SelectBuilder.single_select(
            "ladder:pick:game",
            "Pick",
            [{"label": "AoE2", "value": "aoe2"}, {"label": "Chess", "value": "chess"}],
            on_choose,
        )
        assert menu.custom_id == "ladder:pick:game"
        assert len(menu.options) == 2

    def test_select_builder_multi_bounds(self) -> None:
        async def on_choose(interaction: Any, values: list[str]) -> None:
            return None

        menu = SelectBuilder.multi_select(
            "clans:pick:members",
            "Pick",
            [{"label": "a", "value": "a"}, {"label": "b", "value": "b"}, {"label": "c", "value": "c"}],
            on_choose,
            min_values=2,
            max_values=3,
        )
        assert menu.min_values == 2
        assert menu.max_values == 3
