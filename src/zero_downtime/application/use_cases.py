"""The service's behavior: do the work of one request, and warm up once before accepting traffic."""

from __future__ import annotations

from dataclasses import dataclass

from zero_downtime.application.ports import Burner, Randomness, Waiter
from zero_downtime.domain.profile import RequestProfile


@dataclass(frozen=True, slots=True)
class HandledRequest:
    """What one request cost, returned so callers and tests can see it."""

    latency_ms: float
    cpu_ms: float


class HandleRequest:
    """Spend a random latency inside the profile: compute for part of it, wait for the rest."""

    def __init__(self, profile: RequestProfile, randomness: Randomness, burner: Burner, waiter: Waiter) -> None:
        """Bind the profile and the three injected capabilities."""
        self._profile = profile
        self._randomness = randomness
        self._burner = burner
        self._waiter = waiter

    def __call__(self) -> HandledRequest:
        """Do one request's work."""
        latency = self._profile.latency_ms(self._randomness.fraction())
        computing, waiting = self._profile.split(latency)
        self._burner.burn(computing)
        self._waiter.wait(waiting)
        return HandledRequest(latency_ms=latency, cpu_ms=computing)


class WarmUp:
    """The startup delay: the service is not ready, and must not be listening, until it has run."""

    def __init__(self, waiter: Waiter, delay_ms: float) -> None:
        """Bind the waiter and the delay."""
        self._waiter = waiter
        self._delay_ms = delay_ms

    def __call__(self) -> float:
        """Wait out the delay and return how long it was, in milliseconds."""
        self._waiter.wait(self._delay_ms)
        return self._delay_ms
