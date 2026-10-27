#!/usr/bin/env bash
# Build the service and load generator images and load both into every node of the kind cluster (there is no registry).
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "building $WEB_IMAGE"
docker build --target service --tag "$WEB_IMAGE" "$ROOT"
log "building $LOADGEN_IMAGE"
docker build --target loadgen --tag "$LOADGEN_IMAGE" "$ROOT"
if "$KIND" get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  log "loading both images into cluster $CLUSTER_NAME"
  "$KIND" load docker-image "$WEB_IMAGE" "$LOADGEN_IMAGE" --name "$CLUSTER_NAME"
fi
