"""The lesson's Done when lines, against the four-node cluster (make up first), under constant load.

The load generator inside the cluster counts every failed request; its error count is the only verdict.
Each test brings the cluster to the overlay it needs, so any one can be run alone, and the tests print the
evidence the README quotes. SPIKE_SECONDS (default 300) shortens the five-minute spike for a quick run.
"""

from __future__ import annotations

import contextlib
import os
import statistics
import threading
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.kube import Json, Kube, wait_for
from tests.e2e.loadctl import LoadControl, load_control

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
BASELINE_RPS = 100
SPIKE_RPS = 300
SPIKE_SECONDS = int(os.environ.get("SPIKE_SECONDS", "300"))
RESULTS: dict[str, list[str]] = {}


def record(operation: str, *, before: str | None = None, after: str | None = None) -> None:
    """Fill in one row of the table the README quotes; either cell may be set by a different test."""
    row = RESULTS.setdefault(operation, ["N/A (not run naive)", "not run"])
    if before is not None:
        row[0] = before
    if after is not None:
        row[1] = after


@pytest.fixture(scope="module")
def kube() -> Kube:
    cluster = Kube("zero-downtime")
    if cluster.run("get", "nodes", check=False).returncode != 0:
        pytest.fail("no cluster reachable: run `make up` first")
    return cluster


@pytest.fixture(scope="module")
def load(kube: Kube) -> Iterator[LoadControl]:
    with load_control(kube) as control:
        yield control
        control.set_rate(BASELINE_RPS)


@pytest.fixture(scope="module", autouse=True)
def print_results() -> Iterator[None]:
    yield
    table = "\n".join(
        ["| operation | before (naive) | after (fixed) |", "| --- | --- | --- |"]
        + [f"| {name} | {before} | {after} |" for name, (before, after) in RESULTS.items()]
    )
    print(f"\n\nRESULTS\n{table}\n")
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "results.md").write_text(table + "\n")


def deployment(kube: Kube) -> Json:
    return kube.get("deployment", "web")


def ready(kube: Kube) -> int:
    return int(deployment(kube)["status"].get("readyReplicas", 0))


def apply(kube: Kube, overlay: str, load: LoadControl) -> None:
    """Deploy an overlay, wait for six ready pods and one clean 10-second window at 100 requests a second.

    Right after a deploy, new pods can answer their first requests slowly (the machine is shared and busy), so up
    to six windows are tried; the rollout, spike and drain windows that follow are measured once, strictly.
    """
    kube.script("deploy.sh", overlay)
    load.set_rate(BASELINE_RPS)
    time.sleep(5)
    steady: Json = {}
    for _ in range(6):
        load.reset()
        time.sleep(10)
        steady = load.stats()
        if steady["errors"] == 0:
            break
    assert steady["errors"] == 0, f"{overlay} must serve cleanly before it is disturbed: {steady}"
    assert steady["skipped"] == 0, "the load generator itself must keep up"
    load.reset()


def rollout(kube: Kube, load: LoadControl, *, watch_ready: bool = False) -> tuple[Json, int]:
    """Restart the web pods under load; return the counters of the window and the lowest ready count seen."""
    load.reset()
    lowest = [10_000]
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            with contextlib.suppress(Exception):  # a failed sample is skipped, it is not a verdict
                lowest[0] = min(lowest[0], ready(kube))
            stop.wait(0.4)

    watcher = threading.Thread(target=sample, daemon=True)
    if watch_ready:
        watcher.start()
    kube.run("rollout", "restart", "deployment/web")
    kube.run("rollout", "status", "deployment/web", "--timeout=300s", timeout=320)
    stop.set()
    time.sleep(8)
    return load.reset(), lowest[0]


def test_done_when_1_the_naive_rollout_fails_requests(kube: Kube, load: LoadControl) -> None:
    """The number of failed requests during a naive rollout is written down and is above zero."""
    apply(kube, "naive", load)
    counters, _ = rollout(kube, load)
    print(f"naive rollout under load: {counters}")
    assert counters["errors"] > 0, counters
    assert counters["connection"] > 0, "expect connection errors to dominate"
    record(
        "rolling update", before=f"{counters['errors']} failed requests ({counters['connection']} connection errors)"
    )


def test_done_when_2_a_graceful_rollout_has_zero_errors_three_times(kube: Kube, load: LoadControl) -> None:
    """Readiness, graceful shutdown and a preStop delay: three rollouts in a row, zero failed requests."""
    apply(kube, "graceful", load)
    runs = [rollout(kube, load)[0] for _ in range(3)]
    for number, counters in enumerate(runs, start=1):
        print(f"graceful rollout {number}: {counters}")
        assert counters["errors"] == 0, counters
        assert counters["total"] > 1000, "the rollout must have been under real load"
        assert counters["skipped"] == 0
    record("rolling update", after="0 failed requests, three rollouts in a row")


def test_done_when_3_a_tuned_rollout_never_drops_below_six_ready(kube: Kube, load: LoadControl) -> None:
    """maxUnavailable 0: at least six pods are ready at every sample, and still zero errors."""
    apply(kube, "rolling-tuned", load)
    counters, lowest = rollout(kube, load, watch_ready=True)
    print(f"tuned rollout: lowest ready count sampled {lowest}, {counters}")
    assert lowest >= 6, f"ready count dipped to {lowest}"
    assert counters["errors"] == 0, counters
    strategy = deployment(kube)["spec"]["strategy"]["rollingUpdate"]
    assert (strategy["maxSurge"], strategy["maxUnavailable"]) == ("25%", 0)


def replicas_over_time(kube: Kube, load: LoadControl, seconds: int) -> tuple[list[Json], dict[str, float | None]]:
    """Watch the HPA for a while, one 10-second window of counters at a time."""
    started = time.monotonic()
    windows: list[Json] = []
    first_more: float | None = None
    first_ready_more: float | None = None
    while time.monotonic() - started < seconds:
        time.sleep(10)
        elapsed = time.monotonic() - started
        counters = load.reset()
        hpa = kube.get("hpa", "web")["status"]
        status = deployment(kube)["status"]
        desired, ready_count = int(hpa.get("desiredReplicas", 6)), int(status.get("readyReplicas", 0))
        windows.append({"t": round(elapsed), "desired": desired, "ready": ready_count, **counters})
        print(
            f"  t={elapsed:4.0f}s desired={desired:2d} ready={ready_count:2d} "
            f"p99={counters['p99_ms']:6.1f}ms errors={counters['errors']}"
        )
        if first_more is None and desired > 6:
            first_more = elapsed
        if first_ready_more is None and ready_count > 6:
            first_ready_more = elapsed
    return windows, {"decision": first_more, "first_extra_ready": first_ready_more}


def test_done_when_4_the_autoscaler_absorbs_a_spike_with_zero_errors(kube: Kube, load: LoadControl) -> None:
    """Replicas grow, p99 returns near baseline, errors stay zero, and the time to scale is recorded."""
    apply(kube, "production", load)
    wait_for(
        "the HPA to report a CPU reading", lambda: kube.get("hpa", "web")["status"].get("currentMetrics"), timeout=180
    )
    time.sleep(20)
    load.reset()
    time.sleep(30)
    baseline = load.reset()
    print(f"baseline at {BASELINE_RPS} rps: {baseline}")
    hpa_before = kube.get("hpa", "web")["status"]["currentMetrics"][0]["resource"]["current"]["averageUtilization"]
    print(f"baseline CPU utilization: {hpa_before}% of the request (target 60%)")
    assert baseline["errors"] == 0
    load.set_rate(SPIKE_RPS)
    windows, moments = replicas_over_time(kube, load, SPIKE_SECONDS)
    peak = max(window["ready"] for window in windows)
    # A ten-second window's p99 is the third-slowest of about 300 requests, so one stalled moment on a shared
    # machine moves it; the median of the last minute is the stable reading.
    last_p99 = statistics.median(window["p99_ms"] for window in windows[-6:])
    total_errors = sum(window["errors"] for window in windows)
    print(
        f"peak ready replicas {peak}; HPA decided after {moments['decision']}s; "
        f"first extra pod ready after {moments['first_extra_ready']}s"
    )
    worst = max(window["p99_ms"] for window in windows)
    print(f"p99: baseline {baseline['p99_ms']}ms, worst window {worst}ms, last six windows median {last_p99}ms")
    assert total_errors == 0, windows
    assert peak > 6, "replicas must have grown"
    assert moments["first_extra_ready"] is not None
    assert last_p99 <= baseline["p99_ms"] * 2, (
        f"p99 {last_p99}ms did not return near the baseline {baseline['p99_ms']}ms"
    )
    record(
        "traffic spike (3x)",
        after=(
            f"0 errors; replicas 6 to {peak}; HPA decided after {moments['decision']:.0f}s, "
            f"first extra pod ready after {moments['first_extra_ready']:.0f}s; "
            f"p99 {baseline['p99_ms']:.0f}ms baseline, {last_p99:.0f}ms once scaled"
        ),
    )


def pods_per_node(kube: Kube) -> Counter[str]:
    pods = kube.get("pods", "-l", "app=web")["items"]
    return Counter(pod["spec"]["nodeName"] for pod in pods if pod["status"]["phase"] == "Running")


def test_done_when_5_a_drain_has_zero_errors_and_no_node_holds_over_half(kube: Kube, load: LoadControl) -> None:
    """The busiest worker is drained while the budget holds evictions back; no node held more than half the pods."""
    spec = deployment(kube)["spec"]["template"]["spec"]
    assert spec["topologySpreadConstraints"], "the production overlay must be applied"
    load.set_rate(BASELINE_RPS)
    wait_for("six or more ready pods", lambda: ready(kube) >= 6 or None, timeout=120)
    before = pods_per_node(kube)
    total = sum(before.values())
    print(f"web pods per node before the drain: {dict(before)}")
    assert max(before.values()) * 2 <= total, f"a node holds more than half of {total} pods: {dict(before)}"
    busiest = before.most_common(1)[0][0]
    load.reset()
    drain = kube.run(
        "drain", busiest, "--ignore-daemonsets", "--delete-emptydir-data", "--timeout=300s", timeout=330, check=False
    )
    try:
        time.sleep(8)
        counters = load.reset()
        print(drain.stdout + drain.stderr)
        print(f"errors during the drain: {counters}")
        assert drain.returncode == 0, drain.stderr
        assert counters["errors"] == 0, counters
        assert "disruption budget" in drain.stdout + drain.stderr, (
            "the budget should have refused at least one eviction at first"
        )
        after = pods_per_node(kube)
        print(f"web pods per node after the drain: {dict(after)}")
        assert busiest not in after
        assert max(after.values()) * 2 <= sum(after.values()) + 1
        record(
            "node drain",
            after=(
                f"0 errors; evictions paused by the budget; pods per node "
                f"{sorted(before.values(), reverse=True)} before, {sorted(after.values(), reverse=True)} after"
            ),
        )
    finally:
        kube.run("uncordon", busiest, check=False)


def test_done_when_6_a_scale_down_has_zero_errors(kube: Kube, load: LoadControl) -> None:
    """After the load drops, the autoscaler ends pods through the same graceful shutdown: zero failed requests."""
    if int(deployment(kube)["status"].get("readyReplicas", 0)) <= 6:
        load.set_rate(SPIKE_RPS)
        wait_for("replicas to grow above six", lambda: ready(kube) > 6 or None, timeout=360, interval=5)
    peak = ready(kube)
    load.set_rate(BASELINE_RPS)
    load.reset()
    started = time.monotonic()
    wait_for(
        "the autoscaler to scale back to six",
        lambda: int(deployment(kube)["spec"]["replicas"]) == 6 or None,
        timeout=600,
        interval=5,
    )
    wait_for("six ready pods and no terminating ones", lambda: len(kube.pods("app=web")) == 6 or None, timeout=120)
    took = time.monotonic() - started
    time.sleep(8)
    counters = load.reset()
    print(f"scale-down from {peak} to 6 took {took:.0f}s; counters: {counters}")
    assert counters["errors"] == 0, counters
    assert counters["total"] > 1000
    record("scale-down", after=f"0 errors; {peak} to 6 pods in {took:.0f}s (stabilization 60s, 2 pods per 30s)")
