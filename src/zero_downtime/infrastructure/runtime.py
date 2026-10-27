"""Real clocks and random numbers behind the ports."""

from __future__ import annotations

import random
import time
from collections.abc import Callable


class SystemRandomness:
    """Uniform random numbers from the standard library's generator."""

    def __init__(self, seed: int | None = None) -> None:
        """Seed the generator; leave seed unset in production."""
        self._random = random.Random(seed)

    def fraction(self) -> float:
        """Return the next number in [0, 1)."""
        return self._random.random()


class SleepWaiter:
    """Wait by sleeping, which uses no CPU."""

    def wait(self, milliseconds: float) -> None:
        """Sleep for the given time."""
        if milliseconds > 0:
            time.sleep(milliseconds / 1000)


class SpinBurner:
    """Burn CPU by hashing in a loop until a deadline on a monotonic clock."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        """Take the clock as a parameter so a test can drive it without burning real time."""
        self._clock = clock

    def burn(self, milliseconds: float) -> None:
        """Compute for the given time."""
        deadline = self._clock() + milliseconds / 1000
        value = 0
        while self._clock() < deadline:
            value = (value * 31 + 7) % 1_000_003
