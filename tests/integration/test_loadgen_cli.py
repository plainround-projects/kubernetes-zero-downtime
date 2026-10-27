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


def _program(**environment: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src"), **environment}
    return subprocess.Popen(
        [sys.executable, "-m", "loadgen.interfaces.cli"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


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


def test_an_invalid_target_exits_with_status_one():
    process = _program(TARGET_URL="not-a-url")
    process.communicate(timeout=20)
    assert process.returncode == 1
