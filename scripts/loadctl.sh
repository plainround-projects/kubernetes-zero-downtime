#!/usr/bin/env bash
# Talk to the in-cluster load generator through a short-lived port-forward.
#   loadctl.sh stats        the counters since the last reset
#   loadctl.sh reset        print the final counters of the window and start a new one
#   loadctl.sh rate 300     change the request rate (requests per second)
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster

port="${LOCAL_PORT:-$((19000 + RANDOM % 1000))}"
kubectl -n "$NAMESPACE" port-forward svc/loadgen "$port:9100" >/dev/null &
forward=$!
trap 'kill $forward 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -fs "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
case "${1:-stats}" in
  stats) curl -fsS "http://127.0.0.1:$port/stats" ;;
  reset) curl -fsS -X POST "http://127.0.0.1:$port/reset" ;;
  rate) curl -fsS -X POST -d "{\"rps\": ${2:?usage: loadctl.sh rate <requests per second>}}" "http://127.0.0.1:$port/rate" ;;
  *) die "usage: loadctl.sh stats|reset|rate <n>" ;;
esac
echo
