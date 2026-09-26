"""Shared deploy-artifact rendering: one identity, two renderings.

The startup announcement (Components V2 — links are buttons) and the
/status embed (markdown links) show the **same deployment identity**;
these helpers are the single source of truth for it (developer
mandate: /status and the announcement must converge, kingdoms-services
#109):

- each artifact (services commit, docker image, infra state commit,
  deployment run) carries its own timestamp, split by nature:
  Committed (the source/state commit) vs Built (the image) vs
  Deployed (the run);
- the docker tag renders shortened (sha7 + short stamp) — the full
  pr-<id>-<stamp>-<sha> tag never renders anywhere;
- both renderers degrade the same way on partial pipeline data.
"""

from __future__ import annotations

from kingdoms.core.services.status import StatusService

SERVICES_REPO_URL = "https://github.com/merlin-pinpin-org/kingdoms-services"
INFRA_REPO_URL = "https://github.com/merlin-pinpin-org/kingdoms-infra"
PACKAGE_URL = f"{SERVICES_REPO_URL}/pkgs/container/kingdoms-services"


def is_unix(value: str) -> bool:
    """Whether a deploy timestamp field is a usable unix timestamp."""
    return value.strip().isdigit()


def relative_time(value: str) -> str:
    """Render a unix timestamp as a Discord relative time (empty-safe)."""
    return f"<t:{value.strip()}:R>" if is_unix(value) else ""


def sha7_of(tree_url: str) -> str:
    """Extract the deployed commit sha7 from its tree URL."""
    return tree_url.rstrip("/").rsplit("/", 1)[-1][:7]


def docker_tag(image: str) -> str:
    """Extract the docker tag from a pinned image reference."""
    return image.rsplit(":", 1)[-1] if ":" in image else image


def short_tag(tag: str) -> str:
    """Shorten a docker tag for display (sha7 + short stamp)."""
    parts = tag.rsplit("-", 2)
    if len(parts) == 3:
        stamp, sha = parts[1], parts[2]
        return f"{stamp}-{sha}" if len(stamp) <= 8 else sha
    return tag[:20]


def services_commit_ts(status: StatusService) -> str:
    """Return the services commit timestamp (relative-time ready)."""
    return status.deploy_commit_ts


def infra_commit_ts(status: StatusService) -> str:
    """Return the infra state commit timestamp (relative-time ready)."""
    return status.deploy_infra_commit_ts
