# Autoscaling (HorizontalPodAutoscaler)

## 1. What it is

A **HorizontalPodAutoscaler (HPA)** changes the number of replicas of a Deployment to follow load. Every 15 seconds a controller reads the pods' average CPU use (from the **metrics server**, a small service that collects it from each node's kubelet) and computes:

`desired replicas = ceil( current replicas x current utilization / target utilization )`

where **utilization** is CPU use as a percentage of the pods' CPU **request** (not of the node, not of the limit). Example from this lesson: six pods at 168 percent of a 100 millicore request with a 60 percent target gives `ceil(6 x 168 / 60) = 17`. The HPA then changes the Deployment's `replicas`. If the measured value is within 10 percent of the target, nothing changes (a tolerance that stops constant small changes). The HPA's **behavior** section tunes how fast it may grow and shrink.

"Horizontal" means more copies of the pod; "vertical" would mean bigger pods.

## 2. Why this repository uses it

The lesson's fourth step: a threefold traffic spike (100 to 300 requests a second) must be absorbed with zero errors, with the replica count growing and the latency returning near its baseline; and the fifth step, a scale-down, must also lose nothing. For the CPU metric to move with load, each request does 2 ms of real computation (`CPU_WORK_MS`), and the pods have a CPU request to measure against.

## 3. Where it appears

| File | Role |
| --- | --- |
| `manifests/overlays/production/hpa.yaml` | The autoscaler object. |
| `manifests/overlays/production/kustomization.yaml` | Adds it to the stack, removes the fixed `replicas` and sets the namespace. |
| `manifests/base/web.yaml` | The CPU request (100m) the percentage is measured against. |
| `scripts/loadctl.sh`, `Makefile` (`rate`) | Change the load. |
| `tests/e2e/test_done_when.py` | The spike test and the scale-down test. |

## 4. How it works underneath

The metrics server (bundled with k3s) scrapes every kubelet and answers `kubectl top` and the HPA. The HPA controller compares the average, over all ready pods, of `CPU use / CPU request` with the target.

**Scaling up** is allowed at once here (`stabilizationWindowSeconds: 0`) and by up to 100 percent more pods every 15 seconds, so six pods can become twelve in one step. **Scaling down** looks at the highest recommendation of the last 60 seconds and may remove at most two pods per 30 seconds, so a brief dip does not remove capacity that is needed again a minute later.

When an HPA owns the replica count, the Deployment manifest must *not* state `replicas`: every `kubectl apply` would reset it. That is why the production overlay removes the field with a patch. New pods take time to be scheduled, pulled and warmed up (one second of warm-up plus readiness), so an HPA reacts after a delay of tens of seconds; the test records it ("the HPA decided after N seconds, the first extra pod was ready after M seconds").

An HPA is a namespaced object and finds its target by name in *its own* namespace; an HPA created in the wrong namespace does nothing.

## 5. Walkthrough of this repository's use

### `manifests/overlays/production/hpa.yaml`

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web
  labels:
    app.kubernetes.io/name: web
    app.kubernetes.io/part-of: zero-downtime
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web
  minReplicas: 6
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
        - type: Percent
          value: 100
          periodSeconds: 15
```

`apiVersion: autoscaling/v2` and `kind: HorizontalPodAutoscaler`, named `web` with labels. `spec.scaleTargetRef` names what to scale: the Deployment `web` (apps/v1). `minReplicas: 6` and `maxReplicas: 20` bound the count. `metrics` has one entry of type `Resource` for `cpu` with `target.type: Utilization` and `averageUtilization: 60`: keep the average CPU use at 60 percent of the request. `behavior.scaleUp` starts: `stabilizationWindowSeconds: 0` (act on the latest reading at once) and one policy of type `Percent`, value 100, `periodSeconds: 15`: at most double the replica count every 15 seconds.

```yaml
    scaleDown:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 30
```

`behavior.scaleDown`: `stabilizationWindowSeconds: 60` (use the highest recommendation of the last minute, so a short dip removes nothing) and a policy of type `Pods`, value 2, every 30 seconds: remove at most two pods per half minute, each leaving through the graceful shutdown.


## 6. Try it yourself

```bash
make up
kubectl -n zero-downtime get hpa web
```

`cpu: 56%/60%` (or similar), `MINPODS 6`, `MAXPODS 20`, `REPLICAS 6`.

```bash
make rate RPS=300 && watch -n 5 'kubectl -n zero-downtime get hpa web'
```

Within a minute the target reads over 100 percent and `REPLICAS` climbs towards 16 or 17; afterwards `make stats` shows `errors: 0` and a p99 near its old value. Stop watching with Ctrl-C and restore the rate with `make rate RPS=100`; the replicas fall back to 6 in about five minutes.

```bash
kubectl -n zero-downtime describe hpa web | sed -n '/Events/,$p'
```

Shows each `SuccessfulRescale` event with the reason (`cpu resource utilization (percentage of request) above target`).

## 7. Mistakes people make

- **No CPU request.** The percentage cannot be computed; the HPA reports `<unknown>`.
- **A `replicas` field in the manifest as well.** Each apply fights the autoscaler.
- **Creating the HPA in the wrong namespace.** It sits at `<unknown>` and never scales; this project had exactly that bug, and a policy test now checks every object's namespace.
- **A target too close to the baseline.** At 56 percent baseline and a 60 percent target there is little headroom before a spike triggers growth, which is what the test wants; a service that idles at 55 percent of a request is simply under-requested.
- **Scaling down too fast.** Capacity removed during a dip is missing for the next peak; hence the 60-second window and the two-pods-per-30-seconds limit.

## 8. Check yourself

1. What is utilization measured against? *The CPU request of the pods.*
2. Why does each request burn 2 ms of CPU? *Waiting alone uses no CPU, so the metric would never move.*
3. Why remove `replicas` from the Deployment? *The autoscaler owns the count; a fixed value would be reset on every apply.*
4. How many replicas for 168 percent at a 60 percent target with six pods? *`ceil(6 x 168 / 60) = 17`.*
5. Why is scale-down slower than scale-up? *To avoid removing capacity that is needed again moments later.*

## 9. Further reading

- Horizontal Pod Autoscaling: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
- The algorithm: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#algorithm-details
- Metrics server: https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/
- Resource requests and limits: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
