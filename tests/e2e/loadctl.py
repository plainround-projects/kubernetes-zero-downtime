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
