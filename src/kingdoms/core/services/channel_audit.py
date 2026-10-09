"""Channel audit: declared access policies vs runtime overwrites (kingdoms-services#57).

The mod declarations are the source of truth; the Discord permission
overwrites are the runtime state. They can drift (a human edits channel
permissions, a role is deleted, a channel is moved). This service
compares the two per guild and produces a structured DriftReport —
drift is reported, never silently repaired (repair is the on-demand
sync, kingdoms-services#58).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from kingdoms.core.services.channel import ChannelsDatabase, ChannelsPlatform
from kingdoms.core.services.mod_registry import ModRegistry

logger = logging.getLogger("kingdoms.channels.audit")

DRIFT_SEVERITY_MISSING_CHANNEL = "high"
DRIFT_SEVERITY_MISSING_ROLE = "high"
DRIFT_SEVERITY_WRONG_OVERWRITE = "medium"
DRIFT_SEVERITY_UNDECLARED_MANAGED = "low"


@dataclass(frozen=True, slots=True)
class DriftFinding:
    """One observed difference between declaration and runtime."""

    category: str
    kind: str
    expected: str
    actual: str
    severity: str


@dataclass(frozen=True, slots=True)
class DriftReport:
    """The audit result for one guild: findings by category."""

    guild_id: str
    findings: tuple[DriftFinding, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        """Whether the runtime matches the declarations."""
        return not self.findings


class ChannelAuditSink(Protocol):
    """Narrow seam to route a human-readable drift report."""

    async def send_drift_report(self, guild_id: str, report: DriftReport) -> None:
        """Deliver the report summary to the admin audience."""
        ...


class ChannelAuditService:
    """Compare declared access policies with the runtime overwrites."""

    def __init__(
        self,
        database: ChannelsDatabase,
        platform: ChannelsPlatform,
        registry: ModRegistry,
        sink: ChannelAuditSink | None = None,
        i18n: Any = None,
        locale: str = "en",
    ) -> None:
        """Wire the stores; the sink and i18n are optional (report-only)."""
        self._db = database
        self._platform = platform
        self._registry = registry
        self._sink = sink
        self._i18n = i18n
        self._locale = locale

    async def audit_guild(self, guild_id: str) -> DriftReport:
        """Audit one guild: every declared category of every enabled mod."""
        findings: list[DriftFinding] = []
        for mod_name, definition in self._registry.enabled().items():
            for category_def in definition.channel_categories:
                category = f"{mod_name}:{category_def.key}"
                findings.extend(await self._audit_category(guild_id, category, category_def.access.to_dict()))
        return DriftReport(guild_id=guild_id, findings=tuple(findings))

    async def _audit_category(self, guild_id: str, category: str, policy: dict[str, Any]) -> list[DriftFinding]:
        """Audit one category: channel existence, then overwrites."""
        stored = await self._db.find_channel(guild_id, category)
        if stored is None:
            return [
                DriftFinding(
                    category=category,
                    kind="missing_channel",
                    expected=category,
                    actual="absent",
                    severity=DRIFT_SEVERITY_MISSING_CHANNEL,
                )
            ]
        if not await self._platform.channel_exists(guild_id, stored.channel_id):
            return [
                DriftFinding(
                    category=category,
                    kind="deleted_channel",
                    expected=stored.channel_id,
                    actual="gone",
                    severity=DRIFT_SEVERITY_MISSING_CHANNEL,
                )
            ]
        return self._compare_overwrites(
            category,
            policy,
            await self._platform.get_channel_overwrites(guild_id, stored.channel_id),
        )

    def _compare_overwrites(
        self,
        category: str,
        policy: dict[str, Any],
        overwrites: dict[str, dict[str, bool]] | None,
    ) -> list[DriftFinding]:
        """Compare the declared policy with the actual overwrites."""
        if overwrites is None:
            return [
                DriftFinding(
                    category=category,
                    kind="unreadable_overwrites",
                    expected="permission overwrites",
                    actual="unreadable",
                    severity=DRIFT_SEVERITY_WRONG_OVERWRITE,
                )
            ]
        findings: list[DriftFinding] = []
        everyone_view = bool(policy.get("everyone_view", True))
        actual_everyone_view = overwrites.get("@everyone", {}).get("view_channel", None)
        if actual_everyone_view is not None and actual_everyone_view != everyone_view:
            findings.append(
                DriftFinding(
                    category=category,
                    kind="wrong_overwrite",
                    expected=f"@everyone view={everyone_view}",
                    actual=f"@everyone view={actual_everyone_view}",
                    severity=DRIFT_SEVERITY_WRONG_OVERWRITE,
                )
            )
        everyone_post = bool(policy.get("everyone_post", False))
        actual_everyone_post = overwrites.get("@everyone", {}).get("send_messages", None)
        if actual_everyone_post is not None and actual_everyone_post != everyone_post:
            findings.append(
                DriftFinding(
                    category=category,
                    kind="wrong_overwrite",
                    expected=f"@everyone post={everyone_post}",
                    actual=f"@everyone post={actual_everyone_post}",
                    severity=DRIFT_SEVERITY_WRONG_OVERWRITE,
                )
            )
        view_roles: list[str] = list(policy.get("view") or ())
        post_roles: list[str] = list(policy.get("post") or ())
        role_keys = view_roles + post_roles
        for role_key in sorted(set(role_keys)):
            matched = any(role_key in target for target in overwrites if target != "@everyone")
            if not matched:
                findings.append(
                    DriftFinding(
                        category=category,
                        kind="missing_role",
                        expected=role_key,
                        actual="absent",
                        severity=DRIFT_SEVERITY_MISSING_ROLE,
                    )
                )
        return findings

    def render_report(self, report: DriftReport) -> str:
        """Render the human-readable summary (i18n when available)."""
        if self._i18n is not None:
            try:
                if report.clean:
                    return str(self._i18n.render("channel_audit.clean", self._locale))
                header = str(self._i18n.render("channel_audit.header", self._locale, count=len(report.findings)))
                lines = [
                    self._i18n.render(
                        "channel_audit.finding",
                        self._locale,
                        category=f.category,
                        kind=f.kind,
                        expected=f.expected,
                        actual=f.actual,
                        severity=f.severity,
                    )
                    for f in report.findings
                ]
                return "\n".join([header, *lines])
            except Exception:
                logger.warning("CHANNEL AUDIT i18n render failed — plain fallback")
        if report.clean:
            return "Channel audit: no drift detected."
        lines = [
            f"[{f.severity}] {f.category}: {f.kind} (expected {f.expected}, actual {f.actual})" for f in report.findings
        ]
        return "\n".join(["Channel audit — drift detected:", *lines])

    async def audit_and_report(self, guild_id: str) -> DriftReport:
        """Audit one guild and route the report to the sink (best-effort)."""
        report = await self.audit_guild(guild_id)
        if self._sink is not None:
            try:
                await self._sink.send_drift_report(guild_id, report)
            except Exception:
                logger.warning("CHANNEL AUDIT report routing failed (guild %s)", guild_id)
        return report
