"""A fixed-rate schedule: how many requests are due now, regardless of how long earlier ones took."""

from __future__ import annotations

from loadgen.domain.errors import InvalidRateError


class Pacer:
    """Open-loop pacing. Sends are due every 1/rate seconds from a start time, so a slow response never
    delays the next send, and a stall in the sender is caught up by sending the missed ones together.
    """

    def __init__(self, rate_per_second: float, start: float) -> None:
        """Start the schedule at `start` (a monotonic clock reading)."""
        self._check(rate_per_second)
        self._interval = 1.0 / rate_per_second
        self._next_due = start

    @staticmethod
    def _check(rate_per_second: float) -> None:
        if not 0 < rate_per_second <= 10_000:
            raise InvalidRateError(f"rate must be between 0 and 10000 requests per second, got {rate_per_second}")

    def lateness(self, now: float) -> float:
        """How many seconds the oldest unsent request is overdue at `now` (0.0 when none is due)."""
        return max(0.0, now - self._next_due)

    def due(self, now: float) -> int:
        """How many sends are due at `now`; advances the schedule past them."""
        count = 0
        while self._next_due <= now:
            self._next_due += self._interval
            count += 1
        return count

    def seconds_until_next(self, now: float) -> float:
        """How long to wait before the next send is due (never negative)."""
        return max(0.0, self._next_due - now)

    def set_rate(self, rate_per_second: float, now: float) -> None:
        """Change the rate from `now` on, without a burst or a gap.

        The next send is due at the earlier of the old schedule and one new interval from now, so raising
        the rate takes effect at once and lowering it does not delay a send that was already due.
        """
        self._check(rate_per_second)
        self._interval = 1.0 / rate_per_second
        self._next_due = max(now, min(self._next_due, now + self._interval))
