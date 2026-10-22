"""The modder SDK runtime: declarations in, Discord surfaces out.

``bind_mod`` turns a mod's declarations (``EntitySpec`` + ``Field`` +
``ActionSpec`` + ``EntityPanelSpec``) into live Discord surfaces and
keeps them alive:

* **Storage** — one ``EntityStore`` per entity (ids ``<prefix>:<scope>:<slug>``);
* **Cards** — every entity renders on the shared entity-card template
  (title thumbnail, one-prop-per-line, description, media, source,
  id footer) built from its ``EntitySpec``;
* **The Admin rule** — public cards carry the single ⚙️ Admin button;
  the admin ephemeral carries the mod's actions, greyed by
  ``from_states`` (a greyed click answers "unavailable"), permission
  checked at click time;
* **The action pipeline** — confirm-by-title modal for destructive
  actions, prompt modal for input, then the mod's pure handler
  ``(entity, actor, input) -> ActionResult``; the runtime persists
  through the store and re-renders through the sync callback.

The mod never imports discord.py or Mongo — it declares and handles.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from kingdoms.modsdk.models import (
    ActionResult,
    ActionSpec,
    Entity,
    EntityPanelSpec,
    EntitySpec,
    Field,
)
from kingdoms.modsdk.store import EntityStore

logger = logging.getLogger("kingdoms.modsdk.runtime")

_ADMIN_NS = "modsdk:admin"
_ACTION_NS = "modsdk:action"
_PROMPT_NS = "modsdk:prompt"

_RUNTIMES: dict[str, ModBinding] = {}


def _field_text(value: Any) -> str:
    """Render one field value as card text (bools yes/no, rest str)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


class ModBinding:
    """One mod's bound runtime: its specs, its stores, its hooks."""

    def __init__(
        self,
        *,
        database: Any,
        entity_specs: tuple[EntitySpec, ...] = (),
        panels: tuple[EntityPanelSpec, ...] = (),
        label: str = "",
        sync: Any = None,
        tr: Any = None,
        permission: Any = None,
    ) -> None:
        """Bind one mod: the stores, the panels and the platform hooks.

        ``sync`` re-renders an entity's public surfaces after an action
        (``(entity_key, entity_id, bot) -> awaitable``); ``tr`` renders
        a label key in a guild's locale (``(guild_id, key, fallback)``);
        ``permission`` gates the admin surfaces (an async
        ``(interaction) -> bool`` that already answers on denial).
        """
        self.label = label
        self.entity_specs = {spec.key: spec for spec in entity_specs}
        self.panels = panels
        self.stores = {spec.key: EntityStore(database, spec) for spec in entity_specs}
        self._sync = sync
        self._tr = tr
        self._permission = permission

    def store(self, entity_key: str) -> EntityStore:
        """Return one entity's store (KeyError on an undeclared entity)."""
        return self.stores[entity_key]

    def tr(self, guild_id: str, key: str, fallback: str = "") -> str:
        """Render one label in the guild's locale, English fallback."""
        if self._tr is None:
            return fallback
        rendered = self._tr(guild_id, key, fallback)
        return str(rendered) if rendered else fallback

    async def guard(self, interaction: discord.Interaction) -> bool:
        """Check the admin permission; the hook answers on denial."""
        if self._permission is None:
            return True
        return bool(await self._permission(interaction))

    async def resync(self, entity_key: str, entity_id: str, bot: Any) -> None:
        """Re-render one entity's public surfaces after a mutation."""
        if self._sync is None:
            return
        try:
            await self._sync(entity_key, entity_id, bot)
        except Exception:
            logger.warning("MODSDK RESYNC FAILED (%s:%s)", entity_key, entity_id, exc_info=True)

    def actions(self, entity_key: str) -> tuple[ActionSpec, ...]:
        """List one entity's declared actions (panel order, ≤25)."""
        for panel in self.panels:
            if panel.entity == entity_key:
                return panel.admin_actions[:25]
        return ()

    def action(self, entity_key: str, action_key: str) -> ActionSpec | None:
        """Resolve one action spec by key."""
        for candidate in self.actions(entity_key):
            if candidate.key == action_key:
                return candidate
        return None

    def admin_button(self, spec: EntitySpec, entity: Entity, guild_id: str) -> discord.ui.Button[Any]:
        """Build the single ⚙️ Admin button of one public card."""
        return discord.ui.Button(
            label=self.tr(guild_id, "modsdk.admin", "Admin"),
            emoji="⚙",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{_ADMIN_NS}:{spec.key}:{entity['_id']}"[:100],
        )

    def render_card(self, spec: EntitySpec, entity: Entity, guild_id: str) -> discord.ui.LayoutView:
        """Build one entity's card on the shared entity-card template."""
        admin_button_cls: Any = AdminOpenButton
        from kingdoms.discord.ui.entity_cards import EntityCardSpec, build_entity_card

        props = self._prop_lines(spec, entity, guild_id)
        card = EntityCardSpec(
            title=str(entity.get(spec.title_field, entity.get("_id", "?"))),
            entity_id=str(entity["_id"]),
            props=tuple(props),
            description=str(entity.get("description", "")),
            image_url=str(entity.get(spec.image_field, "") or ""),
            icon_url=str(entity.get("icon_url", "") or ""),
            source_url=str(entity.get(spec.source_field, "") or ""),
            action_buttons=(
                lambda: admin_button_cls(
                    spec.key,
                    str(entity["_id"]),
                    binding=self,
                    guild_id=guild_id,
                ),
            ),
        )
        return build_entity_card(card)

    def _prop_lines(self, spec: EntitySpec, entity: Entity, guild_id: str) -> list[str]:
        """Build the card's property lines: one field per line, label first."""
        lines: list[str] = []
        state = str(entity.get("state", "") or "")
        if state:
            lines.append(f"**{self.tr(guild_id, 'modsdk.state', 'State')}** : {state}")
        for field in spec.fields:
            value = entity.get(field.key)
            if value in (None, ""):
                continue
            lines.append(f"**{self.tr(guild_id, field.label_key, field.key)}** : {_field_text(value)}")
        return lines

    def admin_ephemeral(self, spec: EntitySpec, entity: Entity, guild_id: str) -> discord.ui.LayoutView:
        """Build the admin ephemeral: the entity's actions, greyed by state."""
        view = discord.ui.LayoutView(timeout=None)
        state = str(entity.get("state", "") or "")
        lines = [f"## {entity.get(spec.title_field, entity['_id'])!s}"]
        if state:
            lines.append(f"**{self.tr(guild_id, 'modsdk.state', 'State')}** : {state}")
        lines.append(f"-# `{entity['_id']}`")
        view.add_item(discord.ui.Container(discord.ui.TextDisplay("\n".join(lines))))
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        for action in self.actions(spec.key):
            row.add_item(
                AdminActionButton(
                    spec.key,
                    str(entity["_id"]),
                    action.key,
                    binding=self,
                    guild_id=guild_id,
                    disabled=bool(action.from_states) and state not in action.from_states,
                )
            )
        view.add_item(row)
        return view


class AdminOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ADMIN_NS}:(?P<entity_kind>[a-z0-9_]+):(?P<entity_id>.+)",
):
    """The single ⚙️ Admin button of a mod's public card.

    Click: permission check, entity load, admin ephemeral (the
    entity's actions, greyed by state).
    """

    def __init__(self, entity_kind: str, entity_id: str, *, binding: ModBinding, guild_id: str = "") -> None:
        self.entity_kind = entity_kind
        self.entity_id = entity_id
        self._binding = binding
        self._guild_id = guild_id
        super().__init__(
            discord.ui.Button(
                label=binding.tr(guild_id, "modsdk.admin", "Admin"),
                emoji="⚙",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_ADMIN_NS}:{entity_kind}:{entity_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AdminOpenButton:
        """Rebuild the clicked button from the wire."""
        return cls(
            match.group("entity_kind"),
            match.group("entity_id"),
            binding=_resolve(interaction, match.group("entity_kind")),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the entity's admin ephemeral (permission-checked)."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        binding = self._binding
        if not await binding.guard(interaction):
            return
        spec = binding.entity_specs.get(self.entity_kind)
        if spec is None:
            await interaction.edit_original_response(content="Unknown entity type.")
            return
        entity = await binding.store(self.entity_kind).get(self.entity_id)
        if entity is None:
            await interaction.edit_original_response(content="Unknown entity.")
            return
        await interaction.edit_original_response(
            view=binding.admin_ephemeral(spec, entity, str(interaction.guild_id or ""))
        )


class AdminActionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ACTION_NS}:(?P<entity_kind>[a-z0-9_]+):(?P<action_key>[a-z0-9_]+):(?P<entity_id>.+)",
):
    """One action button inside the admin ephemeral."""

    def __init__(
        self,
        entity_kind: str,
        entity_id: str,
        action_key: str,
        *,
        binding: ModBinding,
        guild_id: str = "",
        disabled: bool = False,
    ) -> None:
        self.entity_kind = entity_kind
        self.entity_id = entity_id
        self.action_key = action_key
        self._binding = binding
        self._guild_id = guild_id
        action = binding.action(entity_kind, action_key)
        label_key = action.label_key if action else f"modsdk.action.{action_key}"
        super().__init__(
            discord.ui.Button(
                label=binding.tr(guild_id, label_key, action_key),
                style=discord.ButtonStyle.primary,
                disabled=disabled,
                custom_id=f"{_ACTION_NS}:{entity_kind}:{action_key}:{entity_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AdminActionButton:
        """Rebuild the clicked button from the wire."""
        return cls(
            match.group("entity_kind"),
            match.group("entity_id"),
            match.group("action_key"),
            binding=_resolve(interaction, match.group("entity_kind")),
            guild_id=str(interaction.guild_id or ""),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the pipeline: guard, state gate, then confirm/prompt/handler."""
        binding = self._binding
        action = binding.action(self.entity_kind, self.action_key)
        if action is None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.edit_original_response(content="Unknown action.")
            return
        if not await binding.guard(interaction):
            return
        entity = await binding.store(self.entity_kind).get(self.entity_id)
        if entity is None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.edit_original_response(content="Unknown entity.")
            return
        state = str(entity.get("state", "") or "")
        if action.from_states and state not in action.from_states:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.edit_original_response(
                content=binding.tr(
                    str(interaction.guild_id or ""),
                    "modsdk.unavailable",
                    "Action unavailable in this state.",
                )
            )
            return
        guild_id = str(interaction.guild_id or "")
        if action.confirm:
            await interaction.response.send_modal(
                ConfirmModal(self.entity_kind, self.entity_id, self.action_key, binding, guild_id)
            )
            return
        if action.prompt:
            await interaction.response.send_modal(
                PromptModal(self.entity_kind, self.entity_id, self.action_key, binding, guild_id, action.prompt)
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await run_action(interaction, binding, self.entity_kind, self.entity_id, self.action_key, entity, {})


class ConfirmModal(discord.ui.Modal):
    """The retyped-title confirmation for destructive actions."""

    def __init__(self, entity_kind: str, entity_id: str, action_key: str, binding: ModBinding, guild_id: str) -> None:
        self.entity_kind = entity_kind
        self.entity_id = entity_id
        self.action_key = action_key
        self._binding = binding
        self._guild_id = guild_id
        super().__init__(title=binding.tr(guild_id, "modsdk.confirm_title", "Confirm"))
        self.name: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=binding.tr(guild_id, "modsdk.confirm_label", "Retype the exact name"),
            style=discord.TextStyle.short,
            required=True,
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Compare the retyped title; run the action on a match."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        binding = self._binding
        spec = binding.entity_specs[self.entity_kind]
        entity = await binding.store(self.entity_kind).get(self.entity_id)
        if entity is None:
            await interaction.edit_original_response(content="Unknown entity.")
            return
        expected = str(entity.get(spec.title_field, ""))
        if str(self.name.value).strip().lower() != expected.strip().lower():
            await interaction.edit_original_response(
                content=binding.tr(self._guild_id, "modsdk.confirm_mismatch", "Name mismatch — nothing changed.")
            )
            return
        await run_action(interaction, binding, self.entity_kind, self.entity_id, self.action_key, entity, {})


class PromptModal(discord.ui.Modal, title="Action"):
    """The input modal of a ``prompt`` action (its fields as inputs)."""

    def __init__(
        self,
        entity_kind: str,
        entity_id: str,
        action_key: str,
        binding: ModBinding,
        guild_id: str,
        fields: tuple[Field, ...],
    ) -> None:
        self.entity_kind = entity_kind
        self.entity_id = entity_id
        self.action_key = action_key
        self._binding = binding
        self._guild_id = guild_id
        self.title = binding.tr(guild_id, f"modsdk.action.{action_key}", action_key)
        super().__init__(title=self.title)
        self.inputs: dict[str, discord.ui.TextInput[Any]] = {}
        for field in fields[:5]:
            widget: discord.ui.TextInput[Any] = discord.ui.TextInput(
                label=binding.tr(guild_id, field.label_key, field.key),
                style=(
                    discord.TextStyle.paragraph
                    if field.kind in ("long_text", "text_area")
                    else discord.TextStyle.short
                ),
                required=field.required,
            )
            self.inputs[field.key] = widget
            self.add_item(widget)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Collect the inputs; run the action with them."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        entity = await self._binding.store(self.entity_kind).get(self.entity_id)
        if entity is None:
            await interaction.edit_original_response(content="Unknown entity.")
            return
        values = {key: str(widget.value) for key, widget in self.inputs.items()}
        await run_action(
            interaction, self._binding, self.entity_kind, self.entity_id, self.action_key, entity, values
        )


async def run_action(
    interaction: discord.Interaction,
    binding: ModBinding,
    entity_kind: str,
    entity_id: str,
    action_key: str,
    entity: Entity,
    values: dict[str, Any],
) -> None:
    """Run the mod's handler, persist, re-render, answer the click."""
    action = binding.action(entity_kind, action_key)
    if action is None or action.handler is None:
        await interaction.edit_original_response(content="Unknown action.")
        return
    try:
        result = await action.handler(entity, interaction.user, values)
    except Exception:
        logger.exception("MODSDK ACTION CRASHED (%s:%s)", entity_kind, action_key)
        await interaction.edit_original_response(content="The action crashed — see the logs.")
        return
    if not isinstance(result, ActionResult):
        result = ActionResult(entity=result if isinstance(result, dict) else None)
    if result.entity is not None or result.new_state:
        await binding.store(entity_kind).update(entity_id, dict(result.entity or {}), new_state=result.new_state)
    await binding.resync(entity_kind, entity_id, interaction.client)
    message = result.message or binding.tr(str(interaction.guild_id or ""), "modsdk.action_done", "Done.")
    await interaction.edit_original_response(content=message)


def _resolve(interaction: discord.Interaction, entity_kind: str) -> ModBinding:
    """Resolve the click's binding: the client registry, then the module one."""
    registry = getattr(interaction.client, "modsdk_bindings", None)
    if isinstance(registry, dict):
        for candidate in registry.values():
            if not isinstance(candidate, ModBinding):
                continue
            if entity_kind in candidate.entity_specs:
                return candidate
        if registry:
            raise KeyError(f"No modsdk binding owns entity '{entity_kind}'.")
    for binding in _RUNTIMES.values():
        if entity_kind in binding.entity_specs:
            binding_: ModBinding = binding
            return binding_
    raise KeyError(f"No modsdk binding owns entity '{entity_kind}'.")


def bind_mod(
    mod_key: str,
    *,
    database: Any,
    entity_specs: tuple[EntitySpec, ...],
    panels: tuple[EntityPanelSpec, ...] = (),
    sync: Any = None,
    tr: Any = None,
    permission: Any = None,
) -> ModBinding:
    """Bind one mod's declarations to the platform (idempotent, last wins).

    Attach the binding to the client for the persistent items:
    ``bot.modsdk_bindings[mod_key] = binding`` (``register_dynamic_items``
    wires the classes once at boot).
    """
    binding = ModBinding(
        database=database,
        entity_specs=entity_specs,
        panels=panels,
        label=mod_key,
        sync=sync,
        tr=tr,
        permission=permission,
    )
    _RUNTIMES[mod_key] = binding
    return binding


def bindings() -> dict[str, ModBinding]:
    """List the registered mod bindings."""
    return _RUNTIMES


def register_dynamic_items(client: discord.Client) -> None:
    """Register the runtime's persistent DynamicItems on the client."""
    client.add_dynamic_items(AdminOpenButton)
    client.add_dynamic_items(AdminActionButton)


def card_action_row(
    binding: ModBinding,
    spec: EntitySpec,
    entity: Entity,
    guild_id: str,
) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Build the card's action row: the single ⚙️ Admin button."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(binding.admin_button(spec, entity, guild_id))
    return row


def attach_admin_button(
    view: discord.ui.LayoutView,
    binding: ModBinding,
    spec: EntitySpec,
    entity: Entity,
    guild_id: str,
) -> discord.ui.LayoutView:
    """Append the ⚙️ Admin action row to one rendered card."""
    view.add_item(card_action_row(binding, spec, entity, guild_id))
    return view


__all__ = [
    "AdminActionButton",
    "AdminOpenButton",
    "ConfirmModal",
    "ModBinding",
    "PromptModal",
    "attach_admin_button",
    "bind_mod",
    "bindings",
    "card_action_row",
    "register_dynamic_items",
    "run_action",
]
