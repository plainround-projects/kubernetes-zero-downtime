"""What can happen to one request, and the measured sample the counters are built from."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    """The class of one finished request. Everything except OK is an error and is counted on its own."""

    OK = "ok"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    HTTP_5XX = "http_5xx"
    HTTP_OTHER = "http_other"

    @property
    def is_error(self) -> bool:
        """True for every outcome except OK."""
        return self is not Outcome.OK


@dataclass(frozen=True, slots=True)
class Sample:
    """One request's outcome and how long it took, in seconds."""

    outcome: Outcome
    latency_seconds: float
