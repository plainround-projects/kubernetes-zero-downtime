# Unit tests

## 1. What it is

A **unit test** checks one small piece of the program (a class, a function) on its own, fast, with no network, no Docker and no cluster. Anything the piece would normally talk to is replaced by a **fake**: a simple stand-in written for the test. The tools are pytest (`pytest.md`) plus Python's `monkeypatch` and `capfd` helpers, explained below.

## 2. Why this repository uses it

The domain and application layers of both programs must stay above 95 percent coverage, and the behaviour the lesson relies on (a request's latency split into computing and waiting, the load generator's pacing, percentiles and error classes) has to be exact. Unit tests prove it in seconds, using fake clocks so no test sleeps for real time, so they run on every `make test`.

## 3. Where it appears

| File | Tests |
| --- | --- |
| `tests/unit/test_profile.py` | The request profile (latency range, split, bounds). |
| `tests/unit/test_use_cases.py` | `HandleRequest` and `WarmUp` with a recording fake. |
| `tests/unit/test_settings.py` | Reading and checking the service's environment. |
| `tests/unit/test_runtime.py` | The real random source, waiter and burner. |
| `tests/unit/test_logging.py` | JSON log lines and logging setup. |
| `tests/unit/test_http.py` | The Flask routes, via the test client. |
| `tests/unit/test_wsgi.py` | The start-up code: warm-up delay and the FATAL exit. |
| `tests/unit/test_loadgen_domain.py` | Outcomes, percentiles, statistics window, pacer. |
| `tests/unit/test_loadgen_runner.py` | The runner with a fake clock and fake executors. |
| `tests/unit/test_loadgen_infrastructure.py` | The HTTP requester, settings and control API against a local test server. |

## 4. How it works underneath

pytest looks for `conftest.py` in the test folder; fixtures defined there are available to every test file beside it without importing. A test asks for a fixture by naming a parameter: `def test_x(store):` receives whatever the `store` fixture returned.

`monkeypatch` is a built-in fixture that replaces an attribute (a function, a variable) for the length of one test and puts the original back afterwards. `capfd` captures what the program writes to file descriptors 1 and 2 (standard output and error), so the test can read the log lines, including ones written by libraries. `pytest.mark.parametrize` runs the same test body once per listed input. `pytest.raises(Error, match="text")` passes only when the block raises that error and the message contains `text` (a regular expression).

The fakes follow the **ports and adapters** idea: the application code depends on an interface (a port such as `Randomness` or `Clock`), and the fake is another implementation of the same interface, so the test never needs the database.

## 5. Walkthrough of this repository's use

### `tests/unit/test_profile.py`

```text
import pytest

from zero_downtime.domain.errors import InvalidProfileError
from zero_downtime.domain.profile import RequestProfile


def test_the_default_profile_is_fifty_to_two_hundred_milliseconds():
    profile = RequestProfile()
    assert (profile.min_ms, profile.max_ms) == (50.0, 200.0)


@pytest.mark.parametrize(("fraction", "expected"), [(0.0, 50.0), (0.5, 125.0), (1.0, 200.0)])
def test_latency_maps_the_random_fraction_onto_the_range(fraction, expected):
    assert RequestProfile().latency_ms(fraction) == pytest.approx(expected)


def test_a_latency_splits_into_computing_and_waiting():
    assert RequestProfile(cpu_ms=3).split(100.0) == (3, 97.0)
```

`test_the_default_profile_is_fifty_to_two_hundred_milliseconds` checks the defaults. `parametrize` runs `latency_ms` for fractions 0.0, 0.5 and 1.0 and expects 50, 125 and 200 (`pytest.approx` compares floats allowing tiny rounding). `test_a_latency_splits_into_computing_and_waiting` checks `RequestProfile(cpu_ms=3).split(100.0) == (3, 97.0)`.

```text
@pytest.mark.parametrize(
    "bad",
    [
        {"min_ms": -1},
        {"min_ms": 100, "max_ms": 50},
        {"cpu_ms": -1},
        {"min_ms": 10, "max_ms": 20, "cpu_ms": 11},
    ],
)
def test_impossible_bounds_are_rejected(bad):
    with pytest.raises(InvalidProfileError):
        RequestProfile(**bad)
```

Four impossible profiles (negative minimum, maximum below minimum, negative CPU time, CPU time above the minimum latency) must each raise `InvalidProfileError`; the dictionary is unpacked into the constructor with `**bad`.

### `tests/unit/test_use_cases.py`

```text
from zero_downtime.application.use_cases import HandleRequest, WarmUp
from zero_downtime.domain.profile import RequestProfile


class FixedRandomness:
    def __init__(self, value):
        self.value = value

    def fraction(self):
        return self.value


class Recorder:
    def __init__(self):
        self.burned, self.waited = [], []

    def burn(self, milliseconds):
        self.burned.append(milliseconds)

    def wait(self, milliseconds):
        self.waited.append(milliseconds)
```

Two helper fakes: `FixedRandomness` always returns one number, and `Recorder` implements both `burn` and `wait` by writing the requested milliseconds into lists.

```text
def test_a_request_computes_for_cpu_ms_and_waits_for_the_rest():
    recorder = Recorder()
    handled = HandleRequest(RequestProfile(cpu_ms=2), FixedRandomness(0.5), recorder, recorder)()
    assert handled.latency_ms == 125.0
    assert recorder.burned == [2]
    assert recorder.waited == [123.0]


def test_the_slowest_request_takes_the_upper_bound():
    recorder = Recorder()
    assert HandleRequest(RequestProfile(), FixedRandomness(1.0), recorder, recorder)().latency_ms == 200.0


def test_warm_up_waits_out_the_delay_and_reports_it():
    recorder = Recorder()
    assert WarmUp(recorder, 1000.0)() == 1000.0
    assert recorder.waited == [1000.0]
```

A request with 0.5 as the random fraction has latency 125 ms; the recorder shows it burned 2 ms and waited 123 ms. With fraction 1.0 the latency is the upper bound, 200 ms. `WarmUp` waits the full delay and returns it (1000 ms in the test).

### `tests/unit/test_settings.py`

```text
import pytest

from zero_downtime.infrastructure.settings import ConfigurationError, Settings


def test_defaults_are_the_lessons_service():
    settings = Settings.from_env({})
    assert settings.startup_delay_seconds == 1.0
    assert (settings.profile.min_ms, settings.profile.max_ms) == (50.0, 200.0)
```

With an empty environment the defaults are a one-second warm-up and the 50 to 200 ms profile.

```text
def test_values_come_from_the_environment():
    settings = Settings.from_env(
        {
            "STARTUP_DELAY_SECONDS": "0.5",
            "LATENCY_MIN_MS": "10",
            "LATENCY_MAX_MS": "20",
            "CPU_WORK_MS": "1",
            "LOG_LEVEL": "warning",
        }
    )
    assert settings.startup_delay_seconds == 0.5
    assert (settings.profile.min_ms, settings.profile.max_ms, settings.profile.cpu_ms) == (10, 20, 1)
    assert settings.log_level == "WARNING"
```

A full environment is read: warm-up 0.5 s, latency range 10 to 20, CPU 1 ms and `LOG_LEVEL=warning` upper-cased to `WARNING`.

```text
@pytest.mark.parametrize(
    ("env", "variable"),
    [
        ({"STARTUP_DELAY_SECONDS": "x"}, "STARTUP_DELAY_SECONDS"),
        ({"STARTUP_DELAY_SECONDS": "-1"}, "STARTUP_DELAY_SECONDS"),
        ({"LATENCY_MIN_MS": "300"}, "LATENCY_MIN_MS"),
        ({"CPU_WORK_MS": "100"}, "CPU_WORK_MS"),
        ({"LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
    ],
)
def test_a_bad_value_names_the_variable(env, variable):
    with pytest.raises(ConfigurationError, match=variable):
        Settings.from_env(env)
```

Five bad inputs, each paired with the variable the error must name: a warm-up that is not a number, a negative warm-up, a minimum latency (300) above the default maximum, a CPU time (100) above the minimum latency, and an invalid log level.

### `tests/unit/test_runtime.py`

```text
import time

from zero_downtime.infrastructure.runtime import SleepWaiter, SpinBurner, SystemRandomness


def test_seeded_randomness_is_repeatable_and_in_range():
    first, second = SystemRandomness(7), SystemRandomness(7)
    values = [first.fraction() for _ in range(5)]
    assert values == [second.fraction() for _ in range(5)]
    assert all(0 <= value < 1 for value in values)


def test_the_waiter_blocks_for_about_the_requested_time_and_ignores_zero():
    started = time.monotonic()
    SleepWaiter().wait(30)
    SleepWaiter().wait(0)
    assert 0.025 <= time.monotonic() - started < 0.5


def test_the_burner_loops_until_the_deadline_on_its_clock():
    reads: list[int] = []
```

Two generators with the same seed give the same five numbers, all in [0, 1). The waiter blocks for about 30 ms and ignores zero (the elapsed time must be between 25 ms and 0.5 s). The third test begins: it builds a fake clock for the burner.

```text
    def clock():
        reads.append(len(reads))
        return len(reads) / 1000  # every read advances the clock by one millisecond

    SpinBurner(clock).burn(40)
    assert 40 <= len(reads) <= 43


def test_the_burner_with_the_real_clock_takes_at_least_the_requested_time():
    started = time.monotonic()
    SpinBurner().burn(30)
    assert time.monotonic() - started >= 0.03
```

The fake clock appends to a list on every read and returns the list length divided by 1000, so each read advances time by one millisecond; `burn(40)` must therefore read the clock between 40 and 43 times. The last test uses the real clock and requires at least 30 ms to pass.

### `tests/unit/test_logging.py`

```text
import json
import logging

from zero_downtime.infrastructure.logging import JsonFormatter, configure_logging


def _record(**extra):
    record = logging.LogRecord("zero_downtime", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.__dict__.update(extra)
    return record


def test_a_record_becomes_one_json_line_with_extra_fields():
    line = JsonFormatter().format(_record(redis_host="redis"))
    entry = json.loads(line)
    assert (entry["level"], entry["logger"], entry["msg"], entry["redis_host"]) == (
        "INFO",
        "zero_downtime",
        "hello world",
        "redis",
    )
    assert "\n" not in line
```

`_record(**extra)` builds a `logging.LogRecord` by hand and copies extra fields in. The first test formats it and parses the output with `json.loads`: level, logger name, the message with its placeholder filled, the extra field, and no newline in the line.

```text
def test_an_exception_is_included_as_text():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exc"]


def test_configure_logging_installs_one_json_handler_at_the_level():
    configure_logging("WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
```

An exception record gives an `exc` field containing `ValueError: boom`. `configure_logging("WARNING")` sets the root level, leaves exactly one handler and uses the JSON formatter.

### `tests/unit/test_http.py`

```text
import pytest

from zero_downtime.application.use_cases import HandleRequest
from zero_downtime.domain.profile import RequestProfile
from zero_downtime.interfaces.http import create_app


class Instant:
    def fraction(self):
        return 0.0

    def burn(self, milliseconds):
        return None

    def wait(self, milliseconds):
        return None


@pytest.fixture
def client():
    instant = Instant()
    return create_app(HandleRequest(RequestProfile(), instant, instant, instant)).test_client()
```

`Instant` is a fake that implements `fraction` (always 0.0), `burn` and `wait` (both do nothing), so no time is spent. The `client` fixture builds the app around `HandleRequest` with it and returns the test client.

```text
def test_the_root_does_the_work_and_names_the_pod(client):
    body = client.get("/").get_json()
    assert body["status"] == "ok"
    assert body["pod"]
    assert body["latency_ms"] == 50.0


def test_healthz_and_readyz_answer(client):
    assert client.get("/healthz").get_json() == {"status": "alive"}
    assert client.get("/readyz").get_json() == {"status": "ready"}
```

`GET /` returns `status: ok`, a pod name and `latency_ms` of 50.0 (fraction 0 gives the minimum). `/healthz` returns `{"status": "alive"}` and `/readyz` returns `{"status": "ready"}`.

### `tests/unit/test_wsgi.py`

```text
import time

import pytest

from zero_downtime.interfaces.wsgi import build_app


def test_warm_up_delays_the_return_of_the_app():
    started = time.monotonic()
    app = build_app({"STARTUP_DELAY_SECONDS": "0.2", "LATENCY_MIN_MS": "0", "LATENCY_MAX_MS": "0", "CPU_WORK_MS": "0"})
    assert time.monotonic() - started >= 0.2
    assert app.test_client().get("/healthz").status_code == 200


def test_a_bad_configuration_exits_naming_the_variable(capfd):
    with pytest.raises(SystemExit) as exit_info:
        build_app({"CPU_WORK_MS": "oops"})
    assert exit_info.value.code == 1
    assert "CPU_WORK_MS" in capfd.readouterr().err
```

`test_warm_up_delays_the_return_of_the_app` builds the app with a 0.2-second warm-up and zero latency, checks that at least 0.2 s passed and that `/healthz` answers 200. `test_a_bad_configuration_exits_naming_the_variable` passes `CPU_WORK_MS=oops`; `capfd` captures standard error and the test expects `SystemExit` with code 1 and the variable named.

### `tests/unit/test_loadgen_domain.py`

```text
import pytest

from loadgen.domain.errors import InvalidRateError
from loadgen.domain.outcome import Outcome, Sample
from loadgen.domain.pacer import Pacer
from loadgen.domain.stats import StatsWindow, percentile


def test_only_ok_is_not_an_error():
    assert [outcome.is_error for outcome in Outcome] == [False, True, True, True, True]


def test_percentile_uses_nearest_rank():
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    assert percentile(values, 0.5) == 0.5
    assert percentile(values, 0.99) == 1.0
    assert percentile([], 0.99) == 0.0


def test_an_empty_window_reads_as_zeros():
    snapshot = StatsWindow().snapshot()
    assert (snapshot.total, snapshot.errors, snapshot.p99_ms, snapshot.max_ms) == (0, 0, 0.0, 0.0)
```

Only `OK` is not an error: the list of `is_error` values for the five outcomes is `[False, True, True, True, True]`. The percentile test uses ten values (0.1 to 1.0): p50 is 0.5, p99 is 1.0, and an empty list gives 0.0. An empty window reads as zeros.

```text
def test_every_error_class_is_counted_on_its_own_and_summed():
    window = StatsWindow()
    for outcome in [Outcome.OK, Outcome.OK, Outcome.CONNECTION, Outcome.TIMEOUT, Outcome.HTTP_5XX, Outcome.HTTP_OTHER]:
        window.record(Sample(outcome, 0.1))
    snapshot = window.snapshot()
    assert (snapshot.total, snapshot.ok, snapshot.errors) == (6, 2, 4)
    assert (snapshot.connection, snapshot.timeout, snapshot.http_5xx, snapshot.http_other) == (1, 1, 1, 1)


def test_latency_percentiles_are_in_milliseconds():
    window = StatsWindow()
    for index in range(1, 101):
        window.record(Sample(Outcome.OK, index / 1000))
    snapshot = window.snapshot()
    assert snapshot.p50_ms == pytest.approx(50)
    assert snapshot.p99_ms == pytest.approx(99)
    assert snapshot.max_ms == pytest.approx(100)
```

Each error class is counted on its own and summed (six samples: two OK, one of each error; `total` 6, `ok` 2, `errors` 4). Latencies 1 ms to 100 ms give p50 50, p99 99 and max 100 in milliseconds.

```text
def test_reset_returns_the_final_reading_and_starts_over():
    window = StatsWindow()
    window.record(Sample(Outcome.CONNECTION, 0.1))
    window.skip()
    final = window.reset()
    assert (final.errors, final.skipped) == (1, 1)
    assert window.snapshot().total == 0
    assert window.snapshot().skipped == 0


def test_the_dictionary_form_includes_the_error_total():
    window = StatsWindow()
    window.record(Sample(Outcome.HTTP_5XX, 0.2))
    assert window.snapshot().as_dict()["errors"] == 1


def test_the_pacer_makes_sends_due_at_a_fixed_interval():
    pacer = Pacer(10, start=0.0)
    assert pacer.due(0.0) == 1
    assert pacer.due(0.05) == 0
    assert pacer.due(0.1) == 1
    assert pacer.seconds_until_next(0.1) == pytest.approx(0.1)
```

`reset` returns the final reading (one error, one skip) and leaves an empty window. The dictionary form includes `errors`. The pacer test: at 10 per second, one send is due at time 0, none at 0.05, one at 0.1, and the next is 0.1 s away.

```text
def test_a_stall_is_caught_up_by_sending_the_missed_requests():
    pacer = Pacer(10, start=0.0)
    assert pacer.due(1.0) == 11


def test_changing_the_rate_neither_bursts_nor_leaves_a_gap():
    pacer = Pacer(10, start=0.0)
    assert pacer.due(0.0) == 1
    pacer.set_rate(100, now=0.0)
    assert pacer.due(0.05) == 5


@pytest.mark.parametrize("rate", [0, -5, 10_001])
def test_a_rate_outside_the_range_is_rejected(rate):
    with pytest.raises(InvalidRateError):
        Pacer(rate, 0.0)
    with pytest.raises(InvalidRateError):
        Pacer(10, 0.0).set_rate(rate, 0.0)
```

After a one-second stall, 11 sends are due at once (the missed ones are caught up). Changing from 10 to 100 per second at time 0 makes five sends due by 0.05 (no burst, no gap). Rates 0, -5 and 10,001 are rejected by both the constructor and `set_rate`.

```text
def test_the_pacer_reports_how_overdue_the_oldest_send_is():
    pacer = Pacer(10, start=0.0)
    assert pacer.lateness(0.0) == 0.0
    assert pacer.lateness(0.35) == pytest.approx(0.35)
    pacer.due(0.35)
    assert pacer.lateness(0.35) == 0.0


def test_late_starts_are_counted_and_cleared_by_reset():
    window = StatsWindow()
    window.late()
    window.late(3)
    assert window.snapshot().late_starts == 4
    assert window.snapshot().as_dict()["late_starts"] == 4
    assert window.reset().late_starts == 4
    assert window.snapshot().late_starts == 0
```

`test_the_pacer_reports_how_overdue_the_oldest_send_is`: at rate 10 with start 0, `lateness(0.0)` is 0.0, at 0.35 it is 0.35 seconds overdue, and after `due(0.35)` has taken the owed sends it is 0.0 again. `test_late_starts_are_counted_and_cleared_by_reset`: `late()` and `late(3)` give four late starts in the snapshot and in its dictionary form, and `reset()` returns the four and starts a window at zero.

### `tests/unit/test_loadgen_infrastructure.py`

```text
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loadgen.application.runner import LoadRunner
from loadgen.domain.outcome import Outcome
from loadgen.infrastructure.http_requester import HttpRequester, PortQuarantine
from loadgen.infrastructure.runtime import PoolExecutor, SystemClock
from loadgen.infrastructure.settings import ConfigurationError, Settings
from loadgen.interfaces.control import make_server


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = int(self.path.strip("/") or 200)
        self.send_response(status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
```

Imports for threads, JSON and a local HTTP server. `StatusHandler` answers a GET with the status code written in the path (`/200`, `/503`), a two-byte body `ok` and a `Content-Length` header.

```text
    def log_message(self, format, *args):
        return


@pytest.fixture
def target():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
```

`log_message` is silenced. The `target` fixture starts a `ThreadingHTTPServer` on port 0 (a free port) in a daemon thread, yields its URL and shuts it down afterwards.

```text
@pytest.mark.parametrize(
    ("path", "outcome"),
    [
        ("/200", Outcome.OK),
        ("/204", Outcome.OK),
        ("/500", Outcome.HTTP_5XX),
        ("/503", Outcome.HTTP_5XX),
        ("/404", Outcome.HTTP_OTHER),
    ],
)
def test_the_requester_classifies_status_codes(target, path, outcome):
    assert HttpRequester(target + path, 2).request().outcome is outcome


def test_a_closed_port_is_a_connection_error():
    assert HttpRequester("http://127.0.0.1:1/", 1).request().outcome is Outcome.CONNECTION


def test_a_server_that_never_answers_is_a_timeout():
    import socket
```

The requester classifies 200 and 204 as `OK`, 500 and 503 as `HTTP_5XX`, 404 as `HTTP_OTHER`. A closed port (`127.0.0.1:1`) is a `CONNECTION` error. The next test begins: a server that never answers is a `TIMEOUT`.

```text
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        port = silent.getsockname()[1]
        assert HttpRequester(f"http://127.0.0.1:{port}/", 0.3).request().outcome is Outcome.TIMEOUT


def test_a_non_http_target_is_refused():
    with pytest.raises(ValueError, match="TARGET_URL"):
        HttpRequester("https://example.test/", 1)


def test_settings_require_a_target_and_validate_the_rest():
    assert Settings.from_env({"TARGET_URL": "http://web"}).rate_per_second == 100.0
    for env in ({}, {"TARGET_URL": "ftp://x"}, {"TARGET_URL": "http://web", "RATE_PER_SECOND": "0"},
                {"TARGET_URL": "http://web", "TIMEOUT_SECONDS": "x"}, {"TARGET_URL": "http://web", "LOG_LEVEL": "LOUD"},
                {"TARGET_URL": "http://web", "CONTROL_PORT": "0"}):  # fmt: skip
        with pytest.raises(ConfigurationError):
            Settings.from_env(env)
```

It opens a socket that listens but never accepts or replies, and a request with a 0.3-second timeout must be classified `TIMEOUT`. A non-`http` URL is refused with `ValueError` mentioning `TARGET_URL`. The settings test requires a target, checks the default rate 100, and requires six invalid environments (none, a non-http URL, rate 0, a non-numeric timeout, a bad log level, control port 0) to raise `ConfigurationError`.

```text
def _call(url, method="GET", body=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
```

`_call(url, method, body)` sends a request with `urllib` and returns (status, parsed JSON), also for HTTP error statuses (it catches `HTTPError` and reads its body).

```text
def test_the_control_api_reads_resets_and_changes_the_rate(target):
    runner = LoadRunner(HttpRequester(target + "/200", 2), SystemClock(), PoolExecutor(8), 50, 8)
    server = make_server(runner, 0, "127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    runner.start()
    try:
        assert _call(base + "/healthz") == (200, {"status": "alive"})
        assert _call(base + "/rate", "POST", {"rps": 80}) == (200, {"rps": 80.0})
        assert _call(base + "/rate", "POST", {"rps": 0})[0] == 400
        assert _call(base + "/rate", "POST", {"nope": 1})[0] == 400
        assert _call(base + "/nothing")[0] == 404
        assert _call(base + "/nothing", "POST", {})[0] == 404
        threading.Event().wait(0.5)
        status, reset = _call(base + "/reset", "POST", {})
        assert status == 200
        assert reset["total"] > 5
        assert reset["errors"] == 0
        assert _call(base + "/stats")[1]["total"] < reset["total"]
    finally:
        runner.stop()
        server.shutdown()
        server.server_close()
```

The control API test builds a real `LoadRunner` at 50 requests per second against the local target, serves the control API on port 0 and checks: `/healthz` is alive; `POST /rate` with `{"rps": 80}` returns 80.0; a rate of 0 and a body without `rps` give 400; unknown paths give 404 for GET and POST; after half a second `POST /reset` returns more than 5 requests and no errors, and a following `/stats` has fewer requests (the window was cleared). `finally` stops the runner and the server.

```text
def test_a_held_back_port_is_forgotten_after_its_time():
    now = [0.0]
    quarantine = PortQuarantine(hold_seconds=10, clock=lambda: now[0])
    quarantine.add(40000)
    assert 40000 in quarantine
    assert 40001 not in quarantine
    assert len(quarantine) == 1
    now[0] = 10.0
    assert 40000 not in quarantine
    assert len(quarantine) == 0


def test_a_failed_request_holds_its_source_port_back_and_a_good_one_does_not(target):
    requester = HttpRequester(target + "/200", 2)
    assert requester.request().outcome is Outcome.OK
    assert len(requester.quarantine) == 0
    failing = HttpRequester(target + "/503", 2)
    assert failing.request().outcome is Outcome.HTTP_5XX
    assert len(failing.quarantine) == 1
```

`test_a_held_back_port_is_forgotten_after_its_time` uses a mutable list `now` as a fake clock: a port added with a 10-second hold is in the quarantine, another port is not, the length is 1; when the clock reaches 10.0 the port is forgotten and the length is 0. `test_a_failed_request_holds_its_source_port_back_and_a_good_one_does_not`: a request answered 200 leaves the quarantine empty; one answered 503 (an error) leaves exactly one port held.

```text
def test_a_timeout_holds_its_source_port_back_and_a_new_request_avoids_it():
    import socket

    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        requester = HttpRequester(f"http://127.0.0.1:{silent.getsockname()[1]}/", 0.2)
        assert requester.request().outcome is Outcome.TIMEOUT
        held = next(iter(requester.quarantine._until))
        for _ in range(20):
            probe = requester._bound_socket()
            assert probe.getsockname()[1] != held
            probe.close()
```

`test_a_timeout_holds_its_source_port_back_and_a_new_request_avoids_it` opens a socket that listens but never answers, so a request with a 0.2-second timeout is a `TIMEOUT`. The quarantine's single port is read from its private dictionary (`# noqa: SLF001` tells ruff the private access is deliberate). Then twenty new bound sockets are made with `_bound_socket()` and none of them gets the held port; each is closed.

### `tests/unit/test_loadgen_runner.py`

```text
from loadgen.application.runner import LoadRunner
from loadgen.domain.outcome import Outcome, Sample


class FakeClock:
    def __init__(self):
        self.time = 0.0

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds


class Immediate:
    """Runs submitted work at once, on the caller's thread."""

    def submit(self, function):
        function()
```

`FakeClock` holds a `time` number: `now()` returns it and `sleep` adds to it. `Immediate` is an executor that runs submitted work at once on the caller's thread.

```text
    def shutdown(self):
        return None


class Held:
    """Keeps submitted work until released, to model senders that are all busy."""

    def __init__(self):
        self.pending = []

    def submit(self, function):
        self.pending.append(function)

    def release(self):
        for function in self.pending:
            function()
        self.pending.clear()

    def shutdown(self):
        self.release()
```

Its `shutdown` does nothing. `Held` is an executor that keeps submitted work in a list until `release()` runs it, to model senders that are all busy; its `shutdown` releases.

```text
class ScriptedRequester:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self):
        self.calls += 1
        return Sample(self.outcomes.pop(0) if self.outcomes else Outcome.OK, 0.05)


def test_ticking_for_one_second_at_ten_per_second_sends_eleven_requests_counting_the_first():
    clock = FakeClock()
    requester = ScriptedRequester([])
    runner = LoadRunner(requester, clock, Immediate(), rate_per_second=10, max_in_flight=5)
    for _ in range(10):
        runner.tick()
        clock.time += 0.1
    runner.tick()
    assert requester.calls == 11
    assert runner.snapshot().total == 11
```

`ScriptedRequester` returns the scripted outcomes in order (then `OK`) and counts calls. The first test ticks ten times 0.1 s apart and once more at 1.0 s: eleven requests, counting the first at time 0.

```text
def test_errors_are_recorded_by_class():
    clock = FakeClock()
    runner = LoadRunner(
        ScriptedRequester([Outcome.OK, Outcome.CONNECTION, Outcome.HTTP_5XX]), clock, Immediate(), 10, 5
    )
    for _ in range(3):
        runner.tick()
        clock.time += 0.1
    snapshot = runner.snapshot()
    assert (snapshot.ok, snapshot.connection, snapshot.http_5xx, snapshot.errors) == (1, 1, 1, 2)


def test_when_every_sender_is_busy_the_request_is_skipped_not_queued():
    clock = FakeClock()
    held = Held()
    runner = LoadRunner(ScriptedRequester([]), clock, held, rate_per_second=10, max_in_flight=2)
    clock.time = 0.5
    runner.tick()  # six sends are due, two senders exist
    assert len(held.pending) == 2
    assert runner.snapshot().skipped == 4
    held.release()
    assert runner.snapshot().total == 2
```

Errors are recorded by class (one OK, one connection error, one 5xx: two errors). With every sender busy (`max_in_flight=2`, six sends due at 0.5 s) two requests are held, four are `skipped`, and after release two are counted.

```text
def test_a_finished_request_frees_its_sender():
    clock = FakeClock()
    held = Held()
    runner = LoadRunner(ScriptedRequester([]), clock, held, rate_per_second=10, max_in_flight=1)
    runner.tick()
    held.release()
    clock.time = 0.1
    runner.tick()
    assert len(held.pending) == 1
    assert runner.snapshot().skipped == 0


def test_changing_the_rate_changes_the_pace():
    clock = FakeClock()
    requester = ScriptedRequester([])
    runner = LoadRunner(requester, clock, Immediate(), rate_per_second=10, max_in_flight=100)
    runner.tick()
    runner.set_rate(100)
    assert runner.rate == 100
    clock.time = 0.1
    runner.tick()
    assert requester.calls == 11
```

A finished request frees its sender (with one sender, after release the next tick can submit again and nothing is skipped). Changing the rate from 10 to 100 makes eleven calls by time 0.1.

```text
def test_reset_hands_back_the_window_and_stop_returns_the_final_reading():
    clock = FakeClock()
    runner = LoadRunner(ScriptedRequester([Outcome.TIMEOUT]), clock, Immediate(), 10, 5)
    runner.tick()
    assert runner.reset().timeout == 1
    assert runner.snapshot().total == 0
    runner.start()
    final = runner.stop()
    assert final.errors == 0


def test_a_stalled_pacer_thread_counts_its_overdue_requests_as_late_starts():
    clock = FakeClock()
    runner = LoadRunner(ScriptedRequester([]), clock, Immediate(), rate_per_second=10, max_in_flight=100)
    runner.tick()
    clock.time = 1.0  # the pacer thread was held up for a second
    runner.tick()
    assert runner.snapshot().late_starts == 10
```

`reset()` hands back the window (one timeout) and starts an empty one; `start()` then `stop()` returns a final reading with no errors. `test_a_stalled_pacer_thread_counts_its_overdue_requests_as_late_starts`: after one tick at time 0, the fake clock jumps to 1.0 (the pacer thread was held up for a second); the next tick finds ten sends overdue by about a second, so `late_starts` is 10.

```text
def test_a_sender_that_starts_late_counts_a_late_start():
    clock = FakeClock()
    held = Held()
    runner = LoadRunner(ScriptedRequester([]), clock, held, rate_per_second=10, max_in_flight=5)
    runner.tick()
    clock.time = 0.5  # the sender threads were busy for half a second before running the request
    held.release()
    assert runner.snapshot().late_starts == 1


def test_a_prompt_sender_is_not_late():
    clock = FakeClock()
    runner = LoadRunner(ScriptedRequester([]), clock, Immediate(), rate_per_second=10, max_in_flight=5)
    for _ in range(5):
        runner.tick()
        clock.time += 0.1
    assert runner.snapshot().late_starts == 0
```

`test_a_sender_that_starts_late_counts_a_late_start`: one request is submitted to the `Held` executor at time 0, the clock moves to 0.5 (the senders were busy) and the request is released; it started 0.5 s after it was due, so one late start is counted. `test_a_prompt_sender_is_not_late`: five ticks 0.1 s apart with an immediate executor produce no late starts.


## 6. Try it yourself

```bash
.venv/bin/python -m pytest tests/unit -q
```

All unit tests pass; the final line shows the count and the time.

```bash
.venv/bin/python -m pytest tests/unit/test_loadgen_runner.py -v
```

Lists each runner test by name; none takes more than a few milliseconds because the clock is a fake.

```bash
.venv/bin/python -m pytest tests/unit -q --cov=zero_downtime --cov=loadgen --cov-report=term-missing | tail -30
```

Shows the percentages and the missing lines per file.

## 7. Mistakes people make

- **Testing the fake instead of the code.** Each test here asserts something about the real class with a fake around it.
- **Leaking a patch.** `monkeypatch` is used so the original is always restored.
- **Asserting on a log message's exact words everywhere.** Only the FATAL lines the lesson tells the learner to look for are asserted exactly.
- **Skipping the failure paths.** About half the tests here are about what goes wrong.

## 8. Check yourself

1. Why does the runner test use a `FakeClock`? *So the test advances time by assignment and never sleeps.*
2. What does the `Held` executor model? *All senders busy: submitted work waits until released, so the runner must skip.*
3. Why does the infrastructure test start a local HTTP server? *The requester's job is to classify real HTTP answers (200, 204, 404, 500, 503) and failures (closed port, silent server).*
4. What does `test_a_stall_is_caught_up_by_sending_the_missed_requests` show? *After a one-second stall at 10 per second, 11 sends are due at once.*
5. Why is `monkeypatch` not needed in `test_wsgi.py`? *The warm-up delay is a setting, so the test passes 0.2 seconds directly.*

## 9. Further reading

- pytest monkeypatch: https://docs.pytest.org/en/stable/how-to/monkeypatch.html
- Capturing output: https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html
- Flask testing: https://flask.palletsprojects.com/en/stable/testing/
- Python `unittest.mock` background on fakes and stubs: https://docs.python.org/3.13/library/unittest.mock.html
