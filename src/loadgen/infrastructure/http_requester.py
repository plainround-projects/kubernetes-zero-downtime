"""The Requester adapter: one HTTP GET on a new connection, classified into an Outcome."""

from __future__ import annotations

import http.client
import time
from urllib.parse import urlsplit

from loadgen.domain.outcome import Outcome, Sample


class HttpRequester:
    """Opens a new connection per request on purpose: each request then meets whatever the Service routes it
    to, which is what exposes a pod that is not ready or is going away. It never raises; failures are samples.
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

    def request(self) -> Sample:
        """Send one request and classify what happened."""
        started = time.monotonic()
        outcome = self._attempt()
        return Sample(outcome, time.monotonic() - started)

    def _attempt(self) -> Outcome:
        connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            connection.request("GET", self._path, headers={"Connection": "close"})
            response = connection.getresponse()
            response.read()
            return self._classify(response.status)
        except TimeoutError:
            return Outcome.TIMEOUT
        except (OSError, http.client.HTTPException):
            return Outcome.CONNECTION
        finally:
            connection.close()

    @staticmethod
    def _classify(status: int) -> Outcome:
        if 200 <= status < 300:
            return Outcome.OK
        return Outcome.HTTP_5XX if status >= 500 else Outcome.HTTP_OTHER
