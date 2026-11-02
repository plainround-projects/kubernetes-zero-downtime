# Integration tests (the real server and the real load generator)

## 1. What it is

An **integration test** checks that two real parts work together, where a unit test would use a fake. Here the real `gunicorn` command (the one in the image, with `--preload`) is started as a subprocess and called over HTTP, and the real load generator program is started as a subprocess against a small local web server. They are marked `integration` and need neither Docker nor Kubernetes.

## 2. Why this repository uses it

Two facts of the lesson are only real in a real process. First, the one-second start-up delay: with `--preload` the port must refuse connections for the whole warm-up and then serve, which is exactly what a pod without a readiness probe is sent traffic into. Second, graceful shutdown: on `SIGTERM` requests already in flight must be answered, the process must exit with status 0 and the listener must be closed. And the load generator must send at its rate, count correctly and print its final counts when stopped.

## 3. Where it appears

| File | Role |
| --- | --- |
| `tests/integration/test_gunicorn.py` | The start-up delay and the graceful shutdown of the real server command. |
| `tests/integration/test_loadgen_cli.py` | The real load generator program against a local server. |
| `Makefile` | `make test-integration`; the CI job `integration`. |

## 4. How it works underneath

`subprocess.Popen` starts a program without waiting for it. `socket.connect_ex` tries to connect and returns 0 on success or an error number (non-zero) if the port refuses, which is a cheap way to ask "is anybody listening?". `process.send_signal(signal.SIGTERM)` sends the same signal Kubernetes sends. Threads (`threading.Thread`) let the test send eight requests at once.

A **free port** is found by binding port 0 (the operating system picks one). The environment variables `STARTUP_DELAY_SECONDS`, `LATENCY_MIN_MS` and so on configure the real program exactly as a Deployment would.

## 5. Walkthrough of this repository's use

### `tests/integration/test_gunicorn.py`

```text
"""The image's real server command as a subprocess: the startup delay and the graceful shutdown."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
```

The docstring: the image's real server command as a subprocess, the start-up delay and the graceful shutdown. Imports for processes, signals, sockets, threads and HTTP. `pytestmark` marks the file `integration`.

```text
SERVER = [
    sys.executable, "-m", "gunicorn", "--bind", "127.0.0.1:{port}", "--workers", "1", "--threads", "16",
    "--worker-class", "gthread", "--graceful-timeout", "10", "--preload", "zero_downtime.interfaces.wsgi:build_app()",
]  # fmt: skip


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start(port: int, **environment: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src"), **environment}
    return subprocess.Popen([part.format(port=port) for part in SERVER], env=env, stderr=subprocess.DEVNULL)


def _refuses(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) != 0
```

`SERVER` is the gunicorn command as a list with a `{port}` placeholder: one worker, 16 threads, the `gthread` class, a 10-second graceful timeout and `--preload`, with the factory `zero_downtime.interfaces.wsgi:build_app()`; `# fmt: skip` keeps the formatter from re-wrapping it. `_free_port()` asks the operating system for a free port. `_start(port, **environment)` builds the environment (the current one plus `PYTHONPATH` pointing at `src` and the given variables) and starts the command with `Popen`, hiding its error output. `_refuses(port)` tries to connect with a 0.5-second timeout and returns true if the connection fails.

```text
def _get(port: int, path: str = "/") -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as response:
        return int(response.status)


@pytest.fixture
def slow_server() -> Iterator[tuple[subprocess.Popen[bytes], int]]:
    port = _free_port()
    process = _start(port, STARTUP_DELAY_SECONDS="0.2", LATENCY_MIN_MS="400", LATENCY_MAX_MS="400", CPU_WORK_MS="0")
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _refuses(port):
            time.sleep(0.05)
        yield process, port
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
```

`_get(port, path)` fetches a URL and returns the status code. The `slow_server` fixture starts a server with a 0.2-second warm-up and a fixed 400 ms latency (min and max both 400, no CPU work), waits up to 15 seconds until the port stops refusing, yields the process and the port, and in `finally` kills the process if it is still running and waits for it.

```text
def test_the_port_refuses_connections_for_the_whole_startup_delay_then_serves():
    port = _free_port()
    started = time.monotonic()
    process = _start(port, STARTUP_DELAY_SECONDS="1.5", LATENCY_MIN_MS="1", LATENCY_MAX_MS="1", CPU_WORK_MS="0")
    try:
        time.sleep(0.5)
        assert _refuses(port), (
            "a pod in this window would be sent traffic it cannot take: that is the naive rollout's error"
        )
        while _refuses(port) and time.monotonic() - started < 20:
            time.sleep(0.05)
        assert time.monotonic() - started >= 1.5
        assert _get(port, "/healthz") == 200
    finally:
        process.terminate()
        process.wait(timeout=15)


def test_sigterm_lets_requests_in_flight_finish_and_then_exits_cleanly(slow_server):
    process, port = slow_server
    statuses: list[int] = []
```

`test_the_port_refuses_connections_for_the_whole_startup_delay_then_serves` starts a server with a 1.5-second warm-up, waits 0.5 seconds and asserts the port refuses (the message says why this matters: a pod in this window would be sent traffic it cannot take), then waits until the port opens, checks at least 1.5 seconds passed and that `/healthz` returns 200; `finally` terminates the process. The shutdown test begins: it takes the slow server and prepares a list for the eight statuses.

```text
    def request() -> None:
        statuses.append(_get(port))

    threads = [threading.Thread(target=request) for _ in range(8)]
    for thread in threads:
        thread.start()
    time.sleep(0.15)  # all eight are inside their 400 ms of work
    process.send_signal(signal.SIGTERM)
    for thread in threads:
        thread.join(timeout=15)
    assert statuses == [200] * 8, "requests already accepted must be answered, not cut off"
    assert process.wait(timeout=15) == 0
    assert _refuses(port), "and the listener is closed afterwards"
```

Eight threads each send a request; after 0.15 seconds (all eight are inside their 400 ms of work) the test sends `SIGTERM`, joins the threads and asserts every status is 200 (requests already accepted must be answered, not cut off), the process exits with status 0 and the listener is closed afterwards.

### `tests/integration/test_loadgen_cli.py`

```text
"""The load generator as the image runs it: a real process that sends, serves its control API and reports on SIGTERM."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
```

The docstring: the load generator as the image runs it, a real process that sends, serves its control API and reports on `SIGTERM`. Imports for JSON, processes, signals, sockets, threads and a test web server.

```text
class Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
```

`Ok` is a tiny web server handler that answers every GET with 200 and the body `ok`; `log_message` is silenced. `_free_port()` as before.

```text
def _program(**environment: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src"), **environment}
    return subprocess.Popen(
        [sys.executable, "-m", "loadgen.interfaces.cli"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
```

`_program(**environment)` starts `python -m loadgen.interfaces.cli` with `PYTHONPATH` pointing at `src` plus the given variables, capturing standard output as text.

```text
def test_it_sends_at_the_configured_rate_and_prints_the_final_counts_on_sigterm():
    target = ThreadingHTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    control = _free_port()
    process = _program(
        TARGET_URL=f"http://127.0.0.1:{target.server_port}/", RATE_PER_SECOND="50", CONTROL_PORT=str(control)
    )
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{control}/stats", timeout=2) as response:
                    json.loads(response.read())
                    break
            except OSError:
                time.sleep(0.1)
        time.sleep(1.5)
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=20)
    finally:
        process.kill()
        target.shutdown()
        target.server_close()
    final = json.loads(output.strip().splitlines()[-1])["final"]
    assert final["errors"] == 0
    assert 30 <= final["total"] <= 120, final
    assert process.returncode == 0
```

The main test starts the local target server, picks a control port and starts the program with a 50-per-second rate. It polls the control API's `/stats` for up to 10 seconds until it answers, waits 1.5 seconds, sends `SIGTERM` and reads the output (`communicate` waits for the program to exit). In `finally` it kills the process and shuts the target down. The last output line is parsed as JSON; the `final` counters must show no errors and a `total` between 30 and 120, and the exit status must be 0.

```text
def test_an_invalid_target_exits_with_status_one():
    process = _program(TARGET_URL="not-a-url")
    process.communicate(timeout=20)
    assert process.returncode == 1
```

An invalid `TARGET_URL` makes the program exit with status 1.


## 6. Try it yourself

```bash
make test-integration
```

Runs the integration tests (no Docker needed); about 10 seconds.

```bash
.venv/bin/python -m pytest tests/integration/test_gunicorn.py -q -k refuses
```

Runs only the start-up test: the port refuses connections for the 1.5-second warm-up, then serves.

```bash
.venv/bin/python -m pytest tests/integration/test_loadgen_cli.py -q
```

Runs the generator against a local server and checks the final JSON line.

## 7. Mistakes people make

- **A fixed port.** Two test runs would collide; a free port avoids it.
- **No cleanup on failure.** `try/finally` terminates or kills the child process.
- **Checking only that the server answers.** The test also checks it does not answer during warm-up.
- **Asserting exact request counts.** The generator test accepts a range (30 to 120 requests in about 1.5 seconds at 50 per second) because timing varies.
- **Killing instead of terminating.** `SIGKILL` cannot be handled; the shutdown test uses `SIGTERM`.

## 8. Check yourself

1. How does the test know the port is closed during warm-up? *`connect_ex` returns a non-zero error number.*
2. Why 400 ms latency and eight threads in the shutdown test? *All eight requests are inside their work when `SIGTERM` arrives, so they must be answered.*
3. What exit status proves a clean shutdown? *0.*
4. What does the generator print on `SIGTERM`? *One JSON line `{"final": {...}}` with the final counts.*
5. What exit status does an invalid target give? *1.*

## 9. Further reading

- Docker `run` reference: https://docs.docker.com/reference/cli/docker/container/run/
- pytest fixtures with yield: https://docs.pytest.org/en/stable/how-to/fixtures.html#yield-fixtures-recommended
- Python `subprocess.Popen`: https://docs.python.org/3.13/library/subprocess.html#popen-constructor
- Python `socket`: https://docs.python.org/3.13/library/socket.html
