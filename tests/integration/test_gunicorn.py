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
