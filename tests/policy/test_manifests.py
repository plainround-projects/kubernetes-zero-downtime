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


def _web(docs: list[Doc]) -> Doc:
    return named(docs, "Deployment", "web")


def _quantity(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(m|Mi|Gi|Ki)?", str(value))
    assert match, f"unparsable quantity {value!r}"
    return float(match.group(1)) * {None: 1, "m": 0.001, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}[match.group(2)]


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


def test_the_finished_web_service_has_both_probes_on_their_own_paths(production):
    container = _pod(_web(production))["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"


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
    assert pod["nodeSelector"] == {"node-role.kubernetes.io/control-plane": ""}
    assert any(t["key"] == "node-role.kubernetes.io/control-plane" for t in pod["tolerations"])


def test_the_web_pods_do_not_tolerate_the_control_plane_taint(production):
    assert "tolerations" not in _pod(_web(production))


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
