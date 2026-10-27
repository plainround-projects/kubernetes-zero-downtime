#!/usr/bin/env bash
# Step 5: show where the web pods are, drain the worker holding the most of them, show the disruption budget
# holding evictions back, then uncordon. The load generator keeps counting errors the whole time.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster

node="$(kubectl -n "$NAMESPACE" get pods -l app=web -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')"
log "web pods per node"
kubectl -n "$NAMESPACE" get pods -l app=web -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c
"$ROOT/scripts/loadctl.sh" reset >/dev/null
trap 'kubectl uncordon "$node" >/dev/null 2>&1 || true' EXIT
log "draining $node"
kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=300s
sleep 5
log "errors counted during the drain:"
"$ROOT/scripts/loadctl.sh" stats
log "web pods per node after the drain"
kubectl -n "$NAMESPACE" get pods -l app=web -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c
