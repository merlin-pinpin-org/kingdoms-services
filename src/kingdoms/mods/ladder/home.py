"""The ladder's home view: the mod's front door inside the guild home.

The core home routes ``mod:ladder`` here; the mod decides what its
home shows. The ladder's home is **button-only** (the platform rule:
views, never commands to type): every action of the /ladder group has
its persistent button, served by DynamicItems in the
``ladder:home:`` namespace — one custom_id, one dispatch path,
restart-proof, click-time guards included.

Every action runs the same domain code as the slash commands
(`LadderSurface` / the core membership): the buttons are a second
entry point, never a second implementation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.ladder.home")

_NS = "ladder:home"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)


def _wiring_and_ladder_id(interaction: discord.Interaction) -> tuple[Any, str]:
    """Resolve the wiring and the guild's ladder id (empty when absent)."""
    from kingdoms.mods.ladder.commands import build_ladder_wiring

    wiring = build_ladder_wiring(bot=interaction.client)
    ladder_id = str(getattr(interaction.client, "_ladder_id", "") or "")
    return wiring, ladder_id


async def _run_membership(interaction: discord.Interaction, action: str) -> None:
    """Register or unregister the clicker through the core membership."""
    from kingdoms.mods.ladder.membership import build_ladder_membership

    wiring, ladder_id = _wiring_and_ladder_id(interaction)
    if wiring is None or not ladder_id:
        await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
        return
    membership = build_ladder_membership(wiring.service, ladder_id, wiring.season_service, wiring.season_roles)
    guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
    user_id = str(interaction.user.id)
    try:
        if action == "register":
            result = await membership.register(guild_id, user_id, interaction.user.display_name)
        else:
            result = await membership.unregister(guild_id, user_id)
    except Exception:
        logger.warning("MEMBERSHIP %s failed (button)", action, exc_info=True)
        await interaction.response.send_message(
            "Action impossible — les inscriptions sont peut-être fermées.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(result.summary, ephemeral=True)


class LadderJoinButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:join",
):
    """Join the queue (same precondition and surface as /ladder join)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Rejoindre la file",
                emoji="⚔️",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:join",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderJoinButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the join flow (profile precondition, then queue)."""
        from kingdoms.mods.ladder.commands import _join_command

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        await _join_command(interaction, wiring.service, ladder_id)


class LadderLeaveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:leave",
):
    """Leave the queue (same surface as /ladder leave)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Quitter la file",
                custom_id=f"{_NS}:leave",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderLeaveButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Leave the queue through the ladder surface."""
        from kingdoms.mods.ladder.surface import ACTION_LEAVE_QUEUE, LadderSurface

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        surface = LadderSurface(wiring.service)
        result = await surface.execute(
            ACTION_LEAVE_QUEUE, ladder_id, str(interaction.user.id), now=_now_ms()
        )
        if result.ok:
            await interaction.response.send_message("Tu as quitté la file.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Impossible : {result.reason}", ephemeral=True)


class LadderQueueButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:queue",
):
    """Show the current queue (same view as /ladder queue)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="File d'attente",
                custom_id=f"{_NS}:queue",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderQueueButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the current queue."""
        from kingdoms.mods.ladder.surface import LadderSurface

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        surface = LadderSurface(wiring.service)
        rows = await surface.queue_view(ladder_id, now=_now_ms())
        if not rows:
            body = "La file est vide."
        else:
            body = "\n".join(
                f"{i + 1}. <@{row.user_id}> — {row.rating} (en attente {row.wait_seconds // 60}m)"
                for i, row in enumerate(rows)
            )
        await interaction.response.send_message(body, ephemeral=True)


class LadderLeaderboardButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:leaderboard",
):
    """Show the standings (same view as /ladder leaderboard)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Classement",
                custom_id=f"{_NS}:leaderboard",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderLeaderboardButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the standings."""
        from kingdoms.mods.ladder.surface import LadderSurface

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        surface = LadderSurface(wiring.service)
        rows = await surface.leaderboard_view(ladder_id)
        if not rows:
            body = "Aucun joueur pour l'instant."
        else:
            body = "\n".join(
                f"{row.rank}. <@{row.user_id}> — {row.rating} ({row.wins}W/{row.losses}L)"
                for row in rows[:10]
            )
        await interaction.response.send_message(body, ephemeral=True)


class LadderInfoButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:info",
):
    """Open the mod's info page (project, repo, maintainers, join us)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="À propos",
                emoji="\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16}",
                custom_id=f"{_NS}:info",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderInfoButton:
        """Rebuild the stateless item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the project's info page (join-us entry point)."""
        await interaction.response.send_message(view=build_info_view(), ephemeral=True)


class LadderHomeBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"home:open:home",
):
    """The back-to-home button every mod view carries."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="⬅️ Home",
                custom_id="home:open:home",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderHomeBackButton:
        """Rebuild the stateless item from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Re-open the core home menu (ephemeral)."""
        from kingdoms.discord.home import open_home_menu

        await open_home_menu(interaction)


async def _ladder_state(interaction: discord.Interaction) -> tuple[bool, bool] | None:
    """Resolve (enrollments_open, queue_paused); None when unresolvable."""
    wiring, ladder_id = _wiring_and_ladder_id(interaction)
    if wiring is None or not ladder_id:
        return None
    try:
        ladder = await wiring.service.get_ladder(ladder_id)
    except Exception:
        return None
    if ladder is None:
        return None
    return bool(ladder.enrollments_open), bool(ladder.queue_paused)


class LadderRegisterButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:register",
):
    """Register on the ladder (core membership, role synced)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="S'inscrire",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:register",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderRegisterButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Register the clicker through the core membership."""
        await _run_membership(interaction, "register")


class LadderUnregisterButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:unregister",
):
    """Unregister from the ladder (core membership, role synced)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Se désinscrire",
                custom_id=f"{_NS}:unregister",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderUnregisterButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Unregister the clicker through the core membership."""
        await _run_membership(interaction, "unregister")




class LadderPreferencesButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:prefs",
):
    """Show the player's map preferences (fav/ban selects, quota-aware)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Mes préférences",
                emoji="🗺️",
                custom_id=f"{_NS}:prefs",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderPreferencesButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the fav/ban selects over the active pool's maps."""
        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        view = await build_preferences_view(interaction, wiring, ladder_id)
        await interaction.response.send_message(view=view, ephemeral=True)


class LadderPreferenceSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:prefs:(?P<kind>fav|ban)",
):
    """The fav/ban select (the kind rides the custom_id; quota enforced)."""

    def __init__(self, kind: str, options: list[discord.SelectOption]) -> None:
        self.kind = kind
        max_values = max(1, len(options)) if options else 1
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:prefs:{kind}",
                placeholder="Tes maps favorites..." if kind == "fav" else "Tes maps bannies...",
                min_values=0,
                max_values=max_values,
                options=options or [discord.SelectOption(label="—", value="none")],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderPreferenceSelect:
        """Rebuild the item; the options come from the active pool."""
        kind = str(match["kind"])
        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        options = await _pool_options(wiring, ladder_id) if wiring and ladder_id else []
        return cls(kind, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Save the selection through the ladder service (quota-capped)."""
        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        player = await wiring.service.get_player(ladder_id, str(interaction.user.id))
        if player is None:
            await interaction.response.send_message("Inscris-toi au ladder d'abord.", ephemeral=True)
            return
        selected = [str(v) for v in self.item.values if v != "none"]
        favs = selected if self.kind == "fav" else list(player.fav_map_ids)
        bans = selected if self.kind == "ban" else list(player.ban_map_ids)
        try:
            await wiring.service.set_preferences(ladder_id, str(interaction.user.id), tuple(favs), tuple(bans))
        except Exception:
            logger.warning("PREFERENCES SAVE failed", exc_info=True)
            await interaction.response.send_message(
                "Sauvegarde impossible (favs et bans doivent être disjoints).", ephemeral=True
            )
            return
        await interaction.response.send_message("Préférences enregistrées.", ephemeral=True)


async def _pool_options(wiring: Any, ladder_id: str) -> list[discord.SelectOption]:
    """Build the pool's map options for the selects (empty list when no pool)."""
    ladder = await wiring.service.get_ladder(ladder_id)
    if ladder is None or ladder.active_map_pool_id is None:
        return []
    pool = await wiring.game_data.get_map_pool(ladder.active_map_pool_id)
    if pool is None:
        return []
    options = []
    for map_id in await wiring.game_data.resolve_pool_map_ids(pool):
        entry = await wiring.game_data.get_map(map_id)
        options.append(discord.SelectOption(label=entry.name if entry else map_id, value=map_id))
    return options[:25]


async def build_preferences_view(
    interaction: discord.Interaction, wiring: Any, ladder_id: str
) -> discord.ui.LayoutView:
    """Build the preferences view: two selects + the quota banner."""
    del interaction
    caps = await wiring.service.preference_caps(ladder_id)
    options = await _pool_options(wiring, ladder_id)
    if not options:
        return _notice_view("Aucun pool actif — les préférences attendent un map pool.")
    return _notice_view(f"⭐ **{caps[0]}** favoris · ⛔ **{caps[1]}** bans autorisés.", options)


def _notice_view(text: str, options: list[discord.SelectOption] | None = None) -> discord.ui.LayoutView:
    """Build a small ephemeral notice view, selects attached when provided."""
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay(f"## 🗺️ Mes préférences\n{text}")]
    if options:
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(LadderPreferenceSelect("fav", options))
        view.add_item(discord.ui.Container(*blocks))
        row2: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row2.add_item(LadderPreferenceSelect("ban", options))
        view.add_item(row)
        view.add_item(row2)
    else:
        view.add_item(discord.ui.Container(*blocks))
    return view


async def _seasons_details(wiring: Any, ladder_id: str) -> str:
    """Build the banner's seasons block: the ladder's seasons with their ids."""
    if wiring is None or wiring.season_service is None or not ladder_id:
        return ""
    try:
        seasons = await wiring.season_service.list_seasons(ladder_id)
    except Exception:
        return ""
    lines = []
    for season in seasons[-5:]:
        marker = "actif" if season.state == "active" else season.state
        lines.append(f"- {season.name} ({marker}) - `{season.id}`")
    if not lines:
        return ""
    return "**Saisons**\n" + "\n".join(lines)


def build_ladder_menu_layout(
    user_id: str | None = None,
    in_queue: bool | None = None,
    enrollments_open: bool = True,
    queue_paused: bool = False,
    season_details: str = "",
    footer_id: str = "",
) -> discord.ui.LayoutView:
    """Build the ladder home layout (shared by the ephemeral view and the pin).

    The pinned salon menu is public and stateless: it shows both queue
    actions. The ephemeral answer is personal: it resolves the clicker's
    queue state and shows **one** of join/leave (#a1fb4f49) — never both.
    Closed enrollments disable the register and join actions; a paused
    queue disables join/leave — the disabled state is visible, not silent.
    """
    view = discord.ui.LayoutView(timeout=None)
    join = LadderJoinButton()
    join.item.disabled = queue_paused
    register = LadderRegisterButton()
    register.item.disabled = not enrollments_open
    main_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    main_row.add_item(register)
    if in_queue is None:
        leave = LadderLeaveButton()
        leave.item.disabled = queue_paused
        main_row.add_item(join)
        main_row.add_item(leave)
    elif in_queue:
        leave = LadderLeaveButton()
        leave.item.disabled = queue_paused
        main_row.add_item(leave)
    else:
        main_row.add_item(join)
    info_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    info_row.add_item(LadderQueueButton())
    info_row.add_item(LadderLeaderboardButton())
    info_row.add_item(LadderPreferencesButton())
    info_row.add_item(LadderInfoButton())
    nav_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    nav_row.add_item(LadderHomeBackButton())
    status_bits = []
    if not enrollments_open:
        status_bits.append("⚠️ Inscriptions fermées")
    if queue_paused:
        status_bits.append("⏸️ File en pause")
    banner = (
        "## 🏺 Ladder\n"
        "Ladder saisonnier 1v1 (AoE2) — tout se fait ici, sans commande."
    )
    if season_details:
        banner += "\n\n" + season_details
    if status_bits:
        banner += "\n" + " · ".join(status_bits)
    container_items: list[Any] = [
        discord.ui.TextDisplay(banner),
        main_row,
        info_row,
        discord.ui.Separator(),
        nav_row,
    ]
    if footer_id:
        container_items.append(discord.ui.TextDisplay(f"-# {footer_id}"))
    view.add_item(discord.ui.Container(*container_items))
    return view


def build_info_view() -> discord.ui.LayoutView:
    """Render the project's info page: what Kingdoms is, where it lives, who runs it."""
    from kingdoms.discord.staff import StaffApplyButton

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                "## 🏓 Kingdoms\n"
                "Une plateforme communautaire de ladders et d'événements "
                "autour de vos jeux préférés (AoE2 aujourd'hui).\n\n"
                "- **Repo** : [kingdoms-services](https://github.com/merlin-pinpin-org/kingdoms-services)\n"
                "- **Infra** : [kingdoms-infra](https://github.com/merlin-pinpin-org/kingdoms-infra)\n"
                "- **Maintainers** : l'équipe Kingdoms\n\n"
                "Envie de contribuer — staff, idées, tests ? Clique ci-dessous."
            ),
        )
    )
    staff_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    staff_row.add_item(StaffApplyButton("ladder", label="Rejoindre le staff"))
    view.add_item(staff_row)
    nav_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    nav_row.add_item(LadderHomeBackButton())
    view.add_item(nav_row)
    return view


async def build_ladder_home_view(interaction: discord.Interaction) -> None:
    """Answer the home's mod:ladder click with the button-only ladder home."""
    in_queue = await _clicker_in_queue(interaction)
    state = await _ladder_state(interaction)
    enrollments_open, queue_paused = state if state else (True, False)
    footer = await _ladder_footer(interaction)
    wiring, ladder_id = _wiring_and_ladder_id(interaction)
    seasons_block = await _seasons_details(wiring, ladder_id)
    await interaction.response.send_message(
        view=build_ladder_menu_layout(
            user_id=str(interaction.user.id),
            in_queue=in_queue,
            enrollments_open=enrollments_open,
            queue_paused=queue_paused,
            season_details=seasons_block,
            footer_id=footer,
        ),
        ephemeral=True,
    )


async def _ladder_footer(interaction: discord.Interaction) -> str:
    """Visible ids footer: the interaction guild's ladder id and active season id."""
    wiring, ladder_id = _wiring_and_ladder_id(interaction)
    if wiring is None or not ladder_id:
        return ""
    footer = ladder_id
    if wiring.season_service is not None:
        try:
            season = await wiring.season_service.get_active_season(ladder_id)
            if season is not None:
                footer = f"{footer} · {season.id}"
        except Exception:
            logger.debug("ladder footer: season id unavailable", exc_info=True)
    return footer


async def _clicker_in_queue(interaction: discord.Interaction) -> bool | None:
    """Resolve whether the clicker sits in the queue (None: unresolvable)."""
    wiring, ladder_id = _wiring_and_ladder_id(interaction)
    if wiring is None or not ladder_id:
        return None
    try:
        player = await wiring.service.get_player(ladder_id, str(interaction.user.id))
    except Exception:
        return None
    return bool(player and player.queued_at is not None)


def register_ladder_home_items(bot: discord.Client) -> None:
    """Register the ladder home's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(
        LadderInfoButton,
        LadderHomeBackButton,
        LadderJoinButton,
        LadderLeaveButton,
        LadderQueueButton,
        LadderLeaderboardButton,
        LadderPreferencesButton,
        LadderPreferenceSelect,
        LadderRegisterButton,
        LadderUnregisterButton,
    )
