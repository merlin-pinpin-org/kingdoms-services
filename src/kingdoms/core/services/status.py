"""StatusService: builds the bot status report consumed by /status.

Generic core capability: it aggregates what the platform and the mod
registry know — uptime, enabled mods with their declared channels/roles,
configured games, bot admins, the deployed-artifact link and version label
— without any mod-specific logic.

Reference: kingdoms-services#35 (bot vs guild admins),
kingdoms-infra#37 (deploy URL plumbing).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from kingdoms.core.services.mod_registry import ModRegistry


@dataclass(frozen=True, slots=True)
class BotAdmins:
    """Bot operators, loaded from the environment (BOT_ADMINS)."""

    user_ids: tuple[str, ...] = field(default_factory=tuple)


def parse_bot_admins(raw: str | None) -> BotAdmins:
    """Parse the BOT_ADMINS environment value; empty means no operator."""
    if raw is None or not raw.strip():
        return BotAdmins()
    return BotAdmins(user_ids=tuple(uid.strip() for uid in raw.split(",") if uid.strip() and uid.strip().isdigit()))


def format_version(label: str, url: str) -> str:
    """Render the Version field.

    A labeled link when the pipeline provides both, the bare label when
    there is no URL, and the package version when the pipeline provides no
    label (local runs, unmanaged deploys).
    """
    if not label:
        label = _package_version()
    if not url:
        return label
    return f"[{label}]({url})"


class StatusService:
    """Aggregate the platform status into a plain, inspectable report."""

    def __init__(
        self,
        registry: ModRegistry,
        bot_admins: BotAdmins,
        games: tuple[str, ...] = (),
        clock: Callable[[], float] = time.monotonic,
        deploy_url: str = "",
        deploy_label: str = "",
        deploy_run_url: str = "",
    ) -> None:
        self._registry = registry
        self._bot_admins = bot_admins
        self._games = tuple(games)
        self._clock = clock
        self._started_at = self._clock()
        self._deploy_url = deploy_url.strip()
        self._deploy_label = deploy_label.strip()
        self._deploy_run_url = deploy_run_url.strip()

    @property
    def deploy_url(self) -> str:
        """Link to the deployed artifact (KINGDOMS_DEPLOY_URL; empty when unknown)."""
        return self._deploy_url

    @property
    def deploy_label(self) -> str:
        """Version label of the deployed artifact (KINGDOMS_DEPLOY_LABEL)."""
        return self._deploy_label

    @property
    def deploy_run_url(self) -> str:
        """URL of the deploy job (KINGDOMS_DEPLOY_RUN_URL; empty when unknown)."""
        return self._deploy_run_url

    @property
    def bot_admins(self) -> tuple[str, ...]:
        """Bot operator user IDs (BOT_ADMINS)."""
        return self._bot_admins.user_ids

    def uptime_seconds(self) -> float:
        """Seconds since the service started."""
        return max(0.0, self._clock() - self._started_at)

    def enabled_mods(self) -> dict[str, dict[str, tuple[str, ...]]]:
        """Return enabled mods with their declared channel categories and roles."""
        return {
            name: {
                "channels": tuple(c.key for c in definition.channel_categories),
                "roles": tuple(r.key for r in definition.roles),
            }
            for name, definition in self._registry.enabled().items()
        }

    def games(self) -> tuple[str, ...]:
        """Return the configured game ids."""
        return self._games

    def report(self) -> dict[str, object]:
        """Full status report (dict, ready for embeds or JSON)."""
        return {
            "version": self._deploy_label or _package_version(),
            "uptime_seconds": round(self.uptime_seconds(), 1),
            "games": self._games,
            "bot_admins": self._bot_admins.user_ids,
            "deploy_url": self._deploy_url,
            "deploy_label": self._deploy_label,
            "deploy_run_url": self._deploy_run_url,
            "enabled_mods": self.enabled_mods(),
        }


def _package_version() -> str:
    import kingdoms

    return kingdoms.__version__
