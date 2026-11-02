# The load generator (open-loop load, percentiles and the control API)

## 1. What it is

A **load generator** is a program that sends a steady stream of requests to a service and records what happened to each. This repository ships its own (package `loadgen`, run as its own pod in the cluster) because the whole lesson is a measurement: *how many requests fail while the cluster rolls, scales or drains the pods?* The answer must come from a counter that cannot be fooled, so it is worth understanding how it works.

Words used here:

- **Request rate**: requests per second (rps). The baseline is 100 rps, the spike is 300 rps.
- **Open-loop** load sends at a fixed schedule regardless of how long earlier requests take. **Closed-loop** load sends the next request only after the previous one finishes, which slows down exactly when the service struggles and hides the problem.
- **Latency** is how long a request takes. A **percentile** answers "what latency did this share of requests stay under?": p50 (the median) is the middle value; p99 is the value 99 percent of requests were faster than, which shows the slow tail.
- An **error** is any request that did not get a good answer: a failed connection, a timeout, a 5xx status (the server failed) or any other non-2xx status.
- A **thread** is an independent path of execution in one process; a **thread pool** is a fixed set of them that take jobs from a queue; a **lock** makes sure only one thread at a time changes shared data.

## 2. Why this repository uses it

A rolling update that drops one request in a thousand looks fine to a person clicking around, and is a failure at 100 rps. The generator counts every request by class (`connection`, `timeout`, `http_5xx`, `http_other`) and the lesson's verdict is its error count. It opens a **new connection for every request** on purpose: each request then meets whatever the Service routes it to, which is what exposes a pod that is not ready yet or is going away. It also counts `skipped` (a request that was due but could not be sent because all senders were busy) so that an overloaded generator can never be mistaken for a good result: a verdict is valid only when `skipped` is zero. In the same spirit it counts `late_starts` (a request that began more than 0.1 s after it was due because the generator's own threads were held up), and a window with late starts is void.

A second measurement trap is worth knowing. When a connection fails, the Linux connection-tracking table keeps a half-open entry for 120 seconds that remembers which pod the Service chose. If a later connection happens to use the same *source port*, it is matched to that entry and sent to the same dead pod, so it fails too, although the Service no longer lists that pod. In this project that made a clean graceful rollout appear to fail about 0.1 to 0.4% of requests for two minutes after the naive step had produced its failures. The generator therefore never reuses a source port that failed within the last 125 seconds.

## 3. Where it appears

| File | Role |
| --- | --- |
| `src/loadgen/domain/pacer.py` | The fixed-rate schedule. |
| `src/loadgen/domain/stats.py` | Counters, percentiles and the snapshot. |
| `src/loadgen/application/runner.py` | Sends what is due, with at most N requests in flight. |
| `src/loadgen/infrastructure/http_requester.py` | One HTTP request on a new connection, classified; holds back the source ports of failed requests. |
| `src/loadgen/interfaces/control.py` | A tiny HTTP API to read and reset the counters and change the rate. |
| `src/loadgen/interfaces/cli.py` | The program: start, serve the control API, print the final counts on SIGTERM. |
| `manifests/base/loadgen.yaml` | The pod that runs it (see `kubernetes.md`). |
| `scripts/loadctl.sh`, `tests/e2e/loadctl.py` | Talk to the control API (see `bash.md`, `tests-e2e.md`). |

## 4. How it works underneath

The **pacer** keeps one number, `next_due`, the time the next request should be sent. Every `1/rate` seconds it adds an interval; when asked "how many are due now?" it counts how many intervals have passed and moves past them. A stall (the machine froze for 200 ms) therefore causes a burst of catch-up sends, never a gap in the schedule. Changing the rate moves `next_due` to the earlier of the old schedule and one new interval from now, so raising the rate takes effect at once and lowering it never delays a request already due.

The **runner** has one thread (the pacer thread) that wakes at least every 50 ms, asks the pacer what is due and hands each request to a thread pool. At most `max_in_flight` requests run at once; a request due while all senders are busy is not queued (queueing would show up as extra latency and hide an overloaded generator), it is counted as `skipped`.

**Percentiles** use the nearest-rank method: sort the latencies, take the value at rank `ceil(fraction x count)`. For 1000 requests, p99 is the 990th value.

The **control API** is plain JSON over HTTP on port 9100: `GET /healthz`, `GET /stats` (read the current window), `POST /reset` (return the window's final reading and start a new one) and `POST /rate` with body `{"rps": 300}`. The tests call `reset` before and after each operation, so each operation gets its own clean measurement window. On `SIGTERM` (the signal Kubernetes sends when stopping a pod) the program stops sending, waits for requests in flight and prints one JSON line `{"final": {...}}`.

`http.client.HTTPConnection` is Python's low-level HTTP client; sending the header `Connection: close` tells the server to close the connection after the answer, so the next request cannot reuse it.

## 5. Walkthrough of this repository's use

### `src/loadgen/domain/pacer.py`

```text
"""A fixed-rate schedule: how many requests are due now, regardless of how long earlier ones took."""

from __future__ import annotations

from loadgen.domain.errors import InvalidRateError


class Pacer:
    """Open-loop pacing. Sends are due every 1/rate seconds from a start time, so a slow response never
    delays the next send, and a stall in the sender is caught up by sending the missed ones together.
    """

    def __init__(self, rate_per_second: float, start: float) -> None:
        """Start the schedule at `start` (a monotonic clock reading)."""
        self._check(rate_per_second)
        self._interval = 1.0 / rate_per_second
        self._next_due = start

    @staticmethod
    def _check(rate_per_second: float) -> None:
        if not 0 < rate_per_second <= 10_000:
            raise InvalidRateError(f"rate must be between 0 and 10000 requests per second, got {rate_per_second}")
```

The docstring describes **open-loop pacing**: sends are due every 1/rate seconds from a start time, so a slow response never delays the next send, and a stall in the sender is caught up by sending the missed ones together. `Pacer.__init__` validates the rate, stores `_interval = 1.0 / rate_per_second` (seconds between sends) and `_next_due = start` (a reading of the monotonic clock). `_check` is a static method (it needs no instance): the rate must be above 0 and at most 10,000, otherwise `InvalidRateError`.

```text
    def lateness(self, now: float) -> float:
        """How many seconds the oldest unsent request is overdue at `now` (0.0 when none is due)."""
        return max(0.0, now - self._next_due)

    def due(self, now: float) -> int:
        """How many sends are due at `now`; advances the schedule past them."""
        count = 0
        while self._next_due <= now:
            self._next_due += self._interval
            count += 1
        return count

    def seconds_until_next(self, now: float) -> float:
        """How long to wait before the next send is due (never negative)."""
        return max(0.0, self._next_due - now)

    def set_rate(self, rate_per_second: float, now: float) -> None:
        """Change the rate from `now` on, without a burst or a gap.
```

`lateness(now)` returns how many seconds the oldest unsent request is overdue: `max(0.0, now - self._next_due)`, which is 0.0 when nothing is due. The runner uses it to notice that its own thread was held up. `due(now)` loops `while self._next_due <= now`, adding one interval to `_next_due` and counting, and returns the count: the number of sends owed. `seconds_until_next(now)` returns the time left until the next send, never negative. `set_rate(rate, now)` begins; its docstring says it changes the rate from `now` on, without a burst or a gap.

```text
        The next send is due at the earlier of the old schedule and one new interval from now, so raising
        the rate takes effect at once and lowering it does not delay a send that was already due.
        """
        self._check(rate_per_second)
        self._interval = 1.0 / rate_per_second
        self._next_due = max(now, min(self._next_due, now + self._interval))
```

The docstring finishes: the next send is due at the earlier of the old schedule and one new interval from now, so raising the rate takes effect at once and lowering it does not delay a send that was already due. The body validates the new rate, sets the new interval and moves `_next_due` to `max(now, min(old _next_due, now + new interval))`: never in the past.

### `src/loadgen/domain/stats.py`

```text
"""Counters for a measurement window: every error by class, and latency percentiles."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from loadgen.domain.outcome import Outcome, Sample


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending list; 0.0 for an empty list."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An immutable reading of a window."""
```

Imports: `math`, `threading` and `dataclass`. `percentile(sorted_values, fraction)` implements nearest-rank: 0.0 for an empty list; otherwise the rank is `max(1, ceil(fraction * len))` and the value at index `rank - 1` is returned. `Snapshot` begins: an immutable reading of a window.

```text
    total: int
    ok: int
    connection: int
    timeout: int
    http_5xx: int
    http_other: int
    skipped: int
    late_starts: int
    p50_ms: float
    p99_ms: float
    max_ms: float

    @property
    def errors(self) -> int:
        """Every failed request: connection errors, timeouts, 5xx and any other non-2xx status."""
        return self.connection + self.timeout + self.http_5xx + self.http_other
```

The `Snapshot` fields are counts (`total`, `ok`, `connection`, `timeout`, `http_5xx`, `http_other`, `skipped`, `late_starts`) and latencies in milliseconds (`p50_ms`, `p99_ms`, `max_ms`). `late_starts` counts requests that began more than 0.1 s after they were due: the generator's own threads were held up, so those requests' times include the generator's delay. The property `errors` adds the four error classes: connection errors, timeouts, 5xx statuses and other non-2xx statuses.

```text
    def as_dict(self) -> dict[str, float | int]:
        """The snapshot as plain JSON-ready values, with the error total included."""
        return {
            "total": self.total,
            "ok": self.ok,
            "errors": self.errors,
            "connection": self.connection,
            "timeout": self.timeout,
            "http_5xx": self.http_5xx,
            "http_other": self.http_other,
            "skipped": self.skipped,
            "late_starts": self.late_starts,
            "p50_ms": round(self.p50_ms, 1),
            "p99_ms": round(self.p99_ms, 1),
            "max_ms": round(self.max_ms, 1),
        }


class StatsWindow:
    """Thread-safe accumulator. `skipped` counts requests the generator itself could not send in time."""
```

`as_dict` returns the snapshot as plain JSON-ready values with the `errors` total included, `late_starts` and the latencies rounded to one decimal. `StatsWindow` begins: a thread-safe accumulator in which `skipped` counts requests the generator itself could not send in time.

```text
    def __init__(self) -> None:
        """Start an empty window."""
        self._lock = threading.Lock()
        self._counts = dict.fromkeys(Outcome, 0)
        self._latencies: list[float] = []
        self._skipped = 0
        self._late_starts = 0

    def record(self, sample: Sample) -> None:
        """Count one finished request."""
        with self._lock:
            self._counts[sample.outcome] += 1
            self._latencies.append(sample.latency_seconds)

    def skip(self) -> None:
        """Count one request that was due but not sent because every sender was busy."""
        with self._lock:
            self._skipped += 1
```

The constructor creates a `threading.Lock`, a dictionary `dict.fromkeys(Outcome, 0)` (a zero for every outcome class), an empty latency list, a skipped counter and a late-start counter. `record(sample)` adds one finished request under the lock; `skip()` counts one unsent request.

```text
    def late(self, count: int = 1) -> None:
        """Count requests that started later than the generator's tolerance after they were due."""
        with self._lock:
            self._late_starts += count

    def snapshot(self) -> Snapshot:
        """Read the window without changing it."""
        with self._lock:
            return self._build()

    def reset(self) -> Snapshot:
        """Return the window's final reading and start a new, empty one."""
        with self._lock:
            final = self._build()
            self._counts = dict.fromkeys(Outcome, 0)
            self._latencies = []
            self._skipped = 0
            self._late_starts = 0
            return final
```

`late(count=1)` adds to the late-start counter under the lock. `snapshot()` reads the window without changing it. `reset()` takes the lock, builds the final reading, then empties the counters, the list, `skipped` and `late_starts`, and returns the final reading: reading and clearing happen atomically so no request is counted twice or lost.

```text
    def _build(self) -> Snapshot:
        ordered = sorted(self._latencies)
        return Snapshot(
            total=sum(self._counts.values()),
            ok=self._counts[Outcome.OK],
            connection=self._counts[Outcome.CONNECTION],
            timeout=self._counts[Outcome.TIMEOUT],
            http_5xx=self._counts[Outcome.HTTP_5XX],
            http_other=self._counts[Outcome.HTTP_OTHER],
            skipped=self._skipped,
            late_starts=self._late_starts,
            p50_ms=percentile(ordered, 0.50) * 1000,
            p99_ms=percentile(ordered, 0.99) * 1000,
            max_ms=(ordered[-1] * 1000) if ordered else 0.0,
        )
```

`_build()` sorts the latencies once and returns a `Snapshot`: totals from the counters, `skipped`, `late_starts`, and p50, p99 and the maximum converted from seconds to milliseconds (the maximum is 0.0 for an empty window).

### `src/loadgen/application/runner.py`

```text
"""The load runner: sends requests at a fixed rate and records every outcome."""

from __future__ import annotations

import threading

from loadgen.application.ports import Clock, Executor, Requester
from loadgen.domain.pacer import Pacer
from loadgen.domain.stats import Snapshot, StatsWindow

LATE_SECONDS = 0.1


class LoadRunner:
    """Drives a Pacer on one thread and hands each due request to an Executor.

    At most `max_in_flight` requests run at once. A request that is due when all senders are busy is not
    queued (queuing would hide an overloaded generator as extra latency); it is counted as `skipped`, and a
    verdict is only valid when `skipped` is zero.
```

`LATE_SECONDS = 0.1` is the tolerance for a late start. `LoadRunner` drives a `Pacer` on one thread and hands each due request to an `Executor`. The docstring states the first key rule: at most `max_in_flight` requests run at once; a request due when all senders are busy is not queued (queueing would hide an overloaded generator as extra latency), it is counted as `skipped`, and a verdict is only valid when `skipped` is zero.

```text
    A request that starts more than LATE_SECONDS after it was due (the pacer thread or a sender thread was held
    up, for example by a busy machine) is counted as `late_starts`: its measured time includes the generator's own
    delay, so a window with late starts is not a valid verdict either.
    """

    def __init__(
        self, requester: Requester, clock: Clock, executor: Executor, rate_per_second: float, max_in_flight: int
    ) -> None:
        """Bind the collaborators; nothing is sent until start()."""
        self._requester = requester
        self._clock = clock
        self._executor = executor
        self._max_in_flight = max_in_flight
        self._window = StatsWindow()
        self._pacer = Pacer(rate_per_second, clock.now())
        self._rate = rate_per_second
        self._lock = threading.Lock()
        self._in_flight = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
```

The second rule: a request that starts more than `LATE_SECONDS` after it was due (the pacer thread or a sender thread was held up, for example by a busy machine) is counted as `late_starts`, because its measured time includes the generator's own delay, so a window with late starts is not a valid verdict either. The constructor stores the requester, clock, executor, rate and limit, creates the `StatsWindow` and the `Pacer`, a lock, an in-flight counter, a `threading.Event` used as a stop flag and a placeholder for the thread.

```text
    @property
    def rate(self) -> float:
        """The current target rate in requests per second."""
        with self._lock:
            return self._rate

    def start(self) -> None:
        """Begin sending on a background thread."""
        self._thread = threading.Thread(target=self._loop, name="pacer", daemon=True)
        self._thread.start()

    def stop(self) -> Snapshot:
        """Stop sending, wait for requests in flight, and return the window's final reading."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._executor.shutdown()
        return self._window.reset()
```

The property `rate` returns the current target rate under the lock. `start()` creates a daemon thread (one that does not keep the program alive) named `pacer` running `_loop`. `stop()` sets the stop flag, waits for the thread (`join`), shuts the executor down (waiting for requests in flight) and returns the window's final reading.

```text
    def set_rate(self, rate_per_second: float) -> None:
        """Change the rate without a burst or a gap."""
        with self._lock:
            self._pacer.set_rate(rate_per_second, self._clock.now())
            self._rate = rate_per_second

    def snapshot(self) -> Snapshot:
        """Read the current window."""
        return self._window.snapshot()

    def reset(self) -> Snapshot:
        """Return the current window's reading and start a new one."""
        return self._window.reset()
```

`set_rate` changes the pacer's rate and the stored rate under the lock. `snapshot()` reads the window. `reset()` returns the window's reading and starts a new window.

```text
    def tick(self) -> float:
        """Send everything that is due now and return how long to wait for the next one."""
        with self._lock:
            now = self._clock.now()
            overdue = self._pacer.lateness(now)
            due = self._pacer.due(now)
            wait = self._pacer.seconds_until_next(now)
        if due and overdue > LATE_SECONDS:
            self._window.late(due)
        for _ in range(due):
            self._send_one()
        return wait

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._clock.sleep(min(self.tick(), 0.05))
```

`tick()` is the heart: under the lock it reads the time, asks the pacer how overdue the oldest unsent request is (`overdue`), how many sends are due and how long until the next. Then, outside the lock: if sends are due and the pacer was overdue by more than `LATE_SECONDS`, all of them are counted as late starts (the pacer thread itself was stalled); then it calls `_send_one` that many times and returns the wait. `_loop()` repeats until stopped: sleep for the smaller of that wait and 0.05 s (so the thread wakes at least 20 times a second).

```text
    def _send_one(self) -> None:
        with self._lock:
            if self._in_flight >= self._max_in_flight:
                self._window.skip()
                return
            self._in_flight += 1
        due_at = self._clock.now()
        self._executor.submit(lambda: self._run_request(due_at))

    def _run_request(self, due_at: float) -> None:
        if self._clock.now() - due_at > LATE_SECONDS:
            self._window.late()
        try:
            self._window.record(self._requester.request())
        finally:
            with self._lock:
                self._in_flight -= 1
```

`_send_one()` under the lock either counts a skip and returns (all senders busy) or increments the in-flight counter; it then notes `due_at`, the time of the decision, and submits `_run_request(due_at)` to the executor (a `lambda` carries the value). `_run_request(due_at)` first checks whether the request is starting more than `LATE_SECONDS` after `due_at` (the sender threads were busy) and counts a late start if so; then it records the requester's sample and, in a `finally` block that always runs, decrements the in-flight counter.

### `src/loadgen/infrastructure/http_requester.py`

```text
"""The Requester adapter: one HTTP GET on a new connection, classified into an Outcome."""

from __future__ import annotations

import http.client
import socket
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from loadgen.domain.outcome import Outcome, Sample

# Linux keeps a half-open connection in its connection-tracking table for 120 seconds, with the destination the
# Service chose for it. A new connection that reuses the same source port is matched to that entry and sent to
# the same (dead or not yet ready) pod again, so it times out too. A port that just failed is not used again for
# a little longer than that.
QUARANTINE_SECONDS = 125.0
BIND_ATTEMPTS = 50
```

The docstring says what the module is. The imports: `http.client` (Python's low-level HTTP client), `socket` (the operating system's network endpoints), `threading`, `time`, `Callable` (a type for anything callable) and `urlsplit` (splits a URL into parts), then the outcome types. The long comment explains why the file keeps a list of recently failed source ports. Linux keeps a half-open connection in its connection-tracking table (the kernel's memory of every connection, which Kubernetes' Service rules rely on) for 120 seconds, together with the pod the Service chose for it. A new connection that reuses the same source port is matched to that entry and sent to the same dead or not-yet-ready pod again, so it times out too, even though the Service no longer lists that pod. So a port that just failed is not used again for a little longer than 120 seconds: `QUARANTINE_SECONDS = 125.0`. `BIND_ATTEMPTS = 50` limits how many times a socket is re-bound to find an allowed port.

```text
class PortQuarantine:
    """Source ports that recently carried a failed request, each held back until its expiry time."""

    def __init__(self, hold_seconds: float = QUARANTINE_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        """Hold each added port for `hold_seconds` on `clock`."""
        self._hold = hold_seconds
        self._clock = clock
        self._until: dict[int, float] = {}
        self._lock = threading.Lock()

    def add(self, port: int) -> None:
        """Hold a port back from now on."""
        with self._lock:
            self._until[port] = self._clock() + self._hold

    def __contains__(self, port: object) -> bool:
        """True while the port is held back; expired entries are forgotten."""
        with self._lock:
            now = self._clock()
            for expired in [held for held, until in self._until.items() if until <= now]:
                del self._until[expired]
            return port in self._until
```

`PortQuarantine` holds the ports of recently failed requests. The constructor takes how long to hold (default 125 s) and a clock function (default `time.monotonic`, replaceable in tests), a dictionary from port to expiry time and a lock, because many sender threads use it at once. `add(port)` records `now + hold` under the lock. `__contains__` makes `port in quarantine` work: under the lock it forgets every entry whose time has passed (a list comprehension finds them first so the dictionary is not changed while being read) and then reports whether the port is still held.

```text
    def __len__(self) -> int:
        """How many ports are held back."""
        with self._lock:
            return len(self._until)


class HttpRequester:
    """Opens a new connection per request on purpose: each request then meets whatever the Service routes it
    to, which is what exposes a pod that is not ready or is going away. It never raises; failures are samples.

    The source port of a request that fails is held back (see PortQuarantine), so that one failure cannot make
    later requests fail for the same stale reason and be counted as if the pods had failed them.
    """
```

`__len__` returns how many ports are held (`len(quarantine)`). `HttpRequester` begins. Its docstring keeps the earlier rule (a new connection per request, so each request meets whatever the Service routes it to; it never raises, failures are samples) and adds the new one: the source port of a request that fails is held back, so one failure cannot make later requests fail for the same stale reason and be counted as if the pods had failed them.

```text
    def __init__(self, url: str, timeout_seconds: float) -> None:
        """Parse the target URL once."""
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError(f"TARGET_URL must be an http:// URL with a host, got {url!r}")
        self._host = parts.hostname
        self._port = parts.port or 80
        self._path = parts.path or "/"
        self._timeout = timeout_seconds
        self.quarantine = PortQuarantine()

    def request(self) -> Sample:
        """Send one request and classify what happened."""
        started = time.monotonic()
        outcome = self._attempt()
        return Sample(outcome, time.monotonic() - started)

    def _attempt(self) -> Outcome:
        outcome, port = self._send()
        if outcome.is_error and port:
            self.quarantine.add(port)
        return outcome
```

The constructor parses the URL once as before and creates the `quarantine` (public so tests can read it). `request()` notes the start time, calls `_attempt()` and returns a `Sample` with the outcome and the elapsed seconds. `_attempt()` calls `_send()`, which returns the outcome and the local port used; if the outcome is an error and a port was used (`port` is not 0), the port is added to the quarantine; then the outcome is returned.

```text
    def _send(self) -> tuple[Outcome, int]:
        """One request on a new connection; returns its outcome and the local port it used (0 if none)."""
        connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        port = 0
        try:
            connection.sock = self._bound_socket()
            port = connection.sock.getsockname()[1]
            connection.sock.connect((self._host, self._port))
            connection.request("GET", self._path, headers={"Connection": "close"})
            response = connection.getresponse()
            response.read()
            return self._classify(response.status), port
        except TimeoutError:
            return Outcome.TIMEOUT, port
        except (OSError, http.client.HTTPException):
            return Outcome.CONNECTION, port
        finally:
            connection.close()
```

`_send()` creates an `HTTPConnection` (which is not connected yet) and sets `port = 0`. In the `try`: `connection.sock = self._bound_socket()` hands the connection a socket that is bound to an allowed local port but not connected, so the library will use it instead of opening its own; `port` is read from it with `getsockname()[1]`; `connect` then performs the TCP handshake with the target; the request is sent with `Connection: close`, the response is read completely and `_classify` maps the status. A `TimeoutError` becomes `Outcome.TIMEOUT`; any other `OSError` (refused, reset, unreachable) or `HTTPException` becomes `Outcome.CONNECTION`; both return the port so a failed one can be held back. `finally: connection.close()` always releases the socket.

```text
    def _bound_socket(self) -> socket.socket:
        """A socket bound to a free local port that is not held back (not yet connected)."""
        for attempt in range(BIND_ATTEMPTS):
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            candidate.settimeout(self._timeout)
            candidate.bind(("", 0))
            if attempt == BIND_ATTEMPTS - 1 or candidate.getsockname()[1] not in self.quarantine:
                return candidate
            candidate.close()
        raise AssertionError("unreachable")

    @staticmethod
    def _classify(status: int) -> Outcome:
        if 200 <= status < 300:
            return Outcome.OK
        return Outcome.HTTP_5XX if status >= 500 else Outcome.HTTP_OTHER
```

`_bound_socket()` tries up to 50 times: create a TCP socket (`AF_INET`, `SOCK_STREAM`), give it the request timeout, `bind(("", 0))` (any local address, port 0 meaning "the operating system chooses a free port") and read which port was chosen. If it is the last attempt, or the port is not in the quarantine, the socket is returned; otherwise it is closed and another is tried. The final `raise AssertionError("unreachable")` is for the type checker. `_classify(status)` maps 200 to 299 to `OK`, 500 and above to `HTTP_5XX` and everything else to `HTTP_OTHER`.

### `src/loadgen/interfaces/control.py`

```text
"""The control API: read and reset the counters, change the rate. Plain JSON over HTTP on one port.

GET  /healthz  liveness
GET  /stats    the current window, without changing it
POST /reset    the current window's final reading; a new window starts
POST /rate     body {"rps": 300} changes the request rate
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from loadgen.application.runner import LoadRunner
from loadgen.domain.errors import InvalidRateError


def make_server(runner: LoadRunner, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """Build the control server bound to the runner; the caller serves it and shuts it down."""
```

The docstring lists the four endpoints. `make_server(runner, port, host="0.0.0.0")` builds a `ThreadingHTTPServer` (Python's standard server that handles each request on its own thread) bound to the runner; the caller serves and shuts it down.

```text
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._reply(200, {"status": "alive"})
            elif self.path == "/stats":
                self._reply(200, {**runner.snapshot().as_dict(), "rps": runner.rate})
            else:
                self._reply(404, {"error": "not found"})
```

The nested class `Handler` extends `BaseHTTPRequestHandler`. `_reply(status, body)` encodes the dictionary as JSON bytes and sends the status line, the `Content-Type` and `Content-Length` headers and the body. `do_GET` answers `/healthz` with `{"status": "alive"}` and `/stats` with the current snapshot plus `rps`, otherwise 404.

```text
        def do_POST(self) -> None:
            if self.path == "/reset":
                self._reply(200, {**runner.reset().as_dict(), "rps": runner.rate})
            elif self.path == "/rate":
                self._set_rate()
            else:
                self._reply(404, {"error": "not found"})

        def _set_rate(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                rate = float(json.loads(self.rfile.read(length))["rps"])
                runner.set_rate(rate)
            except (ValueError, KeyError, TypeError, InvalidRateError) as error:
                self._reply(400, {"error": f'body must be {{"rps": <number>}}: {error}'})
                return
            self._reply(200, {"rps": runner.rate})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
```

`do_POST` answers `/reset` with the final reading of the window plus the rate, and `/rate` by calling `_set_rate`; anything else is 404. `_set_rate` reads `Content-Length` bytes of the body, parses the JSON, takes `rps` as a float and calls `runner.set_rate`; a missing or invalid body or an invalid rate gives status 400 with a message; otherwise 200 with the new rate. `log_message` is overridden to do nothing, so the server does not print a line per request. The function returns the `ThreadingHTTPServer` bound to `(host, port)` with `Handler`.

### `src/loadgen/interfaces/cli.py`

```text
"""The load generator program: send at a fixed rate, serve the control API, print the final counts on SIGTERM."""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
from types import FrameType

from loadgen.application.runner import LoadRunner
from loadgen.infrastructure.http_requester import HttpRequester
from loadgen.infrastructure.logging import configure_logging
from loadgen.infrastructure.runtime import PoolExecutor, SystemClock
from loadgen.infrastructure.settings import ConfigurationError, Settings
from loadgen.interfaces.control import make_server

log = logging.getLogger("loadgen")
```

The docstring states the program: send at a fixed rate, serve the control API, print the final counts on SIGTERM. Imports: `json`, `logging`, `os`, `signal` (receive operating-system signals), `threading` and the pieces to wire together. `log` is the logger.

```text
def main() -> int:
    """Run until SIGTERM or SIGINT, then print the final counts as one JSON line and return the exit code."""
    try:
        settings = Settings.from_env(dict(os.environ))
    except ConfigurationError as error:
        configure_logging("INFO")
        log.critical(f"FATAL invalid configuration: {error}")
        return 1
    configure_logging(settings.log_level)
    runner = LoadRunner(
        HttpRequester(settings.target_url, settings.timeout_seconds),
        SystemClock(),
        PoolExecutor(settings.max_in_flight),
        settings.rate_per_second,
        settings.max_in_flight,
    )
    server = make_server(runner, settings.control_port)
    stopping = threading.Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stopping.set()
```

`main()` reads the settings from the environment; an invalid one logs `FATAL invalid configuration: ...` and returns exit code 1. It configures logging, builds the `LoadRunner` from an `HttpRequester`, a `SystemClock`, a `PoolExecutor` and the settings, creates the control server and a `threading.Event` called `stopping`; the nested function `request_stop` sets the event when a signal arrives.

```text
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    runner.start()
    threading.Thread(target=server.serve_forever, name="control", daemon=True).start()
    log.info(
        "sending",
        extra={"target": settings.target_url, "rps": settings.rate_per_second, "control_port": settings.control_port},
    )
    stopping.wait()
    server.shutdown()
    server.server_close()
    final = runner.stop()
    print(json.dumps({"final": final.as_dict()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

It registers `request_stop` for `SIGTERM` and `SIGINT` (Ctrl-C), starts the runner, serves the control API on a daemon thread, logs `sending` with the target and rate, and waits on `stopping.wait()`. After a signal: shut the server down and close its socket, stop the runner (which waits for requests in flight and returns the final window), print `{"final": {...}}` as one JSON line (`flush=True` forces it out immediately) and return 0. The last two lines run `main()` when the file is executed as a program.


## 6. Try it yourself

```bash
make up
make stats
```

Prints the current window as JSON, for example `{"total": 4120, "ok": 4120, "errors": 0, "connection": 0, "timeout": 0, "http_5xx": 0, "http_other": 0, "skipped": 0, "p50_ms": 128.9, "p99_ms": 200.4, "max_ms": 203.7, "rps": 100.0}`.

```bash
make rate RPS=300 && sleep 20 && make stats
```

The rate triples; `total` grows three times as fast. Put it back with `make rate RPS=100`.

```bash
make deploy OVERLAY=naive && make run
```

The naive overlay has no readiness probe and no graceful shutdown: each `rollout N of 3` line shows non-zero `connection` errors. Go back with `make deploy`, and the same `make run` shows `errors: 0`.

```bash
.venv/bin/python -m pytest tests/unit/test_loadgen_domain.py tests/unit/test_loadgen_runner.py -q
```

Unit tests for the pacer, the statistics and the runner, with a fake clock.

## 7. Mistakes people make

- **Closed-loop load.** Waiting for each answer before sending the next hides outages and understates the error count.
- **Reusing connections.** Keep-alive connections stay attached to one pod and never meet a pod that has just become ready.
- **Queueing instead of counting.** An overloaded generator then reports slow answers that are really its own delay.
- **Reading an average.** Averages hide the slow tail; p99 and the maximum show it.
- **Resetting at the wrong time.** The tests reset the window right before the operation and read it right after, so earlier traffic does not pollute the verdict.

## 8. Check yourself

1. What is the difference between open-loop and closed-loop load? *Open-loop sends on a schedule; closed-loop waits for each answer.*
2. Why does each request use a new connection? *So each one is routed afresh and meets pods that are starting or stopping.*
3. What does `skipped` mean and when is a verdict valid? *Requests the generator could not send in time; valid only when it is zero.*
4. What is p99? *The latency that 99 percent of requests stayed under.*
5. What happens on `SIGTERM`? *Sending stops, requests in flight finish, and the final counts are printed as one JSON line.*

## 9. Further reading

- Python `http.client`: https://docs.python.org/3.13/library/http.client.html
- Python `http.server`: https://docs.python.org/3.13/library/http.server.html
- `concurrent.futures`: https://docs.python.org/3.13/library/concurrent.futures.html
- `threading`: https://docs.python.org/3.13/library/threading.html
- `signal`: https://docs.python.org/3.13/library/signal.html
- Open and closed workload models (concept overview): https://en.wikipedia.org/wiki/Load_testing
