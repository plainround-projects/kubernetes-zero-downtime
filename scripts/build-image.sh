#!/usr/bin/env bash
# Build the service and load generator images and import both into every node of the k3d cluster (there is no registry).
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "building $WEB_IMAGE"
docker build --target service --tag "$WEB_IMAGE" "$ROOT"
log "building $LOADGEN_IMAGE"
docker build --target loadgen --tag "$LOADGEN_IMAGE" "$ROOT"
if "$K3D" cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER_NAME"; then
  log "importing both images into cluster $CLUSTER_NAME"
  "$K3D" image import "$WEB_IMAGE" "$LOADGEN_IMAGE" --cluster "$CLUSTER_NAME" --mode direct
fi
