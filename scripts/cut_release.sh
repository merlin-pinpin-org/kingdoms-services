#!/usr/bin/env bash
# Cut a release of kingdoms-services — the version is never hand-picked.
#
# scripts/generate_changelog.py --bump derives the next tag from the
# Conventional Commits since the last release (feat → minor, fix → patch,
# BREAKING → major); --prerelease appends the next free -rc<n> classifier
# on the same base. The GitHub release itself is created by the Docker
# workflow's create-release job (contents: write) — release creation is a
# mutating call agent sessions cannot perform; CI owns it.
#
# Pre-flight (fail-closed):
#   - main is checked out, clean and up to date with origin;
#   - the required checks of the head commit all passed;
#   - the computed tag does not exist yet.
#
# The tag push itself triggers the Docker workflow: it publishes the
# image to GHCR (tagged with the raw tag name) and pins it on
# kingdoms-infra deploy/test (validation on the test environment before
# any prod promotion — .github/workflows/docker.yml, ADR-0018). A -rc
# pre-release pins on deploy/test like any release, but the Promote
# release workflow refuses to pin it on deploy/prod.
#
# Usage:
#   scripts/cut_release.sh [--prerelease]
#
# Requires: git push rights on this repo.
set -euo pipefail

prerelease=""
if [[ "${1:-}" == "--prerelease" ]]; then
  prerelease="--prerelease"
elif [[ $# -gt 0 ]]; then
  echo "unknown option: $1 (usage: cut_release.sh [--prerelease])" >&2
  exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "::error::checkout main first (currently on '$branch')" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "::error::working tree is not clean" >&2
  exit 1
fi
git fetch origin main --quiet
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "::error::local main is not up to date with origin/main — pull first" >&2
  exit 1
fi

sha="$(git rev-parse HEAD)"
echo "==> Checking the required checks of ${sha:0:7}"
failing="$(gh api "repos/merlin-pinpin-org/kingdoms-services/commits/${sha}/check-runs" \
  --jq '.check_runs[] | select(.conclusion != null and .conclusion != "success" and .conclusion != "neutral" and .conclusion != "skipped") | .name')"
if [[ -n "$failing" ]]; then
  echo "::error::failing checks on main ${sha:0:7}:" >&2
  echo "$failing" >&2
  exit 1
fi
echo "    all checks green"

bump_args=(--bump)
[[ -n "$prerelease" ]] && bump_args+=(--prerelease)
tag="$(uv run python scripts/generate_changelog.py "${bump_args[@]}")"
if ! [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]+)?$ ]]; then
  echo "::error::computed tag '$tag' is not a release tag" >&2
  exit 1
fi
if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
  echo "::error::tag '${tag}' already exists" >&2
  exit 1
fi

echo "==> Creating and pushing tag ${tag}"
git tag -a "$tag" -m "Release ${tag}"
git push origin "$tag"

echo "==> The Docker workflow now:"
echo "    - builds and publishes the image (tagged with the raw tag name);"
echo "    - pins it on kingdoms-infra deploy/test (validation environment);"
echo "    - creates the GitHub release with generated notes."
echo "==> Release ${tag}: https://github.com/merlin-pinpin-org/kingdoms-services/releases/tag/${tag}"
if [[ -n "$prerelease" ]]; then
  echo "    ${tag} is a pre-release: validate it in Discord on the test environment,"
  echo "    then cut the final release (make release) — never promote an -rc to prod."
else
  echo "    next: validate ${tag} in Discord on the test environment, then run the"
  echo "    Promote release workflow (Actions > Promote release > tag ${tag})."
fi
