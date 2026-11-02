# End-to-end tests (every Done-when line on the cluster)

## 1. What it is

An **end-to-end (e2e) test** exercises the whole system the way a user would. Here that means: on the real four-node cluster from `make up`, with the load generator sending 100 requests a second, run each of the lesson's operations (a rolling update, a traffic spike, a node drain, a scale-down) and let the generator's error count be the only verdict. The tests are marked `e2e` and run with `make smoke`. They take 15 to 25 minutes (the spike alone is five minutes) and are the proof behind the README's table.

## 2. Why this repository uses it

The lesson's claims are about measured behaviour of a live cluster: "a naive rollout fails requests", "three graceful rollouts in a row fail none", "the autoscaler absorbs a threefold spike with zero errors", "a drain loses nothing", "a scale-down loses nothing". Only a real cluster under real load can show these. Each Done-when line is exactly one test, and the tests print the evidence the README quotes (the counters, the replicas over time, the pods per node).

## 3. Where it appears

| File | Role |
| --- | --- |
| `tests/e2e/kube.py` | A small wrapper that runs `kubectl` and the project's scripts, with retries. |
| `tests/e2e/loadctl.py` | A client for the load generator's control API, with a reconnect when the tunnel dies. |
| `tests/e2e/test_done_when.py` | One test per Done-when line. |
| `Makefile` | `make smoke`; the CI job `cluster` (with a shorter spike). |

## 4. How it works underneath

Each test brings the cluster to the overlay it needs with `scripts/deploy.sh`, so any one test can be run alone. A **module-scoped fixture** (`load`) opens one port-forward to the generator for the whole run and puts the rate back to 100 at the end. A helper `apply` deploys an overlay and then insists on one clean ten-second window before the operation starts; another, `rollout`, restarts the web Deployment, waits for it and reads the counters. A background thread (`threading.Thread`) can sample the number of ready replicas every 0.4 seconds to prove capacity never dropped.

A busy laptop sometimes makes the Kubernetes API server slow to answer; `kubectl` then prints messages such as `TLS handshake timeout`. The wrapper recognises these **transient** errors and retries instead of failing the test, while a real error is returned at once.

Python features used: **context managers**, the **`contextlib.suppress`** helper (ignore one kind of exception), **threads and events**, **`statistics.median`**, **`collections.Counter`** (a dictionary that counts), and an **environment variable** (`SPIKE_SECONDS`) that shortens the spike.

## 5. Walkthrough of this repository's use

### `tests/e2e/kube.py`

```text
"""A small typed wrapper around kubectl for the cluster tests: run, read JSON, wait, port-forward."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
```

The docstring describes the module. The imports cover JSON, the environment, sockets, processes, time, URL fetching, callable types, `contextmanager`, paths and `Any`.

```text
ROOT = Path(__file__).resolve().parents[2]
Json = dict[str, Any]
ATTEMPTS = 120
RETRY_SECONDS = 8
SCRIPT_ATTEMPTS = 60
TRANSIENT = (
    "Unable to connect to the server",
    "TLS handshake timeout",
    "i/o timeout",
    "connection refused",
    "context deadline exceeded",
    "the server was unable to return a response",
    "etcdserver: request timed out",
    "net/http: request canceled",
)


def _transient(stderr: str) -> bool:
    """True when kubectl failed because the API server did not answer, as opposed to a real error."""
    return any(marker in stderr for marker in TRANSIENT)
```

`ROOT` is the repository folder. `Json` names a parsed JSON object. `ATTEMPTS = 120` and `RETRY_SECONDS = 8` mean a `kubectl` call that hits a transient error is retried up to 120 times, 8 seconds apart (about sixteen minutes at the limit); `SCRIPT_ATTEMPTS = 60` bounds script retries. `TRANSIENT` lists the error texts that mean "the API server did not answer": cannot connect, TLS handshake timeout, I/O timeout, connection refused, deadline exceeded, no response, an etcd (the cluster's key-value store) timeout, a cancelled HTTP request. `_transient(stderr)` is true when any of them appears in the error output.

```text
class Kube:
    """kubectl against the project's own kubeconfig (never ~/.kube/config), scoped to one namespace."""

    def __init__(self, namespace: str = "counter") -> None:
        self.namespace = namespace
        self.env = {**os.environ, "KUBECONFIG": os.environ.get("KUBECONFIG", str(ROOT / ".kube" / "config"))}

    def run(self, *args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        """Run kubectl in the namespace and return the finished process.
```

`class Kube` runs `kubectl` against the project's own kubeconfig (never `~/.kube/config`) in one namespace. `__init__` copies the environment and sets `KUBECONFIG`. `run(...)` begins with a docstring that continues in the next chunk; it takes the arguments, whether to raise on failure (`check`) and a per-call timeout in seconds.

```text
        A kubectl call that fails because the API server did not answer (a busy machine makes that happen) is
        retried for a few minutes; any other failure is returned or raised at once.
        """
        for attempt in range(1, ATTEMPTS + 1):
            try:
                result = subprocess.run(
                    ["kubectl", "-n", self.namespace, *args],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                if attempt == ATTEMPTS:
                    raise
                continue
            if result.returncode == 0 or not _transient(result.stderr) or attempt == ATTEMPTS:
                if check:
                    result.check_returncode()
                return result
            time.sleep(RETRY_SECONDS)
        raise AssertionError("unreachable")
```

The docstring finishes (transient failures are retried, other failures are raised at once). The loop calls `kubectl -n <namespace> <args>` with `subprocess.run`, capturing text. A `TimeoutExpired` is retried until the last attempt, then raised. If the command succeeded, or the error is not transient, or it was the last attempt, the result is returned (after `check_returncode()` raises if `check` is true); otherwise it sleeps `RETRY_SECONDS`. The final `raise AssertionError("unreachable")` is for the type checker: the loop always returns or raises first.

```text
    def script(self, name: str, *args: str, timeout: float = 600) -> str:
        """Run one of the project's scripts and return its output; raise with the output if it fails.

        A script that stops because the cluster did not answer is run again, for up to half an hour.
        """
        for attempt in range(1, SCRIPT_ATTEMPTS + 1):
            result = subprocess.run(
                [str(ROOT / "scripts" / name), *args],
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            unreachable = "no cluster reachable" in result.stderr or _transient(result.stderr)
            if result.returncode == 0:
                return result.stdout
            if not unreachable or attempt == SCRIPT_ATTEMPTS:
                raise AssertionError(f"scripts/{name} failed:\n{result.stdout}\n{result.stderr}")
            time.sleep(30)
        raise AssertionError("unreachable")
```

`script(name, *args)` runs one of the project's scripts. It retries when the output says the cluster is unreachable or transient, sleeping 30 seconds, for up to 60 attempts; a success returns standard output; any other failure raises an `AssertionError` that includes both outputs so a test report shows why.

```text
    def get(self, *args: str) -> Json:
        """kubectl get ... -o json, parsed."""
        parsed: Json = json.loads(self.run("get", *args, "-o", "json").stdout)
        return parsed

    def pods(self, selector: str = "app=web") -> list[Json]:
        """The pods matching a label selector."""
        items: list[Json] = self.get("pods", "-l", selector)["items"]
        return items

    def events(self, pod_name: str) -> list[str]:
        """The event messages recorded against one pod."""
        events = self.get("events", "--field-selector", f"involvedObject.name={pod_name}")["items"]
        return [f"{event['reason']}: {event['message']}" for event in events]

    def logs(self, pod_name: str, *, previous: bool = False) -> str:
        """The container log of a pod, optionally of the previous (crashed) instance."""
        args = ["logs", pod_name] + (["--previous"] if previous else [])
        result = self.run(*args, check=False)
        return result.stdout + result.stderr
```

Small helpers: `get` runs `kubectl get ... -o json` and parses it; `pods` returns the pod list for a label selector (default `app=web`); `events` returns `Reason: message` strings for the events about one pod, using a field selector (`involvedObject.name=<pod>`); `logs` returns a pod's log text (plus standard error), optionally of the `--previous` container instance that crashed.

```text
def wait_for[T](description: str, probe: Callable[[], T | None], *, timeout: float = 120, interval: float = 2) -> T:
    """Poll until probe returns something other than None or False; fail with the description on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value is not None and value is not False:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for: {description}")


def container_status(pod: Json) -> Json | None:
    """The first container's status block, if the kubelet has reported one."""
    statuses = pod.get("status", {}).get("containerStatuses")
    return statuses[0] if statuses else None


def is_ready(pod: Json) -> bool:
    """True when the pod's Ready condition is True."""
    conditions = pod.get("status", {}).get("conditions", [])
    return any(condition["type"] == "Ready" and condition["status"] == "True" for condition in conditions)
```

`wait_for[T](description, probe, timeout, interval)` calls `probe` every `interval` seconds until it returns something other than `None` or `False`, then returns that value; on timeout it raises an error that says what was awaited. `container_status(pod)` returns the first container's status block if the kubelet has reported one. `is_ready(pod)` is true when the pod's `Ready` condition has status `"True"`.

```text
def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
```

`_free_port()` is the same helper as in the integration tests: bind port 0, read the assigned port.

```text
@contextmanager
def port_forward(kube: Kube, target: str, port: int) -> Iterator[str]:
    """kubectl port-forward target to a free local port; yield the base URL."""
    local = _free_port()
    process = subprocess.Popen(
        ["kubectl", "-n", kube.namespace, "port-forward", target, f"{local}:{port}"],
        env=kube.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.5)
                if probe.connect_ex(("127.0.0.1", local)) == 0:
                    break
            time.sleep(0.3)
        yield f"http://127.0.0.1:{local}"
    finally:
        process.terminate()
        process.wait(timeout=10)
```

`port_forward(kube, target, port)` is a context manager. It starts `kubectl port-forward <target> <local>:<port>` in the background with output discarded, then for up to 20 seconds tries to connect to the local port (`connect_ex` returns 0 when it works) until the tunnel is up, yields the base URL, and in `finally` stops the process.

```text
def fetch_json(url: str, timeout: float = 5) -> Json:
    """GET a URL and parse the JSON body; raises OSError (URLError) on connection trouble."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        parsed: Json = json.loads(response.read())
        return parsed
```

`fetch_json(url, timeout)` does an HTTP GET with `urllib` and parses the JSON body; connection trouble raises `OSError` (its subclass `URLError`), which the sixth-failure test relies on.

### `tests/e2e/test_done_when.py`

```text
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
```

The docstring: the lesson's Done-when lines against the four-node cluster under constant load; the generator's error count is the only verdict; each test brings the cluster to the overlay it needs, so any one can run alone; `SPIKE_SECONDS` (default 300) shortens the five-minute spike. Imports: `contextlib`, `os`, `statistics`, `threading`, `time`, `Counter` and the helpers from `kube.py` and `loadctl.py`.

```text
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
```

`pytestmark = pytest.mark.e2e`; `BASELINE_RPS = 100`, `SPIKE_RPS = 300`; `SPIKE_SECONDS` read from the environment with `os.environ.get(..., "300")` and converted to an integer; `RESULTS` is a dictionary that collects the table rows. `record(operation, before=None, after=None)` fills one row of the table the README quotes; each cell can be set by a different test; the default row is `["N/A (not run naive)", "not run"]`.

```text
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
```

The module-scoped `kube` fixture creates a `Kube("zero-downtime")` and fails with a clear message when no cluster answers. The module-scoped `load` fixture opens the port-forward to the generator, yields a `LoadControl` and, afterwards, puts the rate back to 100.

```text
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
```

The autouse fixture `print_results` runs after all tests: it builds a Markdown table from `RESULTS`, prints it under `RESULTS` and writes it to `logs/results.md`. `deployment(kube)` reads the `web` Deployment as JSON and `ready(kube)` returns its ready-replica count. `apply(kube, overlay, load)` begins; its docstring is split across this and the next chunk.

```text
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
```

The docstring continues (up to six clean-window attempts because new pods can answer their first requests slowly on a busy shared machine, while the rollout, spike and drain windows are measured once, strictly). The body: deploy the overlay with `deploy.sh`, set the baseline rate, wait 5 seconds, then up to six times reset the counters, wait 10 seconds and read them, stopping at the first window with zero errors; assert that window had no errors and `skipped == 0`; reset again.

```text
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
```

`rollout(kube, load, watch_ready=False)` resets the counters, prepares a list holding the lowest ready count seen and a `threading.Event`, and defines `sample()`, a loop that records the minimum ready count every 0.4 seconds (`with contextlib.suppress(Exception)` ignores a failed sample: it is not a verdict). If `watch_ready` is set the sampling thread starts. Then `kubectl rollout restart deployment/web` and `rollout status` (up to 300 s), the sampler is stopped, 8 seconds pass so late failures are counted, and the function returns the counters and the lowest ready count.

```text
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
```

Test 1: apply the naive overlay, roll under load, print the counters and assert errors above zero and some connection errors; record the "before" figure. Test 2: apply the graceful overlay, do three rollouts in a row, and for each assert zero errors, more than 1000 requests (the rollout was under real load) and zero skipped; record "0 failed requests, three rollouts in a row".

```text
def test_done_when_3_a_tuned_rollout_never_drops_below_six_ready(kube: Kube, load: LoadControl) -> None:
    """maxUnavailable 0: at least six pods are ready at every sample, and still zero errors."""
    apply(kube, "rolling-tuned", load)
    counters, lowest = rollout(kube, load, watch_ready=True)
    print(f"tuned rollout: lowest ready count sampled {lowest}, {counters}")
    assert lowest >= 6, f"ready count dipped to {lowest}"
    assert counters["errors"] == 0, counters
    strategy = deployment(kube)["spec"]["strategy"]["rollingUpdate"]
    assert (strategy["maxSurge"], strategy["maxUnavailable"]) == ("25%", 0)
```

Test 3: apply the tuned overlay, roll with the ready-count sampler on, and assert the lowest ready count never fell below six, zero errors, and that the strategy is `maxSurge` 25% and `maxUnavailable` 0.

```text
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
```

`replicas_over_time(kube, load, seconds)` watches the autoscaler: every 10 seconds it reads and resets the counters, reads the HPA's desired replicas and the Deployment's ready replicas, appends a window of data, prints a line `t=... desired=... ready=... p99=... errors=...` and notes the first time desired rose above six (the decision) and the first time ready rose above six (the first extra pod ready). It returns the windows and those two moments.

```text
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
```

Test 4: apply the production overlay, wait up to 180 seconds for the autoscaler to report a CPU reading, wait 20 and 30 seconds, read a baseline window and the baseline utilization, assert no errors in it, raise the rate to 300 and watch for `SPIKE_SECONDS`. It computes the peak ready replicas, the median p99 of the last six windows (a ten-second window's p99 is the third-slowest of about 300 requests, so one stalled moment on a shared machine moves it; the median of the last minute is the stable reading) and the total errors, prints them and asserts: zero errors in total, the peak above six and a first extra pod ready.

```text
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
```

It also asserts the final p99 is at most twice the baseline, and records the table row (replicas 6 to the peak, when the autoscaler decided and when the first extra pod was ready, p99 before and after). `pods_per_node` counts the running web pods per node with a `Counter`.

```text
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
```

Test 5 (drain): assert the spread constraint exists (the production overlay is applied), wait for six ready pods, count pods per node and assert no node holds more than half, pick the busiest node, reset the counters and run `kubectl drain` (up to 300 s, `check=False` so a failure is inspected rather than raised). In the `try` block: wait 8 seconds, read the counters, print the drain output and assert the drain succeeded, zero errors, and that the output mentions the disruption budget (it refused at least one eviction at first); then count pods per node after the drain and assert the drained node holds none.

```text
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
```

The test also asserts no node holds more than half of the pods afterwards (plus one for rounding), records the table row with the pod counts before and after, and in `finally` always uncordons the node.

```text
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
```

Test 6 (scale-down): if there are six or fewer ready replicas the test first raises the rate to 300 and waits up to six minutes for the autoscaler to grow; it notes the peak, drops the rate to 100, resets, and waits up to ten minutes for the Deployment's replica count to return to six and then for exactly six web pods to exist (no terminating ones). After 8 more seconds it reads the counters, prints the time taken and asserts zero errors with more than 1000 requests; the table row records the time and the autoscaler's behaviour (60-second stabilization, two pods per 30 seconds).

### `tests/e2e/loadctl.py`

```text
"""A client for the in-cluster load generator's control API, reached through one port-forward."""

from __future__ import annotations

import http.client
import json
import time
import urllib.request
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from tests.e2e.kube import Json, Kube, port_forward

ATTEMPTS = 6


class LoadControl:
    """Read the error counters, reset the window, change the request rate.

    A `kubectl port-forward` can die when the machine is busy; a call that fails to connect opens a new one and
    tries again, so a dead tunnel is not mistaken for a verdict.
    """
```

A client for the load generator's control API, reached through one port-forward. Imports: `http.client` (for its exception type), `json`, `time`, `urllib.request`, `AbstractContextManager` and `contextmanager`, and the helpers from `kube.py`. `ATTEMPTS = 6` is how many times a call is tried. The class docstring explains the design: a `kubectl port-forward` can die when the machine is busy, so a call that fails to connect opens a new tunnel and tries again, and a dead tunnel is never mistaken for a verdict.

```text
    def __init__(self, kube: Kube) -> None:
        self._kube = kube
        self._forward: AbstractContextManager[str] | None = None
        self._base = ""

    def open(self) -> None:
        """Start a port-forward to the generator, replacing any earlier one."""
        self.close()
        self._forward = port_forward(self._kube, "svc/loadgen", 9100)
        self._base = self._forward.__enter__()

    def close(self) -> None:
        """Stop the port-forward."""
        if self._forward is not None:
            self._forward.__exit__(None, None, None)
            self._forward = None
```

`__init__` stores the `Kube` wrapper, a placeholder for the tunnel's context manager and an empty base URL. `open()` closes any earlier tunnel, creates a new `port_forward` to the `loadgen` Service on port 9100 and enters it (`__enter__()` returns the local base URL). `close()` leaves the tunnel's context, which stops the `kubectl` process.

```text
    def _call(self, path: str, method: str = "GET", body: Json | None = None) -> Json:
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                request = urllib.request.Request(self._base + path, method=method, data=data)
                with urllib.request.urlopen(request, timeout=10) as response:
                    parsed: Json = json.loads(response.read())
                    return parsed
            except (OSError, http.client.HTTPException):
                if attempt == ATTEMPTS:
                    raise
                time.sleep(2)
                self.open()
        raise AssertionError("unreachable")

    def stats(self) -> Json:
        """The current window, without changing it."""
        return self._call("/stats")

    def reset(self) -> Json:
        """The current window's final reading; a new window starts."""
        return self._call("/reset", "POST", {})
```

`_call(path, method, body)` encodes the body as JSON (or none) and tries up to six times: build a request, open it with a 10-second timeout and return the parsed JSON. On an `OSError` (which includes `URLError` and a refused connection) or an `http.client.HTTPException` (such as `RemoteDisconnected`), the last attempt re-raises; otherwise it sleeps two seconds and calls `open()` to replace the tunnel. The final `raise AssertionError("unreachable")` is for the type checker. `stats()` reads the current window without changing it, and `reset()` begins here.

```text
    def set_rate(self, requests_per_second: float) -> Json:
        """Change the request rate."""
        return self._call("/rate", "POST", {"rps": requests_per_second})


@contextmanager
def load_control(kube: Kube) -> Iterator[LoadControl]:
    """Port-forward to the load generator and yield a client for it."""
    control = LoadControl(kube)
    control.open()
    try:
        yield control
    finally:
        control.close()
```

`reset()` posts to `/reset` and gets the window's final reading while a new window starts. `set_rate(rps)` posts `{"rps": ...}`. `load_control(kube)` is a context manager: it creates a `LoadControl`, opens the tunnel, yields the client and, in `finally`, closes the tunnel.


## 6. Try it yourself

```bash
make up && make smoke
```

Runs six tests with `-v`; each prints `PASSED`, and at the end a `RESULTS` table (also written to `logs/results.md`) with the figures the README quotes. Finish with `make down`.

```bash
SPIKE_SECONDS=60 .venv/bin/python -m pytest tests/e2e -m e2e -s -v -k done_when_4
```

Runs only the autoscaler test with a one-minute spike.

```bash
make deploy OVERLAY=naive && make run
```

The first test by hand: each `rollout N of 3` shows errors.

## 7. Mistakes people make

- **Judging by `kubectl rollout status`.** It says the pods are updated, not that no request failed.
- **Starting the measurement before the service is steady.** `apply` waits for a clean window first.
- **Reading the counters too early.** `rollout` waits eight seconds after the rollout so late failures are counted.
- **Ignoring `skipped`.** If the generator could not keep up the verdict is void; the tests assert it is zero.
- **A spike test that only checks errors.** It also requires replicas to grow and the median p99 of the last minute to return to within twice the baseline.

## 8. Check yourself

1. Why is the error count the only verdict? *Pod states can look fine while requests fail; the generator counts what a client experienced.*
2. What does the drain test assert besides zero errors? *That the budget held at least one eviction back, that the drained node ends with no web pods and that no node held more than half before.*
3. Why does the scale-down test first check the replica count? *It needs more than six replicas to scale down from; if needed it raises the load first.*
4. What does `SPIKE_SECONDS` do? *Shortens the five-minute spike for quick or hosted runs.*
5. Why does `apply` try up to six windows? *New pods can answer their first requests slowly on a busy shared machine; the operation itself is measured once, strictly.*

## 9. Further reading

- Kubernetes pod lifecycle and container states: https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
- Restart back-off: https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#restart-policy
- EndpointSlices: https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/
- Python `contextlib`: https://docs.python.org/3.13/library/contextlib.html
- Python `itertools.pairwise`: https://docs.python.org/3.13/library/itertools.html#itertools.pairwise
