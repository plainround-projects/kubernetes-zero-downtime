#!/usr/bin/env bash
# shellcheck shell=bash
# Shared settings for the scripts. Sourced, not run.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-zero-downtime}"
NAMESPACE="${NAMESPACE:-zero-downtime}"
WEB_IMAGE="${WEB_IMAGE:-zero-downtime-web:0.1.0}"
LOADGEN_IMAGE="${LOADGEN_IMAGE:-zero-downtime-loadgen:0.1.0}"
KIND="$ROOT/.bin/kind"
export ROOT CLUSTER_NAME NAMESPACE WEB_IMAGE LOADGEN_IMAGE KIND
# A kubeconfig of its own: this project never reads or changes ~/.kube/config.
export KUBECONFIG="${KUBECONFIG:-$ROOT/.kube/config}"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

require_cluster() {
  kubectl get nodes >/dev/null 2>&1 || die "no cluster reachable through $KUBECONFIG; run: make up"
}
