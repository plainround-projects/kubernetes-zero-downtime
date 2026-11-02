# PodDisruptionBudget, topology spread and draining a node

## 1. What it is

Sometimes a human or a tool takes a node out of service on purpose: a kernel upgrade, a hardware swap, a node-pool replacement. This is a **voluntary disruption**. Kubernetes handles it with:

- **Cordon**: mark the node unschedulable so no new pods land on it.
- **Drain**: cordon, then **evict** (politely delete) the pods running there. `kubectl drain <node>` does both.
- A **PodDisruptionBudget (PDB)** is a promise a service owner makes about those evictions: "at most N of my pods may be unavailable because of voluntary disruptions". An eviction that would break the promise is refused (the API answers "429 Too Many Requests"), and `kubectl drain` retries until it is allowed. `maxUnavailable: 1` states it as a number; the alternative `minAvailable: 5` would be wrong once the autoscaler changes the replica count.
- **Topology spread constraints** tell the scheduler how to spread a Deployment's pods over **topology domains** (here, one domain per node, using the label `kubernetes.io/hostname`): `maxSkew: 1` means the counts of any two nodes may differ by at most one.

## 2. Why this repository uses it

The lesson's fifth step: draining one worker node under constant load must lose no requests and must be possible only because the budget holds evictions back; and no node may hold more than half of the web pods, or draining that node would remove too much at once. Spreading the pods is what turns the budget from a rule into an outcome.

## 3. Where it appears

| File | Role |
| --- | --- |
| `manifests/overlays/production/pdb.yaml` | The budget. |
| `manifests/overlays/production/kustomization.yaml` | Adds the topology spread constraint to the Deployment. |
| `scripts/drain-busiest-node.sh`, `make drain` | Drain the worker with the most web pods. |
| `tests/e2e/test_done_when.py` | The drain test. |
| `cluster/k3d-config.yaml` | Taints the server so only the three agents hold web pods. |

## 4. How it works underneath

`kubectl drain` first cordons the node, then asks the API server to create an **Eviction** for each pod. The API server checks each pod's PDB: with `maxUnavailable: 1` and all pods Ready, one eviction is allowed; the next is refused until the replacement pod elsewhere is Ready. The evicted pod then follows the normal termination path (`preStop`, `SIGTERM`; see `rolling-updates.md`), so a drain is only free of errors if graceful shutdown is already right.

The scheduler places the replacement according to the topology spread constraint. `whenUnsatisfiable: DoNotSchedule` makes the rule hard. `nodeTaintsPolicy: Honor` tells it to ignore tainted nodes when counting domains: without it the control-plane node (tainted, so unusable for web pods) counts as an empty domain and caps every worker at one pod; a cordoned node during a drain does the same. `matchLabelKeys: [pod-template-hash]` makes the rule apply per revision, so during a rollout the old and new ReplicaSets are spread independently.

## 5. Walkthrough of this repository's use

### `manifests/overlays/production/pdb.yaml`

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: web
  labels:
    app.kubernetes.io/name: web
    app.kubernetes.io/part-of: zero-downtime
spec:
  # Stated as "at most one pod unavailable" rather than "at least five available", so it stays right when
  # the autoscaler changes the replica count.
  maxUnavailable: 1
  selector:
    matchLabels:
      app: web
```

`apiVersion: policy/v1`, `kind: PodDisruptionBudget`, named `web` with labels. The comment explains the form: stated as "at most one pod unavailable" rather than "at least five available", so it stays right when the autoscaler changes the replica count. `maxUnavailable: 1` and a `selector` with `matchLabels: app: web` choose the protected pods.

### `manifests/overlays/production/kustomization.yaml`

```yaml
# Steps 4 to 6: autoscaling, a disruption budget and an even spread over the nodes. This is the finished
# service and the overlay `make up` deploys.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
# The autoscaler and the budget below are new objects of this overlay, outside the base: without this line they
# would be created in whatever namespace kubectl defaults to, and the Deployment would never be scaled.
namespace: zero-downtime
resources:
  - ../rolling-tuned
  - hpa.yaml
  - pdb.yaml
patches:
  # The HorizontalPodAutoscaler owns the replica count from here on; a replicas field in the manifest would
  # reset it on every apply.
  - target:
      kind: Deployment
      name: web
    patch: |-
      - op: remove
        path: /spec/replicas
      - op: add
        path: /spec/template/spec/topologySpreadConstraints
        value:
          - maxSkew: 1
            topologyKey: kubernetes.io/hostname
            whenUnsatisfiable: DoNotSchedule
            # Without this, the control-plane node (tainted, so unusable) counts as an empty domain and
            # caps every worker at one pod; a cordoned node during a drain does the same.
```

The comment says this is steps 4 to 6: autoscaling, a disruption budget and an even spread over the nodes; this is the finished service and the overlay `make up` deploys. A comment explains the line `namespace: zero-downtime`: the autoscaler and the budget are new objects of this overlay, outside the base, and without this line they would be created in whatever namespace kubectl defaults to and the Deployment would never be scaled. `resources:` lists `../rolling-tuned` (step 3) and the two new files. The patch on the Deployment `web` has two operations. `op: remove` with `path: /spec/replicas` deletes the fixed replica count: the comment says the autoscaler owns it from here on, and a `replicas` field would reset it on every apply. `op: add` with `path: /spec/template/spec/topologySpreadConstraints` adds one constraint: `maxSkew: 1`, `topologyKey: kubernetes.io/hostname` (one domain per node), `whenUnsatisfiable: DoNotSchedule` (a hard rule),

```yaml
            nodeTaintsPolicy: Honor
            labelSelector:
              matchLabels:
                app: web
            matchLabelKeys:
              - pod-template-hash
```

`nodeTaintsPolicy: Honor` (the comment: without it the control-plane node, tainted and so unusable, counts as an empty domain and caps every worker at one pod; a cordoned node during a drain does the same), a `labelSelector` choosing the `app: web` pods and `matchLabelKeys: [pod-template-hash]`, which makes the rule apply per revision so old and new ReplicaSets are spread independently during a rollout.


## 6. Try it yourself

```bash
make up
kubectl -n zero-downtime get pods -l app=web -o wide | awk 'NR>1 {print $7}' | sort | uniq -c
```

Two pods on each of the three agent nodes (never more than half of six on one).

```bash
make drain
```

Prints the pods per node, drains the busiest worker (you can see `evicting pod ...` lines and, if the budget holds one back, `Cannot evict pod as it would violate the pod's disruption budget`), prints `errors counted during the drain:` with `"errors": 0` and the pods per node afterwards (the drained node has none; the others hold more). The script uncordons the node at the end.

```bash
kubectl -n zero-downtime get pdb web
```

`MAX UNAVAILABLE 1`, `ALLOWED DISRUPTIONS 1` when everything is Ready.

## 7. Mistakes people make

- **Draining with no budget.** Every pod of the service may be evicted at once.
- **A budget that allows nothing** (`minAvailable` equal to the replica count). Drains then hang for ever.
- **Spreading by zone only.** With one node per zone that is fine; with several pods on a node the node is the failure domain.
- **Forgetting taints in the spread rule.** The control-plane node then counts as an empty domain.
- **Leaving the node cordoned.** The script uncordons in a `trap`.

## 8. Check yourself

1. What does `kubectl drain` do in two steps? *Cordons the node, then evicts its pods.*
2. What happens to an eviction that would break the budget? *It is refused and retried until it is allowed.*
3. Why `maxUnavailable` and not `minAvailable`? *It stays right when the autoscaler changes the replica count.*
4. What does `maxSkew: 1` mean? *Any two nodes differ by at most one web pod.*
5. Why does the test require that no node hold more than half the pods? *Otherwise draining that node would take away more than the service can lose.*

## 9. Further reading

- Disruptions and PodDisruptionBudgets: https://kubernetes.io/docs/concepts/workloads/pods/disruptions/
- Specifying a disruption budget: https://kubernetes.io/docs/tasks/run-application/configure-pdb/
- Safely draining a node: https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/
- Pod topology spread constraints: https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/
