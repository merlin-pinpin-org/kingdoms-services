#!/usr/bin/env bash
# Generate readable release notes for a kingdoms-services release.
#
# GitHub's --generate-notes output is dense (What's Changed + diff stats +
# full commit lists); the Kingdoms releases want a short, readable
# changelog: the pull requests merged since the previous release tag, one
# bullet each, with their number as the only link. Used by the
# create-release job of the Docker workflow.
#
# Usage:
#   scripts/release_notes.sh <vX.Y.Z>
#
# Output: Markdown on stdout — passed to the release step as the notes.
set -euo pipefail

tag="${1:?usage: release_notes.sh <vX.Y.Z>}"
repo="merlin-pinpin-org/kingdoms-services"

# Range: previous release tag (semver-sorted, excluding this one) .. now.
previous="$(git tag --list 'v*' --sort=-v:refname | grep -v "^${tag}$" | head -1 || true)"

if [[ -n "$previous" ]]; then
  since="$(git log -1 --pretty=format:'%cI' "${previous}^{commit}")"
  header="Since ${previous}"
else
  since="1970-01-01T00:00:00Z"
  header="All merged pull requests (first release)"
fi
# Normalize to a comparable UTC instant (git may emit a local offset).
since_utc="$(date -u -d "$since" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "$since")"

echo "## What's changed"
echo
echo "_${header}_"
echo
gh pr list --repo "$repo" --state merged --limit 200 \
  --json number,title,url,mergedAt \
  --jq "sort_by(.mergedAt) | reverse | .[] | select(.mergedAt > \"$since_utc\") | \"- \\(.title) ([#\\(.number)](\\(.url)))\"" \
  | head -200

echo
echo "## Image"
echo
echo "\`ghcr.io/${repo}:${tag}\` — deployed on the test environment first;"
echo "promoted to production after validation."
