# 0003. Disruption budget, topology spread and autoscaler settings

Date: 2026-10-27. Status: accepted.

## Context

Steps 3 to 6 combine a rollout strategy, an autoscaler, a PodDisruptionBudget and topology spread. Each has a plausible setting that breaks another.

## Decisions

- **PDB `maxUnavailable: 1`**, not `minAvailable: 5`. The autoscaler moves the replica count between 6 and 20; a fixed minimum fits only one count.
- **Topology spread over `kubernetes.io/hostname`, `maxSkew: 1`, `DoNotSchedule`, `nodeTaintsPolicy: Honor`, `matchLabelKeys: [pod-template-hash]`.** Without `Honor`, the tainted control-plane node and any cordoned node count as empty domains, which caps every worker at one pod and leaves drained pods unschedulable. `matchLabelKeys` spreads each ReplicaSet's pods on their own, so a rollout does not skew the new pods.
- **HPA `behavior`.** Scale up may double every 15 seconds. Scale down waits a 60-second stabilization window and removes two pods every 30 seconds, so the lesson's last step takes minutes rather than the default stabilization of five minutes plus the policy's time.
- **The production overlay removes `spec.replicas`.** The autoscaler owns the count; the manifest stating a number would reset it on every apply.

## Consequences

`scripts/deploy.sh production` on a running system leaves the replica count with the autoscaler, but a first apply starts at one replica until the autoscaler raises it to its minimum of six (the script waits for six ready pods). The scale-down timings quoted in the README are for these settings, not Kubernetes defaults.
