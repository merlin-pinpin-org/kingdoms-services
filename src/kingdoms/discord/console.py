"""The ops console: a destructive surface for the test guilds only.

Three layers of protection (developer-mandated \u2014 "que \u00e7a n'arrive
jamais en prod"):

1. **Visibility gate** \u2014 the console section renders in the /admin
   panel only on the env-declared test guilds
   (``KINGDOMS_TEST_GUILD_IDS``) and only for bot admins: nowhere
   else, no flag to flip.
2. **Runtime gate** \u2014 every click re-reads ``KINGDOMS_DEPLOY_ENV``
   and refuses on ``prod``, and re-checks the guard at click time (the
   mandate: seeing the button never implies being allowed to click it).
3. **Permission gate** \u2014 bot admins only (``require_admin``).

Capabilities:

- **clean managed** \u2014 delete only what the bot created: the persisted
  channels (the ``channels`` collection), the declared mod roles
  (``role_mappings``) and the ``bot-admins`` role. Roles are deleted
  but ``bot-admins`` privileges never depended on it (guards read
  BOT_ADMINS first), so a re-run of /admin re-provisions everything.
- **clean nuke** \u2014 delete EVERY channel, category and role of the guild
  (except the @everyone role, undeletable). Behind a triple
  confirmation: three consecutive clicks, each one restating the
  destruction, the third requiring a fresh deliberate click.
- **install modal** \u2014 a multi-select of mods and a multi-select of
  games; selecting a mod auto-installs its declared games (the YAML
  ``games:`` list), uninstalling a game uninstalls every mod that
  needs it. On submit: grants are replaced, channels/roles of
  uninstalled mods/games are deleted, then the newly granted mods are
  provisioned (the same seam as startup).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import discord

from kingdoms.discord.console_guilds import is_test_guild
from kingdoms.discord.guards import require_admin

logger = logging.getLogger("kingdoms.discord.console")

DEPLOY_ENV_VAR = "KINGDOMS_DEPLOY_ENV"

PROD_ENV_NAMES = ("prod", "production")

_NS = "console"


def _deploy_env(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return env.get(DEPLOY_ENV_VAR, "").strip().lower()


def _prod_refusal() -> str:
    return (
        "The console is disabled: this bot runs on a production environment "
        f"({DEPLOY_ENV_VAR}). It is available on test environments only."
    )


def _guild_refusal() -> str:
    return (
        "This guild is not on the console allowlist: only the guild ids declared "
        "in the environment (KINGDOMS_TEST_GUILD_IDS) may use it."
    )


def _wire(bot: discord.Client) -> dict[str, Any]:
    """Collect the bot's provisioned seams (attached at build time)."""
    return {
        "bot": bot,
        "channel_service": getattr(bot, "channel_service", None),
        "mod_roles_service": getattr(bot, "mod_roles_service", None),
        "registry": getattr(bot, "registry", None),
    }


async def _delete_managed_for_guild(guild: discord.Guild) -> tuple[int, int]:
    """Delete the bot-created channels and roles; return (channels, roles).

    Guild-based seam: the Discord callbacks and the headless CLI
    (kingdoms.cli.console_ops) share this one implementation.
    """
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.channel import CHANNELS_COLLECTION
    from kingdoms.core.services.roles import ROLE_MAPPINGS_COLLECTION

    guild_id = str(guild.id)
    database = get_async_database()
    channels_deleted = 0
    roles_deleted = 0
    async for doc in database[CHANNELS_COLLECTION].find({"guild_id": guild_id}):
        channel = guild.get_channel(int(str(doc["channel_id"])))
        if channel is not None:
            try:
                await channel.delete(reason="kingdoms dev console: managed clean")
                channels_deleted += 1
            except Exception:
                logger.warning("CONSOLE channel delete failed (%s)", doc.get("name"), exc_info=True)
        await database[CHANNELS_COLLECTION].delete_one({"_id": doc["_id"]})
    async for mapping in database[ROLE_MAPPINGS_COLLECTION].find({"guild_id": guild_id}):
        role = guild.get_role(int(str(mapping["role_id"])))
        if role is not None:
            try:
                await role.delete(reason="kingdoms dev console: managed clean")
                roles_deleted += 1
            except Exception:
                logger.warning("CONSOLE role delete failed (%s)", mapping.get("role_key"), exc_info=True)
        await database[ROLE_MAPPINGS_COLLECTION].delete_one({"_id": mapping["_id"]})
    bot_admins = discord.utils.get(guild.roles, name="bot-admins")
    if bot_admins is not None:
        try:
            await bot_admins.delete(reason="kingdoms console: managed clean")
            roles_deleted += 1
        except Exception:
            logger.warning("CONSOLE bot-admins role delete failed", exc_info=True)
    return channels_deleted, roles_deleted


async def _delete_managed(interaction: discord.Interaction) -> tuple[int, int]:
    """Delete the bot-created channels and roles of the invoking guild."""
    guild = interaction.guild
    assert guild is not None  # noqa: S101 - guarded by the command check
    return await _delete_managed_for_guild(guild)


async def _nuke_everything_for_guild(guild: discord.Guild) -> tuple[int, int]:
    """Delete every channel and role of the guild; return (channels, roles).

    Guild-based seam: the Discord callbacks and the headless CLI
    (kingdoms.cli.console_ops) share this one implementation. The
    guild is quiesced first (console_quiesce): the running bot's
    self-healing surfaces (live dashboard, boot provisioning) skip the
    guild while the marker is up, so nothing is recreated mid-nuke.
    """
    from kingdoms.discord.console_quiesce import mark_guild_nuked, quiesce_guild, release_guild

    await quiesce_guild(_client_of(guild), guild.id)
    try:
        result = await _nuke_everything_for_guild_inner(guild)
    finally:
        await release_guild(_client_of(guild), guild.id)
    # Durable flag: the guild stays empty \u2014 no self-healing surface may
    # recreate anything until an explicit re-provision (install).
    await mark_guild_nuked(guild.id)
    return result


def _client_of(guild: discord.Guild) -> discord.Client:
    """Recover the client from a guild object (discord.py state seam)."""
    return guild._state._get_client()


async def _nuke_everything_for_guild_inner(guild: discord.Guild) -> tuple[int, int]:
    """Delete every channel and role of the guild (quiesce is held by the caller)."""
    channels_deleted = 0
    roles_deleted = 0
    for channel in list(getattr(guild, "channels", ()) or ()):
        try:
            await channel.delete(reason="kingdoms dev console: nuke")
            channels_deleted += 1
        except Exception:
            logger.warning("CONSOLE nuke channel failed", exc_info=True)
    for role in list(getattr(guild, "roles", ()) or ()):
        if role.is_default() or role.managed:
            continue
        try:
            await role.delete(reason="kingdoms dev console: nuke")
            roles_deleted += 1
        except Exception:
            logger.warning("CONSOLE nuke role failed", exc_info=True)
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.channel import CHANNELS_COLLECTION
    from kingdoms.core.services.roles import ROLE_MAPPINGS_COLLECTION

    guild_id = str(guild.id)
    database = get_async_database()
    await database[CHANNELS_COLLECTION].delete_many({"guild_id": guild_id})
    await database[ROLE_MAPPINGS_COLLECTION].delete_many({"guild_id": guild_id})
    return channels_deleted, roles_deleted


async def _nuke_everything(interaction: discord.Interaction) -> tuple[int, int]:
    """Delete every channel and role of the invoking guild."""
    guild = interaction.guild
    assert guild is not None  # noqa: S101 - guarded by the command check
    return await _nuke_everything_for_guild(guild)


def _mod_games(registry: Any, mod_name: str) -> tuple[str, ...]:
    """Return the games a mod requires (its YAML ``games:`` declaration)."""
    definition = registry.get(mod_name)
    return tuple(getattr(definition, "games", ()) or ()) if definition is not None else ()


def _propagate(
    registry: Any,
    all_games: tuple[str, ...],
    all_mods: tuple[str, ...],
    selected_mods: list[str],
    selected_games: list[str],
) -> tuple[list[str], list[str]]:
    """Apply the mod\u2192game and game\u2192mod propagation rules.

    Selecting a mod installs its declared games; unselecting a game
    unselects every mod that requires it. Idempotent and convergent.
    """
    games = set(selected_games)
    mods = set(selected_mods)
    for mod_name in selected_mods:
        games.update(_mod_games(registry, mod_name))
    surviving = {mod for mod in all_mods if not (required := set(_mod_games(registry, mod))) or required <= games}
    mods &= surviving
    return sorted(games), sorted(mods)


async def _reset_guild_data(guild_id: str) -> dict[str, int]:
    """Wipe one test guild's data from Mongo \u2014 nothing else.

    Every deletion carries a guild scope: ``guild_id`` documents, ids
    prefixed ``<guild_id>:`` or ``...:<guild_id>``. Global content
    (maps, pools, factions, rules, identities, profile stats, user
    settings, audits) is NEVER touched: the reset is isolated to the
    test guild, and the queries below fail closed on scope.
    """
    from kingdoms.core.models.db import get_async_database

    database = get_async_database()
    deleted: dict[str, int] = {}
    guild_scoped: dict[str, Any] = {
        "channels": {"guild_id": guild_id},
        "role_mappings": {"guild_id": guild_id},
        "channel_access_policies": {"_id": {"$regex": f"^{guild_id}:"}},
        "guild_settings": {"_id": guild_id},
        "guild_access": {"_id": f"guild:{guild_id}"},
        "staff": {"_id": {"$regex": f"^staff:{guild_id}:"}},
        "registrations": {"_id": {"$regex": f"^registration:{guild_id}:"}},
        "workflow_states": {"guild_id": guild_id},
        "platform_messages": {"guild_id": guild_id},
        "seasons": {"_id": {"$regex": f"^ladder:{guild_id}:[a-z0-9]+:[0-9]+$"}},
    }
    for collection, query in guild_scoped.items():
        try:
            result = await database[collection].delete_many(query)
            deleted[collection] = int(result.deleted_count)
        except Exception:
            logger.warning("CONSOLE reset failed (%s)", collection, exc_info=True)
            deleted[collection] = -1
    return deleted


async def _uninstall_mod(interaction: discord.Interaction, mod_name: str) -> None:
    """Delete one uninstalled mod's channels and roles, then their mappings."""
    wire = _wire(interaction.client)
    channel_service = wire["channel_service"]
    mod_roles_service = wire["mod_roles_service"]
    registry = wire["registry"]
    guild = interaction.guild
    guild_id = str(interaction.guild_id)
    definition = registry.get(mod_name) if registry is not None else None
    for category_def in getattr(definition, "channel_categories", ()) or ():
        category = f"{mod_name}:{category_def.key}"
        if channel_service is None:
            continue
        stored = await channel_service._db.find_channel(guild_id, category)
        if stored is not None and guild is not None:
            channel = guild.get_channel(int(stored.channel_id))
            if channel is not None:
                try:
                    await channel.delete(reason=f"kingdoms dev console: uninstall {mod_name}")
                except Exception:
                    logger.warning("CONSOLE uninstall channel failed (%s)", category, exc_info=True)
        await channel_service._db.delete_channel(guild_id, category)
        await channel_service._cache.delete_state("channels", f"{guild_id}:{category}")
    if mod_roles_service is None:
        return
    for mapping in await mod_roles_service._db.list_role_mappings(guild_id, mod_name):
        role = guild.get_role(int(mapping.role_id)) if guild is not None else None
        if role is not None:
            try:
                await role.delete(reason=f"kingdoms dev console: uninstall {mod_name}")
            except Exception:
                logger.warning("CONSOLE uninstall role failed (%s)", mapping.role_key, exc_info=True)
        await mod_roles_service._db.delete_role_mapping(guild_id, mod_name, mapping.role_key)
        await mod_roles_service._cache.delete_state("roles:mod", f"{guild_id}:{mod_name}:{mapping.role_key}")


async def _apply_selection(interaction: discord.Interaction, mods: list[str], games: list[str]) -> str:
    """Replace the grants, clean the uninstalled, provision the installed."""
    from kingdoms.discord.wiring import build_guild_access_service

    guild_id = str(interaction.guild_id)
    access = build_guild_access_service()
    if access is None:
        return "Mongo unavailable \u2014 grants cannot be persisted."
    previous_mods = set(await access.enabled_mods(guild_id))
    doc = await access.set_grants(guild_id, games=games, mods=mods)
    removed = previous_mods - set(mods)
    lines = [f"Grants updated: mods `{doc['mods']}`, games `{doc['games']}`."]
    for mod_name in sorted(removed):
        await _uninstall_mod(interaction, mod_name)
    if removed:
        lines.append(f"Uninstalled mods cleaned (channels + roles): {sorted(removed)}.")
    wire = _wire(interaction.client)
    channel_service = wire["channel_service"]
    mod_roles_service = wire["mod_roles_service"]
    provisioned = 0
    if channel_service is not None and mod_roles_service is not None:
        from kingdoms.discord.console_quiesce import clear_guild_nuked

        await clear_guild_nuked(guild_id)
        for mod_name in mods:
            try:
                await channel_service.setup_mod_channels(guild_id, mod_name)
                await mod_roles_service.setup_mod_roles(guild_id, mod_name)
                provisioned += 1
            except Exception:
                logger.warning("CONSOLE provisioning failed (%s)", mod_name, exc_info=True)
    lines.append(f"{provisioned} mod(s) provisioned.")
    return "\n".join(lines)


async def _guard(interaction: discord.Interaction) -> bool:
    """Gate the click-time check: test guild, bot admin, never prod."""
    if _deploy_env() in PROD_ENV_NAMES:
        try:
            await interaction.response.send_message(_prod_refusal(), ephemeral=True)
        except Exception:
            logger.warning("CONSOLE prod refusal answer failed", exc_info=True)
        return False
    if not is_test_guild(interaction.guild_id):
        try:
            await interaction.response.send_message(_guild_refusal(), ephemeral=True)
        except Exception:
            logger.warning("CONSOLE guild refusal answer failed", exc_info=True)
        return False
    bot = interaction.client
    admins = getattr(getattr(bot, "status_service", None), "bot_admins", ())
    roles = getattr(bot, "roles_service", None)
    return await require_admin(
        interaction, admins, roles, denied_message="The console is reserved for bot admins."
    )


def _console_view(guild_id: str | int | None) -> discord.ui.LayoutView:
    """Build the console home with its action buttons.

    The buttons are always rendered (visible), but disabled unless the
    invoking guild is on the env-defined allowlist: an operator sees the
    console exists, yet cannot act on a guild outside the test scope.
    """
    allowed = is_test_guild(guild_id)
    view = discord.ui.LayoutView(timeout=None)
    note = (
        "This guild is on the test allowlist \u2014 actions below apply to "
        "**this guild only**."
        if allowed
        else "This guild is **not** on the test allowlist \u2014 the actions "
        "are disabled (KINGDOMS_TEST_GUILD_IDS decides, env-defined)."
    )
    container: discord.ui.Container[discord.ui.LayoutView] = discord.ui.Container(
        discord.ui.TextDisplay("# \u2699 Console"),
        discord.ui.TextDisplay(
            f"{note}\n**Clean managed** removes only what the bot created; "
            "**Nuke** removes everything (triple confirmation); **Reset data** "
            "wipes this guild's persisted data (isolated \u2014 nothing outside "
            "this guild is touched); **Install** opens the mods/games selection."
        ),
        accent_colour=discord.Colour(0xED4245),
    )
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(DevCleanManagedButton(disabled=not allowed))
    row.add_item(DevNukeButton(disabled=not allowed))
    row.add_item(DevResetDataButton(disabled=not allowed))
    row.add_item(DevInstallButton(disabled=not allowed))
    container.add_item(row)
    view.add_item(container)
    back_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    back_row.add_item(ConsoleBackButton())
    view.add_item(back_row)
    return view


class DevCleanManagedButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:clean:managed",
):
    """Clean only what the bot created (managed clean)."""

    def __init__(self, disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="\U0001f9f9 Clean managed",
                disabled=disabled,
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:clean:managed",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DevCleanManagedButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Clean what the bot created (managed clean)."""
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        channels, roles = await _delete_managed(interaction)
        logger.info(
            "CONSOLE managed clean (guild %s): %d channels, %d roles",
            interaction.guild_id,
            channels,
            roles,
        )
        await interaction.followup.send(
            f"Managed clean done: {channels} channel(s), {roles + 1} role(s) deleted "
            "(\U0001f9f9 the bot-admins role included). Re-run /admin to re-provision.",
            ephemeral=True,
        )


class DevNukeButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:clean:nuke:(?P<step>[0-3])",
):
    """Nuke the whole guild \u2014 triple confirmation."""

    STEPS = (
        ("\U0001f5d1 Nuke the guild", "everything will be deleted"),
        ("\U0001f5d1 Confirm nuke (1/3)", "every channel, category and role will be deleted"),
        ("\u26a0\ufe0f Confirm nuke (2/3)", "this deletes EVERYTHING \u2014 no undo"),
        ("\U0001f6a8 EXECUTE NUKE (3/3)", "last chance \u2014 the guild will be emptied"),
    )

    def __init__(self, step: int = 0, disabled: bool = False) -> None:
        self.step = step
        label, _ = self.STEPS[min(step, 3)]
        super().__init__(
            discord.ui.Button(
                label=label,
                disabled=disabled,
                style=discord.ButtonStyle.danger,
                custom_id=f"{_NS}:clean:nuke:{step}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DevNukeButton:
        """Rebuild the item from the wire."""
        del interaction, item
        return cls(int(match.group("step")))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Advance the nuke confirmation, or execute it at the last step."""
        if not await _guard(interaction):
            return
        if self.step < 3:
            view = discord.ui.LayoutView(timeout=None)
            _, warning = self.STEPS[min(self.step + 1, 3)]
            container: discord.ui.Container[discord.ui.LayoutView] = discord.ui.Container(
                discord.ui.TextDisplay(f"# Nuke confirmation ({self.step + 1}/3)"),
                discord.ui.TextDisplay(f"**{warning}**"),
                accent_colour=discord.Colour(0xED4245),
            )
            row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
            row.add_item(DevNukeButton(self.step + 1))
            container.add_item(row)
            view.add_item(container)
            await interaction.response.edit_message(view=view)
            return
        await interaction.response.defer(ephemeral=True)
        channels, roles = await _nuke_everything(interaction)
        logger.warning(
            "CONSOLE NUKE executed (guild %s): %d channels, %d roles",
            interaction.guild_id,
            channels,
            roles,
        )
        await interaction.followup.send(
            f"Nuke done: {channels} channel(s), {roles} role(s) deleted. "
            "The guild is empty \u2014 re-run /admin to re-provision the bot surfaces.",
            ephemeral=True,
        )


class DevResetDataButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:reset:data",
):
    """Wipe this guild's persisted data \u2014 isolated to the test guild."""

    def __init__(self, disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="\U0001f5d1 Reset data",
                disabled=disabled,
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:reset:data",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DevResetDataButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Wipe this guild's persisted data (isolated)."""
        if not await _guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)
        deleted = await _reset_guild_data(guild_id)
        logger.warning("CONSOLE data reset executed (guild %s): %s", guild_id, deleted)
        summary = "\n".join(f"- `{name}`: {count}" for name, count in sorted(deleted.items()))
        await interaction.followup.send(
            f"Guild data reset done (isolated to guild `{guild_id}`):\n{summary}\n"
            "Global content (maps, factions, identities, profile stats) was NOT touched.",
            ephemeral=True,
        )


class DevInstallButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:install",
):
    """Open the mods/games install modal."""

    def __init__(self, disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="\U0001f4e5 Install mods / games",
                disabled=disabled,
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:install",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DevInstallButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the mods/games install modal."""
        if not await _guard(interaction):
            return
        await interaction.response.send_modal(DevInstallModal())


class DevInstallModal(discord.ui.Modal):
    """The mods/games selection: two multi-selects, propagated at submit."""

    def __init__(self) -> None:
        super().__init__(title="Install mods & games", timeout=600.0)
        from kingdoms.discord.wiring import guild_access_platform

        all_games, all_mods = guild_access_platform()
        self.all_games = tuple(all_games)
        self.all_mods = tuple(all_mods)
        mods_select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"{_NS}:select:mods",
            placeholder="Mods to install\u2026",
            min_values=0,
            max_values=max(1, len(all_mods)),
            options=(
                [discord.SelectOption(label=mod, value=mod) for mod in all_mods]
                or [discord.SelectOption(label="(no mod declared)", value="none")]
            ),
        )
        games_select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=f"{_NS}:select:games",
            placeholder="Games to install\u2026",
            min_values=0,
            max_values=max(1, len(all_games)),
            options=(
                [discord.SelectOption(label=game, value=game) for game in all_games]
                or [discord.SelectOption(label="(no game declared)", value="none")]
            ),
        )
        self.mods_select = mods_select
        self.games_select = games_select
        self.add_item(mods_select)
        self.add_item(games_select)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guard, propagate the selection, apply it."""
        if not await _guard(interaction):
            return
        mods = [v for v in self.mods_select.values if v != "none"]
        games = [v for v in self.games_select.values if v != "none"]
        registry = getattr(interaction.client, "registry", None)
        games, mods = _propagate(registry, self.all_games, self.all_mods, mods, games)
        await interaction.response.defer(ephemeral=True)
        summary = await _apply_selection(interaction, mods, games)
        logger.info("CONSOLE install applied (guild %s): mods=%s games=%s", interaction.guild_id, mods, games)
        await interaction.followup.send(f"Install applied.\n{summary}", ephemeral=True)


PIN_CONSOLE_BUTTON_ID = "admin:pin:button:console"
CONSOLE_BACK_BUTTON_ID = "console:back"


class AdminConsoleButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{PIN_CONSOLE_BUTTON_ID}",
):
    """The /admin panel's console opener (test environments only).

    Rendered by ``build_pin_main_menu`` only on the env-declared test
    guilds (allowlist) and for bot admins; the click re-runs the full
    console guard (bot admin, never prod, allowlist) before opening the
    console view — the mandate: seeing the button never implies being
    allowed to click it.
    """

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="\u2699 Console",
                style=discord.ButtonStyle.danger,
                custom_id=PIN_CONSOLE_BUTTON_ID,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AdminConsoleButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the console view from the /admin panel."""
        if _deploy_env() in PROD_ENV_NAMES or not is_test_guild(interaction.guild_id):
            await interaction.response.edit_message(view=_console_view(interaction.guild_id))
            return
        if not await _guard(interaction):
            return
        await interaction.response.edit_message(view=_console_view(interaction.guild_id))


class ConsoleBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{CONSOLE_BACK_BUTTON_ID}",
):
    """Return from the console view to the /admin main menu."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="\u2190 Admin",
                style=discord.ButtonStyle.secondary,
                custom_id=CONSOLE_BACK_BUTTON_ID,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ConsoleBackButton:
        """Rebuild the item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Return to the /admin main menu."""
        from kingdoms.discord.admin_persistent import _handle_back

        await _handle_back(interaction)
