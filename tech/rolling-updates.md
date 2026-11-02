# Rolling updates and graceful shutdown (the first three steps)

## 1. What it is

A **rolling update** replaces the pods of a Deployment a few at a time, so the service keeps answering while the new version comes in. Done naively it still drops requests. This note explains why, and the three settings that make it lose none: **readiness probes**, a **graceful shutdown** (a `preStop` delay plus a long enough grace period) and a **rollout strategy** that never lowers capacity.

Words used here:

- A **readiness probe** is a check the kubelet runs on a container (here an HTTP request to `/readyz`). While it fails, the pod is *not Ready* and the Service sends it no traffic.
- A **Service** keeps a list of **endpoints**, the addresses of the Ready pods. On every node a program called **kube-proxy** turns that list into packet-forwarding rules (iptables rules in k3s). Each new connection is sent to one endpoint chosen from the rules.
- When Kubernetes deletes a pod it does two things *at the same time*: it marks the pod **Terminating** (the endpoint is removed from the Service, and every node's kube-proxy must learn about it) and it starts stopping the containers. There is no order between them.
- The **grace period** (`terminationGracePeriodSeconds`) is how long Kubernetes waits after asking a container to stop before killing it with `SIGKILL`. Stopping begins with the `preStop` hook (a command or a sleep), then `SIGTERM` to the main process; the hook's time counts against the grace period.
- **`maxSurge`** is how many extra pods a rollout may create above the desired number; **`maxUnavailable`** is how many may be missing. Both default to 25 percent.

## 2. Why this repository uses it

The lesson's headline measurement is the error count of a rolling update under 100 requests a second. The three overlays are the three steps of the answer, and the tests check each step with the load generator's counters: the naive step fails requests, the graceful step and the tuned step lose none.

## 3. Where it appears

| File | Step |
| --- | --- |
| `manifests/overlays/naive/kustomization.yaml` | Step 1: the base as it is (no probes, no `preStop`, defaults). |
| `manifests/overlays/graceful/kustomization.yaml` | Step 2: readiness and liveness probes, a `preStop` sleep and a grace period long enough for both. |
| `manifests/overlays/rolling-tuned/kustomization.yaml` | Step 3: `maxUnavailable: 0`, so capacity never drops. |
| `scripts/rollout-under-load.sh`, `tests/e2e/test_done_when.py` | Run and verify them (see `bash.md`, `tests-e2e.md`). |
| `tests/policy/test_manifests.py` | Checks each step adds only what the lesson says. |

## 4. How it works underneath

**Why the naive rollout fails.** The new pod starts: gunicorn builds the app, which runs the one-second warm-up *before* opening its port, so for that second the port refuses connections. Without a readiness probe Kubernetes considers a pod Ready as soon as its container has started, so the Service sends it traffic into a closed port: `connection` errors. At the other end, the old pod is told to stop at once; for a moment kube-proxy on other nodes still forwards new connections to it, and the pod is gone: more errors.

**Step 2 (graceful).** A readiness probe (`/readyz`, every 2 seconds, two failures to be not-ready) keeps a new pod out of the Service until it can answer. A `preStop` hook that does `sleep 15` keeps the old pod alive and *serving* for fifteen seconds after it is marked Terminating, which gives every node time to learn that it is gone; only then does `SIGTERM` arrive and gunicorn stops accepting and finishes its requests in flight (`--graceful-timeout 10`). The grace period (40 seconds) must cover both: 15 + 10 = 25 seconds of work, with margin. The sleep must be longer than the slowest node takes to update its rules; on an idle laptop one second would do, on a busy machine five seconds was measured to be too short (requests timed out against pods that had already gone), which is why the value here is 15.

**Step 3 (tuned).** With `maxUnavailable: 0` and `maxSurge: 25%` (two extra pods out of six), Kubernetes only removes an old pod after a new one is Ready, so the number of ready pods never falls below six.

A **JSON6902 patch** (`op: add`, `path`, `value`) in each overlay adds the fields; the paths use list indexes such as `containers/0`. Each overlay builds on the previous one (`resources: - ../graceful`).

## 5. Walkthrough of this repository's use

### `manifests/overlays/naive/kustomization.yaml`

```yaml
# Step 1: the naive deployment. No probes, no preStop, defaults for the rollout. A rolling update under load
# drops requests; the load generator counts them.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
```

The comment says step 1 is the naive deployment: no probes, no `preStop`, defaults for the rollout; a rolling update under load drops requests and the load generator counts them. The file is a Kustomization (see `kustomize.md`) that only lists `../../base` as its resource: nothing is added to the base, so the base's lack of probes and hooks *is* the step.

### `manifests/overlays/graceful/kustomization.yaml`

```yaml
# Step 2: readiness, a preStop delay and a grace period long enough for both. The 15-second sleep must outlast
# the time every node's kube-proxy needs to drop the pod from its rules: 5 seconds was measured to be too short on
# a loaded machine (requests timed out against pods that had already gone). 15 + gunicorn's 10-second graceful
# timeout fits the 40-second grace period.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../naive
patches:
  - target:
      kind: Deployment
      name: web
    patch: |-
      - op: add
        path: /spec/template/spec/terminationGracePeriodSeconds
        value: 40
      - op: add
        path: /spec/template/spec/containers/0/readinessProbe
        value:
          httpGet:
            path: /readyz
            port: http
          periodSeconds: 2
          timeoutSeconds: 2
          failureThreshold: 2
      - op: add
        path: /spec/template/spec/containers/0/livenessProbe
        value:
```

The comments explain step 2: readiness, a `preStop` delay and a grace period long enough for both; the 15-second sleep must outlast the time every node's kube-proxy needs to drop the pod from its rules, and 5 seconds was measured to be too short on a loaded machine; 15 plus gunicorn's 10-second graceful timeout fits the 40-second grace period. `resources: - ../naive` builds on step 1. One JSON6902 patch on the Deployment `web` makes four additions with `op: add`. First `terminationGracePeriodSeconds: 40`. Second, a `readinessProbe` on the first container: an HTTP GET of `/readyz` on the port named `http`, every 2 seconds (`periodSeconds`), with a 2-second timeout and two consecutive failures (`failureThreshold: 2`) to be marked not ready.

```yaml
          httpGet:
            path: /healthz
            port: http
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 2
      - op: add
        path: /spec/template/spec/containers/0/lifecycle
        value:
          preStop:
            sleep:
              seconds: 15
```

The `livenessProbe` value continues: an HTTP GET of `/healthz` on the port `http`, starting after 5 seconds (`initialDelaySeconds`, because the port is closed during the one-second warm-up) and repeating every 10 seconds. 
Fourth, `lifecycle.preStop.sleep.seconds: 15`: a hook using Kubernetes' native `sleep` action (available since 1.30), which needs no shell or `sleep` program in the image.

### `manifests/overlays/rolling-tuned/kustomization.yaml`

```yaml
# Step 3: capacity never drops during a rollout. maxUnavailable 0 means an old pod is removed only after a
# new one is Ready; maxSurge 25% of six rounds up to two extra pods at a time.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../graceful
patches:
  - target:
      kind: Deployment
      name: web
    patch: |-
      - op: add
        path: /spec/strategy
        value:
          type: RollingUpdate
          rollingUpdate:
            maxSurge: 25%
            maxUnavailable: 0
```

The comment explains step 3: capacity never drops during a rollout. `maxUnavailable: 0` means an old pod is removed only after a new one is Ready, and `maxSurge: 25%` of six rounds up to two extra pods at a time. `resources: - ../graceful` builds on step 2. The patch adds `/spec/strategy` with `type: RollingUpdate` and a `rollingUpdate` block holding `maxSurge: 25%` and `maxUnavailable: 0`.


## 6. Try it yourself

```bash
make deploy OVERLAY=naive && make run
```

Three lines `rollout N of 3: {... "errors": 100-200, "connection": ...}`: the naive step drops requests, mostly connection errors.

```bash
make deploy OVERLAY=graceful && make run
```

`"errors": 0` in each of the three rollouts (each rollout now takes about a minute because each old pod sleeps 15 seconds).

```bash
make deploy OVERLAY=rolling-tuned
kubectl -n zero-downtime get deploy web -o jsonpath='{.spec.strategy}'
```

Prints `{"rollingUpdate":{"maxSurge":"25%","maxUnavailable":0},"type":"RollingUpdate"}`.

```bash
kubectl -n zero-downtime get pods -w
```

During a rollout, shows new pods `Running` but `0/1` until ready, and old pods `Terminating` for about fifteen seconds while still serving.

## 7. Mistakes people make

- **No readiness probe.** New pods get traffic before they can serve it.
- **No `preStop` delay.** The pod stops while other nodes still send it traffic.
- **A grace period shorter than the `preStop` sleep plus the shutdown time.** `SIGKILL` arrives mid-shutdown (the policy test checks this).
- **A `preStop` sleep shorter than the propagation time.** It works on an idle machine and fails under load.
- **Using `maxUnavailable` above zero for a service with no spare capacity.** A rollout then removes capacity first.
- **Judging a rollout by `kubectl rollout status` alone.** It reports pods, not the requests that failed; this project counts the requests.

## 8. Check yourself

1. Why does the naive rollout produce connection errors? *New pods are routed to before their port opens, and old pods are stopped while still receiving traffic.*
2. What does the `preStop` sleep achieve? *The terminating pod keeps serving while every node's rules stop sending it traffic.*
3. Why must the grace period exceed `preStop` plus gunicorn's graceful timeout? *The hook's time counts against the grace period; otherwise `SIGKILL` arrives mid-drain.*
4. What does `maxUnavailable: 0` guarantee? *An old pod is removed only after a new one is Ready.*
5. How does the project prove a step works? *Three rollouts in a row under 100 requests a second with the generator's error count at zero.*

## 9. Further reading

- Deployments and rolling updates: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-update-deployment
- Container lifecycle hooks: https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/
- Termination of pods: https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination
- Probes: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
- Services and kube-proxy: https://kubernetes.io/docs/reference/networking/virtual-ips/
