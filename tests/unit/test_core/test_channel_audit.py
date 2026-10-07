"""Unit tests for the channel access policies and drift audit (#57).

Covers the acceptance properties: policy -> overwrites mapping at
provisioning, drift detection for a missing channel, a deleted channel,
a wrong overwrite and a missing role, a clean report on a synced guild,
and the i18n rendering of the report.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.channel_audit import (
    DRIFT_SEVERITY_MISSING_CHANNEL,
    DRIFT_SEVERITY_MISSING_ROLE,
    DRIFT_SEVERITY_WRONG_OVERWRITE,
    ChannelAuditService,
    DriftReport,
)
from kingdoms.core.services.mod_definition import ChannelAccessPolicy, ChannelCategoryDef, ModDefinition
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.state import StateService
from tests.mocks.state_mock import FakeClock, InMemoryStateStore

GUILD = "123456"


class FakeChannelsDatabase:
    """In-memory ChannelsDatabase: documents keyed by guild:category."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelModel] = {}

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        return self.channels.get(f"{guild_id}:{category}")

    async def upsert_channel(self, channel: ChannelModel) -> None:
        self.channels[channel.id] = channel

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        existed = f"{guild_id}:{category}" in self.channels
        if existed:
            del self.channels[f"{guild_id}:{category}"]
        return existed


class FakeChannelsPlatform:
    """In-memory platform recording applied policies and serving overwrites."""

    def __init__(self) -> None:
        self.live: set[str] = set()
        self.applied: dict[str, dict[str, object]] = {}
        self.overwrites: dict[str, dict[str, dict[str, bool]]] = {}
        self.next_id = 3000

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        return None

    async def create_channel(self, guild_id: str, name: str, category_id: str | None = None) -> str:
        channel_id = f"ch{self.next_id}"
        self.next_id += 1
        self.live.add(channel_id)
        self.overwrites[channel_id] = {}
        return channel_id

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        return channel_id in self.live

    async def find_category_by_name(self, guild_id: str, name: str) -> str | None:
        return None

    async def create_category(self, guild_id: str, name: str) -> str:
        channel_id = f"cat{self.next_id}"
        self.next_id += 1
        self.live.add(channel_id)
        self.overwrites[channel_id] = {}
        return channel_id

    async def apply_access_policy(
        self, guild_id: str, channel_id: str, policy: dict[str, object]
    ) -> None:
        self.applied[channel_id] = policy
        overwrites: dict[str, dict[str, bool]] = {"@everyone": {}}
        if policy.get("everyone_view", True):
            overwrites["@everyone"]["view_channel"] = True
        else:
            overwrites["@everyone"]["view_channel"] = False
        if policy.get("everyone_post", False):
            overwrites["@everyone"]["send_messages"] = True
        else:
            overwrites["@everyone"]["send_messages"] = False
        for role_key in list(policy.get("view", ())) + list(policy.get("post", ())):
            overwrites[role_key] = {
                "view_channel": True,
                "send_messages": role_key in policy.get("post", ()),
            }
        self.overwrites[channel_id] = overwrites

    async def get_channel_overwrites(
        self, guild_id: str, channel_id: str
    ) -> dict[str, dict[str, bool]] | None:
        return self.overwrites.get(channel_id)


def make_registry() -> ModRegistry:
    """Register a mod with two categorized channels and distinct policies."""
    registry = ModRegistry()
    registry.register(
        ModDefinition(
            name="example",
            channel_categories=(
                ChannelCategoryDef(
                    key="announce",
                    display_name="Annonces",
                    access=ChannelAccessPolicy(everyone_view=True, everyone_post=False),
                ),
                ChannelCategoryDef(
                    key="admin",
                    display_name="Admin",
                    access=ChannelAccessPolicy(
                        view=("Admins",), post=("Admins",), everyone_view=False, everyone_post=False
                    ),
                ),
            ),
        )
    )
    return registry


def make_services(
    registry: ModRegistry,
) -> tuple[ChannelService, ChannelAuditService, FakeChannelsDatabase, FakeChannelsPlatform]:
    """Wire the ChannelService and audit service with in-memory fakes."""
    db = FakeChannelsDatabase()
    platform = FakeChannelsPlatform()
    state = StateService(InMemoryStateStore(clock=FakeClock()))
    service = ChannelService(database=db, platform=platform, cache=state, registry=registry)
    audit = ChannelAuditService(database=db, platform=platform, registry=registry)
    return service, audit, db, platform


@pytest.mark.asyncio
async def test_provisioning_applies_the_declared_policies() -> None:
    """setup_mod_channels applies each category's policy as overwrites."""
    registry = make_registry()
    service, _audit, _, platform = make_services(registry)
    channels = await service.setup_mod_channels(GUILD, "example")
    announce_id = channels["example:announce"].id
    admin_id = channels["example:admin"].id
    assert platform.applied[announce_id]["everyone_view"] is True
    assert platform.applied[announce_id]["everyone_post"] is False
    assert platform.applied[admin_id]["everyone_view"] is False
    assert "Admins" in platform.applied[admin_id]["view"]


@pytest.mark.asyncio
async def test_synced_guild_audits_clean() -> None:
    """A freshly provisioned guild produces an empty drift report."""
    registry = make_registry()
    service, audit, _, _ = make_services(registry)
    await service.setup_mod_channels(GUILD, "example")
    report = await audit.audit_guild(GUILD)
    assert report.clean
    assert report.findings == ()


@pytest.mark.asyncio
async def test_missing_channel_is_reported() -> None:
    """A declared category never provisioned is a high-severity finding."""
    registry = make_registry()
    _, audit, _, _ = make_services(registry)
    report = await audit.audit_guild(GUILD)
    assert not report.clean
    kinds = [f.kind for f in report.findings]
    assert kinds == ["missing_channel", "missing_channel"]
    assert all(f.severity == DRIFT_SEVERITY_MISSING_CHANNEL for f in report.findings)


@pytest.mark.asyncio
async def test_deleted_channel_is_reported() -> None:
    """A channel gone from the platform is a high-severity finding."""
    registry = make_registry()
    service, audit, _, platform = make_services(registry)
    await service.setup_mod_channels(GUILD, "example")
    platform.live.clear()
    report = await audit.audit_guild(GUILD)
    assert {f.kind for f in report.findings} == {"deleted_channel"}
    assert all(f.severity == DRIFT_SEVERITY_MISSING_CHANNEL for f in report.findings)


@pytest.mark.asyncio
async def test_wrong_overwrite_is_reported() -> None:
    """A human-flipped @everyone overwrite is a medium-severity finding."""
    registry = make_registry()
    service, audit, _db, platform = make_services(registry)
    channels = await service.setup_mod_channels(GUILD, "example")
    announce_id = channels["example:announce"].id
    platform.overwrites[announce_id]["@everyone"]["send_messages"] = True
    report = await audit.audit_guild(GUILD)
    announce_findings = [f for f in report.findings if f.category == "example:announce"]
    assert len(announce_findings) == 1
    assert announce_findings[0].kind == "wrong_overwrite"
    assert announce_findings[0].severity == DRIFT_SEVERITY_WRONG_OVERWRITE


@pytest.mark.asyncio
async def test_missing_role_overwrite_is_reported() -> None:
    """A declared role gone from the overwrites is a high-severity finding."""
    registry = make_registry()
    service, audit, _db, platform = make_services(registry)
    channels = await service.setup_mod_channels(GUILD, "example")
    admin_id = channels["example:admin"].id
    del platform.overwrites[admin_id]["Admins"]
    report = await audit.audit_guild(GUILD)
    admin_findings = [f for f in report.findings if f.category == "example:admin"]
    assert len(admin_findings) == 1
    assert admin_findings[0].kind == "missing_role"
    assert admin_findings[0].severity == DRIFT_SEVERITY_MISSING_ROLE


class FakeI18n:
    """Minimal i18n seam for the render tests."""

    def render(self, key: str, locale: str = "en", **kwargs: Any) -> str:
        if key == "channel_audit.clean":
            return "Channel audit: no drift detected."
        if key == "channel_audit.header":
            return f"Channel audit — {kwargs['count']} drift finding(s):"
        return (
            f"[{kwargs['severity']}] {kwargs['category']}: {kwargs['kind']} "
            f"(expected {kwargs['expected']}, actual {kwargs['actual']})"
        )


@pytest.mark.asyncio
async def test_report_renders_through_i18n() -> None:
    """The drift summary renders through the i18n seam."""
    registry = make_registry()
    service, _, _, _ = make_services(registry)
    await service.setup_mod_channels(GUILD, "example")
    audit = ChannelAuditService(
        database=FakeChannelsDatabase(),
        platform=FakeChannelsPlatform(),
        registry=registry,
        i18n=FakeI18n(),
    )
    clean = DriftReport(guild_id=GUILD)
    assert audit.render_report(clean) == "Channel audit: no drift detected."
    report = await audit.audit_guild(GUILD)
    rendered = audit.render_report(report)
    assert rendered.startswith("Channel audit — 2 drift finding(s):")
    assert "missing_channel" in rendered


@pytest.mark.asyncio
async def test_yaml_access_policy_parsing(tmp_path: Any) -> None:
    """A mod YAML access declaration parses into the ChannelAccessPolicy."""
    from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    (mods_dir / "policymod.yaml").write_text(
        """
id: policymod
channels:
  - key: private
    display_name: Private
    access:
      view: [Admins]
      post: [Admins]
      everyone_view: false
      everyone_post: false
""",
        encoding="utf-8",
    )
    definitions = load_mod_definitions(tmp_path)
    registry = ModRegistry(definitions)
    definition = registry.require("policymod")
    policy = definition.channel_category("private").access
    assert policy.view == ("Admins",)
    assert policy.post == ("Admins",)
    assert policy.everyone_view is False
    assert policy.everyone_post is False
