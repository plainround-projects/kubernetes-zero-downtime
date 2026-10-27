#!/usr/bin/env bash
# Apply an overlay (naive, graceful, rolling-tuned or production; default production) and wait for it.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster

overlay="${1:-production}"
[ -d "$ROOT/manifests/overlays/$overlay" ] || die "no overlay named $overlay"
log "applying overlay $overlay"
kubectl apply -k "$ROOT/manifests/overlays/$overlay"
kubectl -n "$NAMESPACE" rollout status deployment/web --timeout=300s
kubectl -n "$NAMESPACE" rollout status deployment/loadgen --timeout=300s
# The autoscaler (production only) may still be bringing the Deployment up to its minimum.
for _ in $(seq 1 60); do
  ready="$(kubectl -n "$NAMESPACE" get deployment web -o jsonpath='{.status.readyReplicas}')"
  [ "${ready:-0}" -ge 6 ] && break
  sleep 3
done
kubectl -n "$NAMESPACE" get pods -o wide
