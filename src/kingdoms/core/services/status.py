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


def _link(text: str, url: str) -> str:
    """Render a labeled link, or the bare text when there is no URL."""
    return f"[{text}]({url})" if url else text


def _format_version_pr(ref: str, label: str, url: str) -> str:
    """Version for a PR deploy: Pull-request #<n> -> deployment comment."""
    return f"Pull-request {_link(f"#{ref or label}", url)}"


def _format_version_pr_titled(ref: str, title: str, url: str) -> str:
    """Pull-request line: only #<n> links; the title stays plain text."""
    quoted = f" “{title}”" if title else ""
    return f"Pull-request {_link(f"#{ref}", url)}{quoted}"


def _format_version_commit(ref: str, label: str, url: str, ts: str) -> str:
    """Version for a main deploy: Commit <sha> + relative time."""
    text = _link(f"Commit {ref or label}", url)
    if ts.strip().isdigit():
        text = f"{text} <t:{ts.strip()}:R>"
    return text


def _format_version_release(ref: str, label: str, url: str) -> str:
    """Version for a release deploy: Release vX.Y.Z (only vX.Y.Z links)."""
    return f"Release {_link(ref or label, url)}"


def format_version(
    label: str,
    url: str,
    kind: str = "",
    ref: str = "",
    tree_url: str = "",
    ts: str = "",
    pr_title: str = "",
) -> str:
    """Render the Version line of the Services section.

    Typed links when the pipeline provides the deploy kind:
    `Pull-request #<n> "<title>"` (deployment comment), `Commit <sha>` +
    tree with a relative timestamp, or `Release vX.Y.Z` + tree. Falls
    back to a labeled link on (label, url), then to the bare label /
    package version.
    """
    if not label:
        label = _package_version()
    if kind == "pr":
        return _format_version_pr_titled(ref, pr_title, url) if pr_title else _format_version_pr(ref, label, url)
    if kind == "main":
        return _format_version_commit(ref, label, url, ts)
    if kind == "release":
        return _format_version_release(ref, label, url)
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
        deploy_infra_label: str = "",
        deploy_infra_url: str = "",
        deploy_kind: str = "",
        deploy_ref: str = "",
        deploy_tree_url: str = "",
        deploy_ts: str = "",
        deploy_run_number: str = "",
        deploy_run_ts: str = "",
        deploy_image: str = "",
        deploy_branch: str = "",
        deploy_pr_title: str = "",
        deploy_commit_ts: str = "",
        deploy_infra_commit_ts: str = "",
    ) -> None:
        self._registry = registry
        self._bot_admins = bot_admins
        self._games = tuple(games)
        self._clock = clock
        self._started_at = self._clock()
        self._deploy_url = deploy_url.strip()
        self._deploy_label = deploy_label.strip()
        self._deploy_run_url = deploy_run_url.strip()
        self._deploy_infra_label = deploy_infra_label.strip()
        self._deploy_infra_url = deploy_infra_url.strip()
        self._deploy_kind = deploy_kind.strip()
        self._deploy_ref = deploy_ref.strip()
        self._deploy_tree_url = deploy_tree_url.strip()
        self._deploy_ts = deploy_ts.strip()
        self._deploy_run_number = deploy_run_number.strip()
        self._deploy_run_ts = deploy_run_ts.strip()
        self._deploy_image = deploy_image.strip()
        self._deploy_branch = deploy_branch.strip()
        self._deploy_pr_title = deploy_pr_title.strip()
        self._deploy_commit_ts = deploy_commit_ts.strip()
        self._deploy_infra_commit_ts = deploy_infra_commit_ts.strip()

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
    def deploy_infra_label(self) -> str:
        """Version label of the deployed infra state branch (deploy/<env>@<sha>)."""
        return self._deploy_infra_label

    @property
    def deploy_infra_url(self) -> str:
        """Link to the deployed infra state tree (KINGDOMS_DEPLOY_INFRA_URL)."""
        return self._deploy_infra_url

    @property
    def deploy_kind(self) -> str:
        """Deploy kind: pr, main, or release (KINGDOMS_DEPLOY_KIND)."""
        return self._deploy_kind

    @property
    def deploy_ref(self) -> str:
        """Deploy reference: PR number, short sha, or release tag."""
        return self._deploy_ref

    @property
    def deploy_tree_url(self) -> str:
        """Link to the deployed source tree (KINGDOMS_DEPLOY_TREE_URL)."""
        return self._deploy_tree_url

    @property
    def deploy_ts(self) -> str:
        """Unix timestamp of the deployed commit (KINGDOMS_DEPLOY_TS)."""
        return self._deploy_ts

    @property
    def deploy_run_number(self) -> str:
        """Number of the deploy job (KINGDOMS_DEPLOY_RUN_NUMBER)."""
        return self._deploy_run_number

    @property
    def deploy_run_ts(self) -> str:
        """Unix timestamp of the deploy job start (KINGDOMS_DEPLOY_RUN_TS)."""
        return self._deploy_run_ts

    @property
    def deploy_image(self) -> str:
        """Pinned container image reference (KINGDOMS_DEPLOY_IMAGE; empty when unknown)."""
        return self._deploy_image

    @property
    def deploy_branch(self) -> str:
        """Source branch of the deployed commit (KINGDOMS_DEPLOY_BRANCH)."""
        return self._deploy_branch

    @property
    def deploy_pr_title(self) -> str:
        """Title of the PR that triggered the deploy (KINGDOMS_DEPLOY_PR_TITLE)."""
        return self._deploy_pr_title

    @property
    def deploy_commit_ts(self) -> str:
        """Unix timestamp of the deployed services commit (KINGDOMS_DEPLOY_COMMIT_TS)."""
        return self._deploy_commit_ts

    @property
    def deploy_infra_commit_ts(self) -> str:
        """Unix timestamp of the deployed infra state commit (KINGDOMS_DEPLOY_INFRA_COMMIT_TS)."""
        return self._deploy_infra_commit_ts

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
            "deploy_infra_label": self._deploy_infra_label,
            "deploy_infra_url": self._deploy_infra_url,
            "deploy_kind": self._deploy_kind,
            "deploy_ref": self._deploy_ref,
            "deploy_tree_url": self._deploy_tree_url,
            "deploy_ts": self._deploy_ts,
            "deploy_run_number": self._deploy_run_number,
            "deploy_run_ts": self._deploy_run_ts,
            "deploy_image": self._deploy_image,
            "deploy_branch": self._deploy_branch,
            "deploy_pr_title": self._deploy_pr_title,
            "deploy_commit_ts": self._deploy_commit_ts,
            "deploy_infra_commit_ts": self._deploy_infra_commit_ts,
            "enabled_mods": self.enabled_mods(),
        }


def _package_version() -> str:
    import kingdoms

    return kingdoms.__version__
