# kubernetes-zero-downtime

A small HTTP service on a four-node k3d cluster, under constant load from an in-cluster load generator that counts every failed request, rolled, scaled, drained and scaled down until each of the four operations finishes with zero errors.

| | |
| --- | --- |
| **Lesson** | `/prep/kubernetes/project-zero-downtime`, "Roll out, scale and drain with zero errors" (`data-structure-explainer/src/content/tracks/kubernetes/13-project-zero-downtime.md` in the Plainround app) |
| **Planned hours** | 10 |
| **Level** | Intermediate |
| **Architecture** | [ARCHITECTURE.md](ARCHITECTURE.md), decisions in [docs/adr](docs/adr) |

## Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/architecture-dark.svg">
  <img alt="In a k3d cluster, a load generator on the tainted server node sends 100 requests a second to Service web, which routes only to ready web pods on worker nodes 1, 2 and 3, while the HPA scales the pods from 6 to 20 and the disruption budget allows one voluntary disruption at a time." src="docs/diagrams/architecture.svg">
</picture>

The generator counts every failed request; the amber notes are the naive overlay that refuses connections and the graceful overlay that ends at zero errors.

## How one run flows

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/flow-dark.svg">
  <img alt="One rollout under load: the generator resets its counters, the Deployment adds pods that join Service web only when ready, an old pod is deleted and drains through preStop and SIGTERM, and the generator reads errors: 0." src="docs/diagrams/flow.svg">
</picture>

The amber row is the naive setup sending traffic to a pod that is still warming up; with readiness and preStop the run ends with no errors.

## Prerequisites

| Tool | Version | Used for |
| --- | --- | --- |
| Docker | 24 or newer (tested with Docker Desktop 24.0.6 on an Apple-silicon Mac) | the k3d cluster (k3s in containers) and building the images |
| kubectl | within one minor version of the cluster (tested with 1.35.3 against Kubernetes 1.35.8) | everything in the cluster |
| Python | 3.12 or 3.13 (tested with 3.13; the CI uses 3.12) | the tests and the lint tools |
| GNU Make | 3.81 or newer (the one on macOS works) | every command below |
| k3d | v5.9.0, downloaded by `make tools` into `.bin/` with its SHA-256 checked (nothing is installed system-wide) | the local cluster |
| kubeconform | v0.8.0, downloaded the same way | schema checks of the manifests |
| metrics-server | the one k3s bundles (nothing to install) | the autoscaler's CPU readings |
| Cluster image | `rancher/k3s:v1.35.8-k3s1` (Kubernetes 1.35.8), set in `cluster/k3d-config.yaml` | pulled by Docker on first `make up` |
| Python packages | Flask and gunicorn at run time (the load generator uses only the standard library); pytest, ruff, mypy, import-linter, yamllint, shellcheck-py, pip-audit for development, all pinned with hashes in `requirements.lock` and `requirements-dev.lock`; `make` installs them into `.venv/` | the service and the checks |

No cloud account and no secrets. The cluster has its own kubeconfig (`.kube/config`, ignored by git); this project never reads or changes `~/.kube/config`. Plan on about 3 GB of memory for the four cluster nodes and up to 20 web pods, and a machine that is not busy with much else: the verdict is a count of failed requests, and a starved machine can add failures that have nothing to do with the manifests (the load generator's `skipped` counter and the tests' steady-state check catch the common cases).

## Setup, run, test, stop

```bash
make up                  # creates the cluster (1 server node, 3 agent nodes), builds and imports both images, deploys the finished service (overlay production) and the load generator
make run                 # three rolling updates in a row under 100 requests a second; prints the load generator's counters after each
make deploy OVERLAY=naive        # step 1's deployment: no probes, no preStop; then make run shows the errors (graceful, rolling-tuned and production are the later steps)
make stats               # the load generator's counters since its last reset
make rate RPS=300        # triple the load; watch kubectl -n zero-downtime get hpa web -w
make drain               # drain the worker holding the most web pods, under load, and print the error count
make test                # unit tests with coverage and the manifest policy checks (no cluster needed)
make lint                # ruff, format check, mypy, import rules, yamllint, shellcheck, hadolint, kubeconform
make smoke               # all four operations on the cluster, every Done when line (about 20 minutes; SPIKE_SECONDS=120 shortens the spike)
make test-integration    # the real gunicorn command: the startup delay, graceful shutdown, and the load generator program
make audit               # known-vulnerability check of the locked dependencies
make down                # deletes the cluster and its kubeconfig
```

`make help` lists every target. `make up` is idempotent. The first run pulls the k3s image (about 250 MB) and the Python base image.

## Done when: the lesson's checklist and the proof for each line

Every line is also checked by an automated cluster test, [tests/e2e/test_done_when.py](tests/e2e/test_done_when.py), which `make smoke` runs; its output is the evidence quoted here. Numbers are from runs on an Apple-silicon Mac (details under "Verification status"); yours will differ, the zero-error outcomes after the fixes should not.

| # | Done when (from the lesson) | Command | What proves it |
| --- | --- | --- | --- |
| 1 | **Measure the naive rollout.** You have the number of failed requests during the rollout written down, and it is above zero. | `make deploy OVERLAY=naive`, `scripts/rollout-under-load.sh 1` | A naive rollout under 100 requests a second failed **124** of 1,133 requests (94 connection errors, 30 timeouts): roughly one in nine, mostly `connection refused` from pods that were sent traffic before their port opened. [`test_done_when_1_the_naive_rollout_fails_requests`](tests/e2e/test_done_when.py) |
| 2 | **Add readiness and graceful shutdown.** A rollout under load completes with zero failed requests, three times in a row. | `make deploy OVERLAY=graceful`, `make run` | Three rollouts in a row, each under 100 requests a second, each with `errors: 0`: 1,713, 1,601 and 1,649 requests, p99 200 to 201 ms. The same test, run again after the naive step three more times, passed three for three (nine rollouts, no failed request). [`test_done_when_2_a_graceful_rollout_has_zero_errors_three_times`](tests/e2e/test_done_when.py) |
| 3 | **Tune the rollout.** A rollout of 6 replicas keeps at least 6 ready pods the whole time, checked with a watch on ready counts. | `make deploy OVERLAY=rolling-tuned`; `kubectl -n zero-downtime get deployment web -w` during `make run` | The ready count was sampled every 0.4 s during a rollout of 6 replicas and never read below 6 (`lowest ready count sampled 6`), with 2,085 requests and `errors: 0`; the strategy is `maxSurge: 25%`, `maxUnavailable: 0`. [`test_done_when_3_a_tuned_rollout_never_drops_below_six_ready`](tests/e2e/test_done_when.py) |
| 4 | **Autoscale on a spike.** Replicas grow, p99 latency returns near baseline, and the error count stays at zero; you also record how long scaling took. | `make deploy OVERLAY=production`, `make rate RPS=300`, `kubectl -n zero-downtime get hpa web -w`, `make stats` | At 100 requests a second the average CPU reading was 52% of the request (target 60%; other runs read 37 to 40%). After the rate went to 300 the autoscaler decided to grow after 40 s, replicas went from 6 to 9, the first extra pod was ready after 40 s, errors stayed at 0 for the whole five minutes and the median p99 of the last minute was 200 ms against a 202 ms baseline (the worst single ten-second window was 1,172 ms). [`test_done_when_4_the_autoscaler_absorbs_a_spike_with_zero_errors`](tests/e2e/test_done_when.py) |
| 5 | **Drain a node safely.** The drain completes with zero failed requests, and pods are spread so that no single node held more than half of them. | `make drain` | `kubectl drain` of the busiest worker evicted its pods one at a time (the disruption budget refused at least one eviction at first) and the generator counted `errors: 0` of 3,602 requests; pods per node were [3, 3, 3] before (no node held more than half of 9) and [5, 4] on the two remaining workers after. [`test_done_when_5_a_drain_has_zero_errors_and_no_node_holds_over_half`](tests/e2e/test_done_when.py) |
| 6 | **Scale down without errors.** Scale-down completes with zero failed requests, and the README has a table of all four operations, before and after, with the error counts. | `make rate RPS=100`, then watch `kubectl -n zero-downtime get hpa web -w` and `make stats`; the table is below | After the rate returned to 100 the autoscaler took the Deployment from 9 pods to 6 in 86 s (60 s stabilization, two pods per 30 s) and the generator counted `errors: 0` of 9,413 requests. The table below has all four operations. [`test_done_when_6_a_scale_down_has_zero_errors`](tests/e2e/test_done_when.py) |

### The table of all four operations

Written by `make smoke` to `logs/results.md` (this is the last full run, 2026-11-02: 6 passed in 663 s):

| operation | before (naive) | after (fixed) |
| --- | --- | --- |
| rolling update | 124 failed requests (94 connection errors, 30 timeouts) | 0 failed requests, three rollouts in a row (1,713, 1,601 and 1,649 requests); the tuned rollout never dropped below 6 ready pods |
| traffic spike (3x) | N/A (not run naive) | 0 errors; replicas 6 to 9; HPA decided after 40 s, first extra pod ready after 40 s; p99 202 ms baseline, 200 ms once scaled |
| node drain | N/A (not run naive) | 0 errors; evictions paused by the budget; pods per node [3, 3, 3] before, [5, 4] after |
| scale-down | N/A (not run naive) | 0 errors; 9 to 6 pods in 86 s (stabilization 60 s, 2 pods per 30 s) |

Only the rolling update has a naive measurement (the lesson's step 1). The other three operations are shown with the finished manifests; running them against the naive deployment would only repeat the same readiness and shutdown failures.

### Why each fix is there

- **Readiness probe.** A pod joins the Service's endpoints only after `/readyz` answers. Without it a new pod is "ready" the moment its container starts, which is before the one-second warm-up has opened the port. The load generator then sees `connection refused` for roughly a second per new pod.
- **`preStop` sleep.** When a pod is deleted, two things start at once: the endpoints controller removes it from the Service, and the kubelet starts stopping it. Removing the pod from kube-proxy's rules on every node takes a moment longer than stopping it. The fifteen-second sleep keeps the pod serving while that propagates. It started at five seconds, which is enough on an idle machine; on a busy laptop rollouts then lost a few requests to timeouts and a drain lost over two hundred, so it was raised to fifteen and the losses went to zero.
- **Graceful shutdown and the grace period.** gunicorn stops accepting on SIGTERM and finishes the requests in flight (its graceful timeout is 10 seconds). The grace period (40 seconds) counts the `preStop` sleep too, so it must cover 15 + 10 with margin; if it is shorter, SIGKILL arrives mid-shutdown and the cut-off requests are counted as errors.
- **`maxUnavailable: 0`, `maxSurge: 25%`.** Old pods are removed only after new ones are ready, so capacity never dips below six.
- **CPU request and the autoscaler.** Utilization is a percentage of the request (150m). Each request computes for 2 ms, so CPU follows load and the autoscaler has something to respond to; the measured baseline is about 38% of the request, so the threefold spike takes the average to about 115% and the autoscaler to 12 pods.
- **Disruption budget and topology spread.** The budget (`maxUnavailable: 1`) lets a drain evict one web pod at a time. Spread across nodes (with `nodeTaintsPolicy: Honor`) means no node holds more than half the pods, so a drain never removes most of the service at once.
- **Scale-down stabilization.** The autoscaler waits 60 seconds of low load before removing pods (the default is five minutes) and removes at most two every 30 seconds; those pods leave through the same graceful shutdown.

## Verification status

Verified on 2026-11-02 on a k3d cluster (one server, three workers) on a laptop that was shared with other work. Unit, policy and integration tests, lint and kubeconform pass, and the last full `make smoke` run passed all six tests (663 s). The runs before it found, and the repository now contains the fix for, each of these:

- **A bug the policy tests did not see.** The autoscaler and the disruption budget are new objects of the `production` overlay, outside the base's `namespace:` line, so they were created in the `default` namespace and the Deployment never scaled. The overlay now sets its own namespace and a policy test checks every object of every step.
- **`preStop` sleep 5 s was too short on a busy machine.** A drain then lost 221 requests (164 of them connection errors). At 15 s (grace period 40 s) the drain, the tuned rollout and the scale-down each counted `errors: 0` in repeated runs.
- **CPU request 100m put the baseline at 60% of the request**, exactly on the autoscaler's target, and the spike drove it to its maximum of 20 pods. At 150m the baseline is 37 to 52% and the spike settles at 9 to 14 pods.
- **A ten-second window's p99 is a noisy statistic** (the third-slowest of about 300 requests), so "p99 returns near baseline" compares the median of the last minute with twice the baseline.
- **The port-forward to the load generator can die under load**; the test client reconnects.
- **The graceful rollouts lost 2 to 8 requests, all client timeouts, in most runs (Done when 2).** The cause was in the measurement, not in the manifests. When a connection fails, Linux keeps a half-open entry in its connection-tracking table for 120 seconds, remembering which pod the Service chose. A later connection from the same source port is matched to that entry and goes to the same dead pod, so it times out too. The naive step produces hundreds of such failures, and for the next two minutes about one request in 300 reused one of those ports, which made the graceful rollouts look as if they lost requests. Evidence: with no rollouts at all, the steady state after the naive step showed the same timeouts, decaying over about 120 seconds; flushing the table's half-open entries right after the graceful deploy made a two-minute quiet window and three rollouts clean; and a `preStop` of 30 s, a guaranteed CPU core for the generator and `minReadySeconds` did not remove them. The load generator now never reuses a source port that failed within the last 125 seconds, and it also counts `late_starts` (requests that began more than 0.1 s after they were due because its own threads were held up); the cluster test measures a rollout window with late starts again. After the change, three runs of the naive step followed by three graceful rollouts passed three for three, and then the full run above.

## Stretch goals

| Stretch (from the lesson) | Built? |
| --- | --- |
| Use a startup probe with a 30-second startup and show what liveness alone would have done. | No. The `STARTUP_DELAY_SECONDS` setting and the `graceful` overlay are where it would go; [ARCHITECTURE.md](ARCHITECTURE.md) describes it under "How to extend it". |
| Add a second container as a native sidecar and check that it starts before and stops after the app. | No. |
| Replace CPU-based autoscaling with requests per second from a custom metric. | No. It needs a metrics adapter (Prometheus and its adapter) that this repository does not install. |

## What is simplified for teaching

Only what the lesson scopes out:

- The "nodes" are containers on one machine, so latencies and the autoscaler's timing depend on the machine's load. The load generator and the cluster share the machine's CPU.
- One Service and no ingress or cloud load balancer in front of it, so there is no external deregistration delay to line up with.
- New connection per request, no keep-alive; real clients reuse connections.
- The service is stateless and the images are built locally and loaded into the nodes (no registry).
- The service does its CPU work in a busy loop so the autoscaler has a signal; a real service's CPU use comes from its work.

## Project structure

```text
src/zero_downtime/       the web service
  domain/                the request profile (latency bounds, how much of it is computing) and named errors.
                         Depends on nothing outside the standard library.
  application/           HandleRequest and WarmUp, and the ports they need (Randomness, Waiter, Burner).
                         Depends on domain only.
  infrastructure/        the real clock, sleep and busy loop; settings from the environment; JSON logging.
  interfaces/            the Flask routes and the composition root gunicorn calls.
src/loadgen/             the load generator, with the same four layers
  domain/                Outcome, Pacer (fixed-rate schedule) and StatsWindow (counters and percentiles).
  application/           LoadRunner, and the ports it needs (Requester, Clock, Executor).
  infrastructure/        the HTTP requester, the thread pool, settings, logging.
  interfaces/            the control API and the program.
tests/
  unit/                  both programs, with fakes for time and I/O. No Docker, no cluster.
  policy/                the rendered overlays: probes, preStop, grace period against the Dockerfile's graceful
                         timeout, rollout settings, the autoscaler, the budget, topology spread; and what each step adds.
  integration/           the real gunicorn command (startup delay, graceful shutdown) and the load generator program.
  e2e/                   all four operations on the k3d cluster, with the load generator as the judge.
manifests/
  base/                  namespace, service accounts, the web Deployment and Service, the load generator.
  overlays/              naive, graceful, rolling-tuned and production: the lesson's steps, each adding one thing.
scripts/                 tools.sh, cluster-start.sh, build-image.sh, deploy.sh, loadctl.sh,
                         rollout-under-load.sh, drain-busiest-node.sh.
cluster/                 the k3d cluster definition (1 server node, 3 agent nodes).
docs/adr/                decision records (the load generator, the warm-up, the budget and spread settings).
Dockerfile               multi-stage; targets `service` and `loadgen`; non-root, base image pinned by digest.
Makefile                 the only commands a reader needs (make help).
```

The dependency rule (domain depends on nothing; application on domain; infrastructure and interfaces on both; nothing on interfaces) is enforced by `make lint` through import-linter, separately for each program, and a further contract states that the service and the load generator share no code.

## Troubleshooting

- **The naive rollout shows 0 errors:** the machine was too slow for the load generator to keep up, or the cluster was not at steady state. Check `make stats` shows `"skipped": 0` and re-run `scripts/rollout-under-load.sh`.
- **`make up` hangs pulling an image:** Docker Desktop's credential helper can block `docker pull` when many pulls run at once. Pull with an empty client configuration instead: `mkdir -p /tmp/anon-docker && echo '{}' > /tmp/anon-docker/config.json`, then `DOCKER_HOST=unix://$HOME/.docker/run/docker.sock docker --config /tmp/anon-docker pull rancher/k3s:v1.35.8-k3s1`; the image lands in the normal image store.
- **Pods stay `Pending` and a node has a `disk-pressure` taint:** Docker Desktop's disk is full. `cluster/k3d-config.yaml` already relaxes the kubelet's disk thresholds; free space with `docker system prune` if the disk is nearly full.
- **`make up` fails creating the cluster:** run `make down`, make sure Docker has memory to spare, and run `make up` again.
- **The autoscaler shows `<unknown>`:** the bundled metrics-server is not ready yet; wait for `kubectl top nodes` to answer.
- **A drain is stuck:** a pod cannot be rescheduled. Check `kubectl -n zero-downtime get pods -o wide` and that the topology spread has `nodeTaintsPolicy: Honor`.

## Author and license

Written by Sumit Srivastava. Released under the MIT License, see [LICENSE](LICENSE). Changes are in [CHANGELOG.md](CHANGELOG.md).
