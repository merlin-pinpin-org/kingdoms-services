#!/usr/bin/env bash
# Cut a release of kingdoms-services (tag vX.Y.Z + GitHub release).
#
# Pre-flight (fail-closed):
#   - the tag matches vX.Y.Z (classifiers allowed, like the release-tags
#     ruleset);
#   - main is checked out, clean and up to date with origin;
#   - the required checks of the head commit all passed.
#
# The tag push itself triggers the Docker workflow: it publishes the
# vX.Y.Z image to GHCR and pins it on kingdoms-infra deploy/test
# (validation on the test environment before any prod promotion —
# .github/workflows/docker.yml, ADR-0018).
#
# Usage:
#   scripts/release.sh <vX.Y.Z> [--notes-file <file>]
#
# Requires: gh (authenticated, write on this repo).
set -euo pipefail

tag="${1:?usage: release.sh <vX.Y.Z> [--notes-file <file>]}"
shift || true
notes_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --notes-file) notes_file="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

if ! [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$ ]]; then
  echo "::error::'$tag' is not a release tag (expected vX.Y.Z, classifiers allowed)" >&2
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

if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
  echo "::error::tag '${tag}' already exists" >&2
  exit 1
fi

echo "==> Creating tag ${tag}"
git tag -a "$tag" -m "Release ${tag}"

notes_args=()
if [[ -n "$notes_file" ]]; then
  notes_args=(--notes-file "$notes_file")
else
  notes_args=(--generate-notes)
fi

echo "==> Creating the GitHub release (triggers the image build and the deploy/test pin)"
gh release create "$tag" "${notes_args[@]}" --verify-tag
echo "==> Release ${tag} created: https://github.com/merlin-pinpin-org/kingdoms-services/releases/tag/${tag}"
echo "    next: the Docker workflow pins ${tag} on deploy/test; validate in Discord,"
echo "    then run the Promote release workflow (Actions > Promote release > ${tag})."
