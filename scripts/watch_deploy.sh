#!/usr/bin/env bash
# Watch a kingdoms-infra deployment end-to-end (ADR-0018 single-writer chain).
#
# Given an environment and the unique version label of the pin (the short
# image tag: pr-<id>-<timestamp>-<sha7>, sha-<sha> or vX.Y.Z), this script:
#   1. polls the kingdoms-infra state branch deploy/<env> for the pin commit
#      (the commit whose message carries the label);
#   2. watches the Deploy environment workflow run for that exact state
#      commit (matched on head_sha) until it completes.
#
# It exits 0 when the deploy run succeeded, 1 otherwise. Used by the deploy
# and release workflows and by agent sessions — one implementation of the
# "watch the pin land, then watch the deploy" pattern.
#
# Usage:
#   scripts/watch-deploy.sh <env> <version-label> [--deadline-find <min>] [--deadline-run <min>]
#
# Requires: gh (authenticated with read access to kingdoms-infra).
set -euo pipefail

env_name="${1:?usage: watch-deploy.sh <env> <version-label>}"
label="${2:?usage: watch-deploy.sh <env> <version-label>}"
find_deadline_min="${3:-30}"
run_deadline_min="${4:-15}"
infra_owner_repo="merlin-pinpin-org/kingdoms-infra"

state_file="envs/${env_name}/state/kingdoms-bot.yml"

echo "==> Waiting for the pin commit carrying '${label}' on deploy/${env_name}"
find_deadline=$(( $(date +%s) + find_deadline_min * 60 ))
state_sha=""
while [[ $(date +%s) -lt $find_deadline ]]; do
  state_sha="$(gh api "repos/${infra_owner_repo}/commits?sha=deploy/${env_name}&path=${state_file}&per_page=10" \
    --jq ".[] | select(.commit.message | contains(\"${label}\")) | .sha" | head -1 || true)"
  if [[ -n "$state_sha" ]]; then
    echo "    pin commit: ${state_sha}"
    break
  fi
  echo "    not landed yet..."
  sleep 20
done
if [[ -z "$state_sha" ]]; then
  echo "::error::the pin never landed on deploy/${env_name} (${find_deadline_min} min deadline) — check the kingdoms-infra Pin state workflow run" >&2
  exit 1
fi

echo "==> Watching the Deploy environment run for state commit ${state_sha:0:7}"
run_deadline=$(( $(date +%s) + run_deadline_min * 60 ))
run_url=""
conclusion=""
while [[ $(date +%s) -lt $run_deadline ]]; do
  run_json="$(gh api "repos/${infra_owner_repo}/actions/workflows/deploy.yml/runs?branch=deploy/${env_name}&per_page=5" \
    --jq ".workflow_runs[] | select(.head_sha == \"${state_sha}\") | .id" | head -1 || true)"
  if [[ -n "$run_json" ]]; then
    run_url="https://github.com/${infra_owner_repo}/actions/runs/${run_json}"
    status="$(gh api "repos/${infra_owner_repo}/actions/runs/${run_json}" --jq '.status')"
    if [[ "$status" == "completed" ]]; then
      conclusion="$(gh api "repos/${infra_owner_repo}/actions/runs/${run_json}" --jq '.conclusion')"
      break
    fi
    echo "    run ${run_json} in progress (${status})..."
  else
    echo "    run not started yet..."
  fi
  sleep 20
done
if [[ -z "$run_url" ]]; then
  echo "::error::the kingdoms-infra deploy run never started (${run_deadline_min} min deadline)" >&2
  exit 1
fi

echo "    deploy run: ${run_url} — ${conclusion:-unknown}"
if [[ "$conclusion" == "success" ]]; then
  echo "==> deploy/${env_name} is deployed and healthy"
  exit 0
fi
echo "::error::the deploy run did not succeed: ${run_url}" >&2
exit 1
