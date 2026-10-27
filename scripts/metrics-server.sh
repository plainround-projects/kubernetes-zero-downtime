#!/usr/bin/env bash
# Install metrics-server (pinned, checksum-verified): the autoscaler reads CPU use from it, and without it
# the HorizontalPodAutoscaler shows <unknown> for ever. Idempotent.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster

VERSION="v0.9.0"
SHA256="1cec29a5267809306a2c6ec74a3e449abbb705b4a8beed0c8a1963910f72c79b"
if ! kubectl -n kube-system get deployment metrics-server >/dev/null 2>&1; then
  manifest="$(mktemp)"
  curl -fsSL -o "$manifest" "https://github.com/kubernetes-sigs/metrics-server/releases/download/$VERSION/components.yaml"
  [ "$(shasum -a 256 "$manifest" | cut -d' ' -f1)" = "$SHA256" ] || die "metrics-server manifest checksum mismatch"
  kubectl apply -f "$manifest"
  rm -f "$manifest"
  # kind's kubelets serve self-signed certificates, so metrics-server must skip verifying them.
  kubectl -n kube-system patch deployment metrics-server --type=json \
    -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi
kubectl -n kube-system rollout status deployment/metrics-server --timeout=240s
for _ in $(seq 1 60); do
  kubectl top nodes >/dev/null 2>&1 && exit 0
  sleep 5
done
die "metrics-server is running but kubectl top still has no data"
