"""What the runner needs from outside the domain."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from loadgen.domain.outcome import Sample


class Requester(Protocol):
    """Sends one request to the target and reports how it went. Never raises."""

    def request(self) -> Sample:
        """Send one request."""
        ...


class Clock(Protocol):
    """A monotonic clock and a sleep, injected so the runner is testable without real time."""

    def now(self) -> float:
        """Seconds on a monotonic clock."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for the given time."""
        ...


class Executor(Protocol):
    """Runs a function on another thread; a thread pool in production, an immediate call in tests."""

    def submit(self, function: Callable[[], None]) -> object:
        """Schedule the function."""
        ...

    def shutdown(self) -> None:
        """Wait for scheduled functions and release threads."""
        ...
