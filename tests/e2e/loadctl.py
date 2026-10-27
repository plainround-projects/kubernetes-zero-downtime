"""A client for the in-cluster load generator's control API, reached through one port-forward."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from tests.e2e.kube import Json, Kube, port_forward


class LoadControl:
    """Read the error counters, reset the window, change the request rate."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def _call(self, path: str, method: str = "GET", body: Json | None = None) -> Json:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, method=method, data=data)
        with urllib.request.urlopen(request, timeout=10) as response:
            parsed: Json = json.loads(response.read())
            return parsed

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
    with port_forward(kube, "svc/loadgen", 9100) as base:
        yield LoadControl(base)
