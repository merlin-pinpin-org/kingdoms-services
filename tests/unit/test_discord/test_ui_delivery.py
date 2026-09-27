"""Unit tests for the per-destination rendering policy (#56).

The delivery rules mirror the runtime checks (#55) exactly: in a DM
the recipient is known, so role-gated ``dm_allowed`` components they
lack the role for render **disabled** and never-applicable ones are
omitted; a channel message is a broadcast, so only public components
survive. Both UI systems of ADR-0009 are covered — the declarative
SDK (Components V2 layouts) and the embed-view archetypes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import discord

from kingdoms.discord.ui import (
    PUBLIC_POLICY,
    ButtonBuilder,
    ComponentPolicy,
    ConfirmationView,
    Container,
    MessageDestination,
    Row,
    SelectBuilder,
    Text,
    UILayout,
    annotate_layout,
    component_policies,
    interactive_items,
    render_for,
)

Handler = Callable[[Any], Awaitable[None]]

GATED = "clans:join:confirm"
OPEN_DM = "core:profile:me"
OPEN = "core:rules:open"
GATED_DM_ONLY = "admin:setup:run"


async def _noop(_interaction: Any) -> None:
    return None


def _panel() -> discord.ui.LayoutView:
    gated = ButtonBuilder.primary("Join", GATED, _noop, required_roles=("clan_member",), dm_allowed=True)
    open_dm = ButtonBuilder.success("My profile", OPEN_DM, _noop, dm_allowed=True)
    public = ButtonBuilder.secondary("Rules", OPEN, _noop)
    secret = ButtonBuilder.danger("Run setup", GATED_DM_ONLY, _noop, required_roles=("bot_admin",), dm_allowed=False)
    menu = SelectBuilder.single_select(
        "clans:pick:game",
        "Pick",
        [{"label": "AOE2", "value": "aoe2"}],
        _noop_select,
    )
    return (
        UILayout().add(Container().add(Text("Panel")).add(Row(gated, open_dm, public, secret)).add(Row(menu))).build()
    )


async def _noop_select(_interaction: Any, _values: list[str]) -> None:
    return None


def _serialized(view: discord.ui.LayoutView) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for child in view.children:
        out.append(child.to_component_dict())
    return out


def _buttons_payload(view: discord.ui.LayoutView) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for row_dict in _serialized(view):
        for component in _walk(row_dict):
            if component.get("type") == 2:
                payloads[str(component.get("custom_id"))] = component
    return payloads


def _walk(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for component in payload.get("components", []):
        out.append(component)
        out.extend(_walk(component))
    return out


class TestComponentPolicy:
    def test_open_component_defaults_to_public(self) -> None:
        policy = PUBLIC_POLICY
        assert policy.required_roles == ()
        assert policy.dm_allowed is False
        assert policy.is_public is True

    def test_role_gated_component_is_not_public(self) -> None:
        policy = ComponentPolicy(required_roles=("clan_member",), dm_allowed=True)
        assert policy.is_public is False

    def test_declared_not_public_never_reaches_a_channel(self) -> None:
        policy = ComponentPolicy(public=False)
        assert policy.is_public is False


class TestAnnotateLayout:
    def test_policies_attach_to_built_items(self) -> None:
        view = _panel()
        policies = component_policies(view)
        assert set(policies) == {GATED, OPEN_DM, OPEN, GATED_DM_ONLY, "clans:pick:game"}
        assert policies[GATED].required_roles == ("clan_member",)
        assert policies[GATED].dm_allowed is True
        assert policies[OPEN].dm_allowed is False

    def test_annotate_layout_attaches_policies_post_build(self) -> None:
        gated = ButtonBuilder.primary("Join", GATED, _noop)
        view = UILayout().add(Container().add(Row(gated))).build()
        annotate_layout(view, {GATED: ComponentPolicy(required_roles=("clan_member",), dm_allowed=True)})
        assert component_policies(view)[GATED].required_roles == ("clan_member",)


class TestChannelPolicy:
    def test_channel_keeps_only_public_components(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.CHANNEL)
        payloads = _buttons_payload(view)
        assert set(payloads) == {OPEN, OPEN_DM}
        assert payloads[OPEN]["disabled"] is False

    def test_channel_never_disables_it_removes(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.CHANNEL)
        items = interactive_items(view)
        assert GATED not in items
        assert GATED_DM_ONLY not in items

    def test_select_declared_public_survives_a_channel(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.CHANNEL)
        assert "clans:pick:game" in interactive_items(view)


class TestDMentionPolicy:
    def test_dm_disables_the_missing_role_dm_allowed_component(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.DM, frozenset())
        payloads = _buttons_payload(view)
        assert payloads[GATED]["disabled"] is True
        assert payloads[GATED]["label"] == "Join"

    def test_dm_keeps_the_component_usable_when_the_role_is_held(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.DM, frozenset({"clan_member"}))
        payloads = _buttons_payload(view)
        assert payloads[GATED]["disabled"] is False

    def test_dm_omits_the_never_applicable_components(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.DM, frozenset())
        payloads = _buttons_payload(view)
        assert GATED_DM_ONLY not in payloads
        assert OPEN not in payloads

    def test_dm_keeps_dm_allowed_open_components(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.DM, frozenset())
        payloads = _buttons_payload(view)
        assert payloads[OPEN_DM]["disabled"] is False

    def test_ephemeral_follows_the_dm_rules(self) -> None:
        view = _panel()
        render_for(view, MessageDestination.EPHEMERAL, frozenset())
        payloads = _buttons_payload(view)
        assert payloads[GATED]["disabled"] is True
        assert OPEN not in payloads


class TestViewArchetypes:
    def test_confirmation_view_dm_disables_when_role_missing(self) -> None:
        view = ConfirmationView(mod="clans", required_roles=("clan_member",), dm_allowed=True)
        view.apply_destination(MessageDestination.DM, frozenset())
        assert view.confirm_button.disabled is True
        assert view.cancel_button.disabled is True

    def test_confirmation_view_dm_usable_when_role_held(self) -> None:
        view = ConfirmationView(mod="clans", required_roles=("clan_member",), dm_allowed=True)
        view.apply_destination(MessageDestination.DM, frozenset({"clan_member"}))
        assert view.confirm_button.disabled is False

    def test_confirmation_view_channel_removes_gated_pair(self) -> None:
        view = ConfirmationView(mod="clans", required_roles=("clan_member",), dm_allowed=True)
        view.apply_destination(MessageDestination.CHANNEL)
        assert list(view.children) == []

    def test_open_confirmation_survives_everywhere(self) -> None:
        view = ConfirmationView(mod="core")
        view.apply_destination(MessageDestination.CHANNEL)
        assert len(list(view.children)) == 2
        view_dm = ConfirmationView(mod="core", dm_allowed=True)
        view_dm.apply_destination(MessageDestination.DM, frozenset())
        assert len(list(view_dm.children)) == 2
