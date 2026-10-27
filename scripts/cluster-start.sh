#!/usr/bin/env bash
# Create the local kind cluster (idempotent) and wait for every node to be Ready.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

"$ROOT/scripts/tools.sh" >/dev/null
mkdir -p "$(dirname "$KUBECONFIG")"
if "$KIND" get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  log "cluster $CLUSTER_NAME already exists"
  "$KIND" export kubeconfig --name "$CLUSTER_NAME" --kubeconfig "$KUBECONFIG"
else
  log "creating kind cluster $CLUSTER_NAME (Kubernetes 1.35, one control-plane node and three workers)"
  "$KIND" create cluster --name "$CLUSTER_NAME" --config "$ROOT/cluster/kind-config.yaml" --kubeconfig "$KUBECONFIG" --wait 180s
fi
kubectl wait --for=condition=Ready nodes --all --timeout=180s
kubectl get nodes
