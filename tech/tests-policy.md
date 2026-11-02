# Policy tests (manifests as data)

## 1. What it is

A **policy test** checks the Kubernetes manifests against rules ("every container has resource limits", "no Secret is committed") without a cluster. The test renders each Kustomize overlay with `kubectl kustomize` (see `kustomize.md`), reads the resulting YAML into Python dictionaries with PyYAML (`yaml.md`) and uses plain `assert` statements on those dictionaries. A **fixture** with `scope="session"` renders once and shares the result with all tests.

## 2. Why this repository uses it

A rule written only in a README gets broken. Here each property of the finished service is a test that fails the moment a manifest drifts: every container has requests and limits and is locked down in every step, the probes use different paths, the grace period covers the `preStop` sleep plus gunicorn's graceful timeout, the rollout never removes a ready pod before a new one is ready, the autoscaler owns the replica count, the budget selects the web pods, pods are spread over nodes. Two tests guard the lesson itself: each step adds only what the lesson says (step 2 changes only probes, `preStop` and grace period; step 3 only the strategy; step 6 only the spread), and every object of every step lives in the `zero-downtime` namespace. That second test was added after a real bug: the autoscaler and the budget, being new objects of the production overlay, were created in the `default` namespace and the Deployment never scaled.

## 3. Where it appears

| File | Role |
| --- | --- |
| `tests/policy/conftest.py` | Renders overlays; fixtures `production` and `overlays`; helpers `workloads_of` and `named`. |
| `tests/policy/test_manifests.py` | The rules. |
| `Makefile` | `make test` runs them with the unit tests. |

## 4. How it works underneath

`subprocess.run([...], capture_output=True, text=True, check=True)` starts the `kubectl` program, collects its output as text, and raises an error if its exit status is non-zero. `yaml.safe_load_all` parses a stream of documents separated by `---` into a sequence of dictionaries (`safe` means it refuses YAML that could construct arbitrary Python objects). `pathlib.Path` is Python's object for file paths; `.resolve().parents[2]` climbs two folders up from the test file to the repository root.

Kubernetes quantities such as `50m` (50 millicores) and `64Mi` (64 mebibytes, 64 x 1024 x 1024 bytes) are strings, so the test converts them to numbers with a regular expression (`re.fullmatch`) before comparing a request with a limit.

## 5. Walkthrough of this repository's use

### `tests/policy/conftest.py`

```text
"""Render the Kustomize overlays once per session so the policy tests read plain dictionaries."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
Doc = dict[str, Any]
```

The docstring says the overlays are rendered once per session. Imports: `subprocess`, `Path`, `Any`, `pytest`, `yaml` (PyYAML). `ROOT` is the repository folder and `Doc` names "one parsed object".

```text
def render(overlay: str) -> list[Doc]:
    """kubectl kustomize manifests/overlays/<overlay>, parsed into one dictionary per resource."""
    output = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "manifests" / "overlays" / overlay)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(output) if doc]


def workloads_of(docs: list[Doc]) -> list[Doc]:
    """The Deployments, StatefulSets and DaemonSets among rendered resources."""
    return [doc for doc in docs if doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet"}]


def named(docs: list[Doc], kind: str, name: str) -> Doc:
    """The one resource of a kind and name."""
    return next(doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name)
```

`render(overlay)` runs `kubectl kustomize manifests/overlays/<overlay>`, captures the text and returns the non-empty parsed objects. `workloads_of(docs)` keeps the Deployments, StatefulSets and DaemonSets. `named(docs, kind, name)` returns the first object of that kind and name (`next(...)` over a generator).

```text
@pytest.fixture(scope="session")
def production() -> list[Doc]:
    return render("production")


@pytest.fixture(scope="session")
def overlays() -> dict[str, list[Doc]]:
    return {name: render(name) for name in ("naive", "graceful", "rolling-tuned", "production")}
```

Two session fixtures: `production` renders that overlay once; `overlays` renders all four into a dictionary.

### `tests/policy/test_manifests.py`

```text
"""Policy checks on the rendered manifests: what the finished service must have, and what each step adds."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.policy.conftest import Doc, named, workloads_of

pytestmark = pytest.mark.policy

ROOT = Path(__file__).resolve().parents[2]


def _pod(workload: Doc) -> dict[str, Any]:
    spec: dict[str, Any] = workload["spec"]["template"]["spec"]
    return spec
```

The docstring states the purpose. Imports include `re`; `_pod(workload)` digs out `spec.template.spec`. `pytestmark` labels the file `policy`.

```text
def _web(docs: list[Doc]) -> Doc:
    return named(docs, "Deployment", "web")


def _quantity(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(m|Mi|Gi|Ki)?", str(value))
    assert match, f"unparsable quantity {value!r}"
    return float(match.group(1)) * {None: 1, "m": 0.001, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}[match.group(2)]
```

`_web(docs)` is the Deployment `web`. `_quantity` parses strings such as `100m` and `64Mi` into numbers (millicores to cores, binary suffixes to bytes) so a request can be compared with a limit.

```text
@pytest.mark.parametrize("overlay", ["naive", "graceful", "rolling-tuned", "production"])
def test_every_container_in_every_step_has_requests_and_limits_and_is_locked_down(overlays, overlay):
    for workload in workloads_of(overlays[overlay]):
        pod = _pod(workload)
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        assert pod["automountServiceAccountToken"] is False
        for container in pod["containers"]:
            resources = container["resources"]
            for resource in ("cpu", "memory"):
                assert _quantity(resources["requests"][resource]) <= _quantity(resources["limits"][resource])
            context = container["securityContext"]
            assert context["allowPrivilegeEscalation"] is False
            assert context["readOnlyRootFilesystem"] is True
            assert context["capabilities"]["drop"] == ["ALL"]
            assert not container["image"].endswith(":latest")
```

For each of the four overlays, every workload: non-root, default seccomp, no token; every container has requests no larger than limits for CPU and memory, no privilege escalation, a read-only root file system, all capabilities dropped and no `:latest` image.

```text
@pytest.mark.parametrize("overlay", ["naive", "graceful", "rolling-tuned", "production"])
def test_every_namespaced_object_of_every_step_is_in_the_zero_downtime_namespace(overlays, overlay):
    """The autoscaler and the budget are added by the production overlay itself; they must not land in `default`."""
    for doc in overlays[overlay]:
        if doc["kind"] != "Namespace":
            assert doc["metadata"].get("namespace") == "zero-downtime", (doc["kind"], doc["metadata"]["name"])


def test_the_finished_web_service_has_both_probes_on_their_own_paths(production):
    container = _pod(_web(production))["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
```

For each overlay, every object other than the Namespace has `namespace: zero-downtime` (the docstring explains the bug this guards). The finished web container has readiness on `/readyz` and liveness on `/healthz`.

```text
def test_the_grace_period_covers_the_prestop_sleep_plus_gunicorns_graceful_timeout(production):
    pod = _pod(_web(production))
    prestop = pod["containers"][0]["lifecycle"]["preStop"]["sleep"]["seconds"]
    dockerfile = (ROOT / "Dockerfile").read_text()
    graceful = int(re.search(r'"--graceful-timeout", "(\d+)"', dockerfile).group(1))  # type: ignore[union-attr]
    assert prestop >= 3, "the sleep must outlast endpoint propagation to the other nodes"
    assert pod["terminationGracePeriodSeconds"] >= prestop + graceful, (
        "Kubernetes counts the preStop hook against the grace period; too short and SIGKILL arrives mid-drain"
    )


def test_the_rollout_never_removes_a_ready_pod_before_a_new_one_is_ready(production):
    rolling = _web(production)["spec"]["strategy"]["rollingUpdate"]
    assert rolling["maxUnavailable"] == 0
    assert rolling["maxSurge"] == "25%"
```

The grace period test: it reads the `preStop` sleep seconds from the pod, finds gunicorn's `--graceful-timeout` in the Dockerfile with a regular expression, requires the sleep to be at least 3 seconds (it must outlast the time other nodes take to stop sending traffic) and the pod's `terminationGracePeriodSeconds` to be at least the sum (Kubernetes counts the `preStop` hook against the grace period; too short and `SIGKILL` arrives mid-drain). The rollout test requires `maxUnavailable: 0` and `maxSurge: 25%`.

```text
def test_the_autoscaler_owns_the_replica_count_and_is_scaled_on_cpu_with_a_request_to_scale_against(production):
    web = _web(production)
    assert "replicas" not in web["spec"]
    hpa = named(production, "HorizontalPodAutoscaler", "web")
    assert hpa["spec"]["scaleTargetRef"] == {"apiVersion": "apps/v1", "kind": "Deployment", "name": "web"}
    assert (hpa["spec"]["minReplicas"], hpa["spec"]["maxReplicas"]) == (6, 20)
    metric = hpa["spec"]["metrics"][0]["resource"]
    assert metric["name"] == "cpu"
    assert metric["target"]["averageUtilization"] == 60
    assert "cpu" in _pod(web)["containers"][0]["resources"]["requests"]


def test_the_disruption_budget_selects_the_web_pods_and_allows_one_unavailable(production):
    budget = named(production, "PodDisruptionBudget", "web")
    assert budget["spec"]["maxUnavailable"] == 1
    assert (
        budget["spec"]["selector"]["matchLabels"].items()
        <= _web(production)["spec"]["template"]["metadata"]["labels"].items()
    )
```

The autoscaler test: the Deployment has no `replicas` field (the autoscaler owns the count); the HorizontalPodAutoscaler targets the Deployment `web`, has 6 to 20 replicas and a CPU target of 60 percent, and the container has a CPU request to scale against. The budget test: `maxUnavailable: 1` and a selector whose labels are a subset (`<=` on `.items()`) of the web pod's labels.

```text
def test_pods_are_spread_over_nodes_ignoring_tainted_and_cordoned_ones(production):
    (constraint,) = _pod(_web(production))["topologySpreadConstraints"]
    assert constraint["topologyKey"] == "kubernetes.io/hostname"
    assert constraint["maxSkew"] == 1
    assert constraint["whenUnsatisfiable"] == "DoNotSchedule"
    assert constraint["nodeTaintsPolicy"] == "Honor", (
        "otherwise the control-plane node or a cordoned node counts as an empty domain"
    )


def test_the_load_generator_is_pinned_to_the_control_plane_node_that_is_never_drained(production):
    pod = _pod(named(production, "Deployment", "loadgen"))
    terms = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    assert terms == [{"matchExpressions": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists"}]}]
    assert any(t["key"] == "node-role.kubernetes.io/control-plane" for t in pod["tolerations"])


def test_the_web_pods_do_not_tolerate_the_control_plane_taint(production):
    assert "tolerations" not in _pod(_web(production))
```

Spread test: exactly one topology spread constraint over `kubernetes.io/hostname` with `maxSkew: 1`, `whenUnsatisfiable: DoNotSchedule` and `nodeTaintsPolicy: Honor` (without it the control-plane node or a cordoned node counts as an empty domain). The load generator is pinned to the control-plane node and tolerates its taint; the web pods have no tolerations.

```text
def test_the_naive_step_has_no_probes_no_prestop_and_the_default_rollout(overlays):
    web = _web(overlays["naive"])
    container = _pod(web)["containers"][0]
    assert not {"readinessProbe", "livenessProbe", "lifecycle"} & container.keys()
    assert "terminationGracePeriodSeconds" not in _pod(web)
    assert "strategy" not in web["spec"]
    assert web["spec"]["replicas"] == 6
    kinds = {doc["kind"] for doc in overlays["naive"]}
    assert not {"HorizontalPodAutoscaler", "PodDisruptionBudget"} & kinds


def _without(web: Doc, *paths: tuple[str, ...]) -> Doc:
    import copy

    clone = copy.deepcopy(web)
    for path in paths:
        node: Any = clone
        for key in path[:-1]:
            node = node[key]
        node.pop(path[-1], None)
    return clone
```

The naive step has no probes, no `lifecycle`, no grace period setting, no `strategy` and six replicas, and neither an autoscaler nor a budget. `_without(web, *paths)` makes a deep copy of a Deployment with the given nested keys removed.

```text
def test_each_step_adds_only_what_the_lesson_says(overlays):
    naive, graceful = _web(overlays["naive"]), _web(overlays["graceful"])
    stripped = _without(graceful, ("spec", "template", "spec", "terminationGracePeriodSeconds"))
    stripped["spec"]["template"]["spec"]["containers"][0] = {
        key: value
        for key, value in stripped["spec"]["template"]["spec"]["containers"][0].items()
        if key not in {"readinessProbe", "livenessProbe", "lifecycle"}
    }
    assert stripped == naive, "step 2 must change only the probes, the preStop hook and the grace period"
    tuned = _without(_web(overlays["rolling-tuned"]), ("spec", "strategy"))
    assert tuned == graceful
    final = _without(_web(overlays["production"]), ("spec", "template", "spec", "topologySpreadConstraints"))
    final["spec"]["replicas"] = 6
    assert final == _web(overlays["rolling-tuned"])
```

`test_each_step_adds_only_what_the_lesson_says` strips step 2's additions (grace period, probes, lifecycle) from the graceful Deployment and requires equality with the naive one; strips the strategy from step 3 and requires equality with step 2; strips the spread constraints and sets six replicas on the production Deployment and requires equality with step 3.


## 6. Try it yourself

```bash
.venv/bin/python -m pytest tests/policy -q
```

Passes with no cluster: 18 tests, each name a sentence describing a rule.

```bash
sed -i.bak 's/maxUnavailable: 0/maxUnavailable: 1/' manifests/overlays/rolling-tuned/kustomization.yaml && .venv/bin/python -m pytest tests/policy -q -k never_removes; mv manifests/overlays/rolling-tuned/kustomization.yaml.bak manifests/overlays/rolling-tuned/kustomization.yaml
```

The rollout test fails (`assert 1 == 0`), and the last command restores the file.

```bash
kubectl kustomize manifests/overlays/production | grep -A3 "^kind: HorizontalPodAutoscaler" ; kubectl kustomize manifests/overlays/production | grep "namespace:" | sort | uniq -c
```

Every namespaced object, the autoscaler and the budget included, shows `namespace: zero-downtime`.

## 7. Mistakes people make

- **Testing the source instead of the rendered output.** Kustomize can add, rename or hash things; the rendered YAML is what the cluster gets.
- **Using unsafe YAML loading.** `yaml.load` can build arbitrary objects from a file; use `safe_load`.
- **Comparing `"64Mi"` and `"128Mi"` as text.** `"128Mi" < "64Mi"` as strings; the test converts to numbers.
- **A rule with no failing example.** Each rule here has been checked to fail when its manifest line is removed.

## 8. Check yourself

1. Why does the grace-period test read the Dockerfile? *The gunicorn graceful timeout is written there; the test adds it to the `preStop` sleep and compares with the pod's grace period.*
2. What does `named(docs, "Deployment", "web")` return? *The one Deployment called `web`.*
3. Why does the namespace test exist? *A new object added by an overlay is outside the base's `namespace:` setting and would land in `default` without its own line.*
4. What does `test_each_step_adds_only_what_the_lesson_says` do? *Strips the added fields from each step and requires the result to equal the previous step.*
5. Why must web pods not tolerate the control-plane taint? *Otherwise they could be scheduled on the node that holds the load generator and is never drained.*

## 9. Further reading

- PyYAML: https://pyyaml.org/wiki/PyYAMLDocumentation
- Python `subprocess`: https://docs.python.org/3.13/library/subprocess.html
- Python `re`: https://docs.python.org/3.13/library/re.html
- Kubernetes resource quantities: https://kubernetes.io/docs/reference/kubernetes-api/common-definitions/quantity/
- `pathlib`: https://docs.python.org/3.13/library/pathlib.html
