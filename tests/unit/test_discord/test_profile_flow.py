"""The profile flow's regression tests: add, remove and rename round-trips.

The three modals of the profile surface answer the session's live findings:
the add modal's select must be Label-wrapped (Discord's modal constraint),
the remove modal reads its multi-select at submission, and every mutation
re-renders the profile view with the new nickname/accounts. These tests pin
the whole flow against fakes (no Mongo, no provider).
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord import home as home_module


class _FakeRegistration:
    """Registration stand-in: bindings in memory, provider always valid."""

    def __init__(self, bindings: list[dict[str, Any]] | None = None) -> None:
        self.bindings = bindings or []

    async def list_bindings(self, user_id: str) -> list[dict[str, Any]]:
        return [b for b in self.bindings if b["user_id"] == user_id]

    async def bind_profile(self, user_id: str, game_key: str, profile_id: str) -> dict[str, Any]:
        for b in self.bindings:
            if b["profile_id"] == profile_id:
                raise ValueError("already bound")
        binding = {
            "_id": f"binding:{game_key}:{user_id}:{profile_id}",
            "user_id": user_id,
            "game_key": game_key,
            "profile_id": profile_id,
            "profile": {"display_name": "Joueur", "aliases": ["Joueur"]},
        }
        self.bindings.append(binding)
        return binding

    async def unlink_profile(self, user_id: str, game_key: str, profile_id: str) -> bool:
        before = len(self.bindings)
        self.bindings = [
            b
            for b in self.bindings
            if not (b["user_id"] == user_id and b["game_key"] == game_key and b["profile_id"] == profile_id)
        ]
        return len(self.bindings) < before


class _FakeIdentity:
    """Identity stand-in: one display name in memory."""

    def __init__(self) -> None:
        self.users: dict[str, str] = {}

    async def get_user(self, user_id: str) -> Any:
        name = self.users.get(user_id)
        if name is None:
            return None

        class _U:
            id = user_id
            display_name = name

        return _U()

    async def get_or_create_user(self, *, platform: str, platform_user_id: str, display_name: str) -> Any:
        self.users[f"{platform}:{platform_user_id}"] = display_name

        class _U:
            id = f"{platform}:{platform_user_id}"

        return _U()

    async def set_display_name(self, user_id: str, display_name: str) -> Any:
        self.users[user_id] = display_name

        class _U:
            id = user_id

        _U.display_name = display_name
        return _U()


class _Followups:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> Any:
        message = type("M", (), {"content": content, "kwargs": kwargs})()
        self.messages.append(message)
        return message


class _Response:
    def __init__(self) -> None:
        self.deferred = False
        self.modals: list[discord.ui.Modal] = []
        self.sent: list[str] = []

    async def defer(self, ephemeral: bool = True, thinking: bool = True) -> None:
        self.deferred = True

    async def send_message(self, message: str, ephemeral: bool = True) -> None:
        self.sent.append(message)

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.modals.append(modal)

    def is_done(self) -> bool:
        return self.deferred or bool(self.sent)


class _Client:
    def __init__(self) -> None:
        self.registration_service: Any = None
        self.identity_service: Any = None
        self.mod_profile_enrichers: dict[str, Any] = {}
        self.messages = None
        self.logs_service = None


class _Interaction:
    """Interaction stand-in: user 42, one guild, defer+followup."""

    guild_id = 123
    user = type("U", (), {"id": 42, "display_name": "TestUser"})()
    guild = type("G", (), {"id": 123, "get_member": lambda self, i: None, "fetch_member": None})()

    def __init__(self) -> None:
        self.client = _Client()
        self.followup = _Followups()
        self.response = _Response()
        self.data: dict[str, Any] = {}


@pytest.mark.asyncio
async def test_add_account_submits_binds_and_refreshes(monkeypatch: Any) -> None:
    """The add modal binds the account then re-renders the profile view."""
    registration = _FakeRegistration()
    interaction = _Interaction()
    interaction.client.registration_service = registration
    modal = home_module.ProfileAddAccountModal(
        [discord.SelectOption(label="Age of Empires II", value="aoe2", default=True)]
    )
    object.__setattr__(modal.game, "_values", ["aoe2"])
    object.__setattr__(modal.profile_id, "_value", "123456")
    await modal.on_submit(interaction)
    assert registration.bindings and registration.bindings[0]["profile_id"] == "123456"
    contents = [str(m.content).lower() for m in interaction.followup.messages]
    assert any("profil" in c or "compte" in c for c in contents), (
        "the profile view must be re-rendered after the add"
    )


@pytest.mark.asyncio
async def test_remove_account_multiselect_unlinks_and_refreshes(monkeypatch: Any) -> None:
    """The remove modal unlinks every selected account, then refreshes."""
    registration = _FakeRegistration(
        [
            {
                "_id": "binding:aoe2:42:111",
                "user_id": "42",
                "game_key": "aoe2",
                "profile_id": "111",
                "profile": {"display_name": "A"},
            },
            {
                "_id": "binding:aoe2:42:222",
                "user_id": "42",
                "game_key": "aoe2",
                "profile_id": "222",
                "profile": {"display_name": "B"},
            },
        ]
    )
    interaction = _Interaction()
    interaction.client.registration_service = registration
    options = [
        discord.SelectOption(label="A", value="aoe2|111"),
        discord.SelectOption(label="B", value="aoe2|222"),
    ]
    modal = home_module.ProfileRemoveAccountModal(options)
    object.__setattr__(modal.accounts, "_values", ["aoe2|111", "aoe2|222"])
    await modal.on_submit(interaction)
    assert registration.bindings == [], "every selected account must be unlinked"
    assert any("retir" in str(m.content).lower() for m in interaction.followup.messages), (
        "the removal confirmation must mention what was removed"
    )
    assert len(interaction.followup.messages) >= 2, "the profile view must follow the confirmation"


@pytest.mark.asyncio
async def test_rename_submits_persists_and_refreshes(monkeypatch: Any) -> None:
    """The rename modal persists the name then re-renders the profile."""
    identity = _FakeIdentity()
    identity.users["discord:42"] = "Old"
    interaction = _Interaction()
    interaction.client.identity_service = identity
    modal = home_module.ProfileRenameModal("Old")
    object.__setattr__(modal.name, "_value", "NewPseudo")
    await modal.on_submit(interaction)
    assert identity.users["discord:42"] == "NewPseudo"
    contents = [str(m.content).lower() for m in interaction.followup.messages]
    assert any("profil" in c or "pseudo" in c for c in contents), (
        "the profile view must be re-rendered after the rename"
    )


@pytest.mark.asyncio
async def test_remove_button_opens_multiselect_modal(monkeypatch: Any) -> None:
    """The remove button answers with the multi-select modal (Label-wrapped)."""
    registration = _FakeRegistration(
        [
            {
                "_id": "binding:aoe2:42:111",
                "user_id": "42",
                "game_key": "aoe2",
                "profile_id": "111",
                "profile": {"display_name": "A"},
            }
        ]
    )
    interaction = _Interaction()
    interaction.client.registration_service = registration
    button = home_module.ProfileRemoveAccountButton()
    await button.callback(interaction)
    assert interaction.response.modals, "the button must open a modal"
    modal = interaction.response.modals[0]
    assert isinstance(modal, home_module.ProfileRemoveAccountModal)
    payload = modal.to_components()
    assert any(c.get("type") == 18 for c in payload), "the select must be Label-wrapped"
    assert modal.accounts.max_values == len(modal.accounts.options)


@pytest.mark.asyncio
async def test_add_button_opens_label_wrapped_modal(monkeypatch: Any) -> None:
    """The add button answers with the game-select modal (Label-wrapped)."""
    interaction = _Interaction()
    interaction.client.registration_service = _FakeRegistration()
    button = home_module.ProfileAddAccountButton()
    await button.callback(interaction)
    assert interaction.response.modals, "the button must open a modal"
    payload = interaction.response.modals[0].to_components()
    assert any(c.get("type") == 18 for c in payload), "the game select must be Label-wrapped"


@pytest.mark.asyncio
async def test_add_failure_names_the_provider(monkeypatch: Any) -> None:
    """A provider failure answers with the provider's name and DMs admins."""
    registration = _FakeRegistration()
    interaction = _Interaction()
    interaction.client.registration_service = registration

    async def _boom(user_id: str, game_key: str, profile_id: str) -> dict[str, Any]:
        raise RuntimeError("profile 848567 is not valid for game aoe2")

    registration.bind_profile = _boom  # type: ignore[method-assign]
    admins_dmed: list[str] = []

    async def _dm(bot: Any, admin_ids: tuple[str, ...], content: str) -> None:
        admins_dmed.append(content)

    monkeypatch.setattr("kingdoms.discord.error_report.dm_admins", _dm)

    class _Status:
        bot_admins = ("111",)

    interaction.client.status_service = _Status()  # type: ignore[attr-defined]
    modal = home_module.ProfileAddAccountModal(
        [discord.SelectOption(label="Age of Empires II", value="aoe2", default=True)]
    )
    object.__setattr__(modal.game, "_values", ["aoe2"])
    object.__setattr__(modal.profile_id, "_value", "848567")
    await modal.on_submit(interaction)
    contents = [str(m.content) for m in interaction.followup.messages]
    assert any("ext-librematch" in c for c in contents), "the error must name the provider"
    assert admins_dmed and "RuntimeError" in admins_dmed[0], "the admins must receive the stack"


@pytest.mark.asyncio
async def test_profile_view_shows_alias_names() -> None:
    """The profile line shows the provider alias, never a bare '?'."""
    registration = _FakeRegistration(
        [
            {
                "_id": "binding:aoe2:42:111",
                "user_id": "42",
                "game_key": "aoe2",
                "profile_id": "848567",
                "profile": {"aliases": ["Merlin Pinpin"]},
            }
        ]
    )
    interaction = _Interaction()
    interaction.client.registration_service = registration
    # the view is a Components V2 LayoutView: walk its text displays
    await home_module._profile_followup(interaction)
    texts: list[str] = []

    def walk(component: Any) -> None:
        content = getattr(component, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for child in getattr(component, "children", None) or ():
            walk(child)

    view = getattr(interaction.followup.messages[-1], "kwargs", {}).get("view")
    assert view is not None, "the profile view must be sent"
    for item in getattr(view, "children", None) or ():
        walk(item)
    assert any("Merlin Pinpin" in t for t in texts), (
        "the alias must be displayed instead of '?'"
    )
