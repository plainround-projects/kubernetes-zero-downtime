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
