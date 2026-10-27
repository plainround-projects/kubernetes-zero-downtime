"""How a request behaves: how long it takes and how much of that time is spent computing."""

from __future__ import annotations

from dataclasses import dataclass

from zero_downtime.domain.errors import InvalidProfileError


@dataclass(frozen=True, slots=True)
class RequestProfile:
    """Latency is drawn between the bounds; cpu_ms of it is real computation, the rest is waiting (I/O).

    The computation is what makes CPU utilization, and therefore the autoscaler, respond to load.
    """

    min_ms: float = 50.0
    max_ms: float = 200.0
    cpu_ms: float = 2.0

    def __post_init__(self) -> None:
        """Reject bounds that cannot describe a request."""
        if self.min_ms < 0 or self.max_ms < self.min_ms:
            raise InvalidProfileError(f"need 0 <= min_ms <= max_ms, got {self.min_ms} and {self.max_ms}")
        if not 0 <= self.cpu_ms <= self.min_ms:
            raise InvalidProfileError(f"cpu_ms must be between 0 and min_ms, got {self.cpu_ms}")

    def latency_ms(self, fraction: float) -> float:
        """Map a number in [0, 1] (from an injected random source) onto the latency range."""
        return self.min_ms + (self.max_ms - self.min_ms) * fraction

    def split(self, latency_ms: float) -> tuple[float, float]:
        """Split a latency into (computing ms, waiting ms)."""
        return self.cpu_ms, latency_ms - self.cpu_ms
