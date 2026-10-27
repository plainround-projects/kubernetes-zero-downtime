#!/usr/bin/env bash
# One rolling update under load, with the verdict: reset the counters, restart the web pods, wait for the
# rollout and a few seconds more, then print what the load generator counted. Usage: rollout-under-load.sh [runs]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster

runs="${1:-1}"
for run in $(seq 1 "$runs"); do
  "$ROOT/scripts/loadctl.sh" reset >/dev/null
  kubectl -n "$NAMESPACE" rollout restart deployment/web >/dev/null
  kubectl -n "$NAMESPACE" rollout status deployment/web --timeout=300s >/dev/null
  sleep 8
  printf 'rollout %s of %s: ' "$run" "$runs"
  "$ROOT/scripts/loadctl.sh" stats
done
