# 0001. The load generator runs in the cluster, open-loop, and counts every error by class

Date: 2026-10-27. Status: accepted.

## Context

The lesson's only verdict is the load generator's error count during a rollout, a spike, a drain and a scale-down. A verdict is only as good as the generator: it has to meet the same routing as real clients, keep sending when the service struggles, and say what kind of failure it saw.

## Decision

- It runs as a Deployment on the server node and sends to `web.zero-downtime.svc`, so each request goes through kube-proxy to whichever ready pod is next. It never runs through `kubectl port-forward`, which tunnels to a single pod.
- It is open-loop: `Pacer` fixes the send times in advance, so a slow response does not slow the next send. If every sender thread is busy when a request is due, the request is counted as `skipped`, never queued; a result with `skipped > 0` is not valid, and the tests assert it is zero.
- Every request uses a new connection. Each one meets a fresh routing decision, which is what exposes a pod that is not ready or is going away.
- It counts `late_starts`: requests that began more than 0.1 s after they were due because its own threads were held up. Like `skipped`, a window with late starts is not a valid verdict, and the cluster test measures such a rollout again.
- It never reuses a source port that failed in the last 125 seconds. Linux keeps a half-open connection in its connection-tracking table for 120 seconds, remembering which pod the Service chose; a later connection from the same source port is matched to that entry and sent to the same dead pod. Without this rule, the failures of the naive step made the graceful step look as if it lost 0.1 to 0.4% of its requests for about two minutes (proved by flushing the table, which made the same rollouts clean).
- It counts `connection`, `timeout`, `http_5xx` and `http_other` separately, with p50, p99 and max latency per window, and exposes them over a small control API (`/stats`, `/reset`, `/rate`).
- It is pinned to the server node (the one labeled `node-role.kubernetes.io/control-plane`), which is never drained.

## Alternatives considered

- **The lesson's `load.py` on the host through a port-forward.** Cannot work: the port-forward pins to one pod.
- **A closed loop (one request at a time per worker).** Throughput falls when latency rises, so a stall looks like fewer requests instead of errors.
- **Keep-alive connections.** Realistic, but failures then depend on how long a connection lives, which is a separate lesson (connection draining).
- **`hey` or `k6` in the cluster.** Good tools, but they report a rate and a status histogram at the end of a run; this project needs counters it can read and reset between operations, and the load generator is part of what the repository teaches.

## Consequences

The generator and the service share the cluster's CPU, so very high rates would make the generator itself the bottleneck; `skipped` shows when that happens. The generator is a second image and a second package with no code shared with the service.
