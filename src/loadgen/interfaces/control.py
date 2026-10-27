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
