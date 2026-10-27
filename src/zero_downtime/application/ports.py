"""What the use cases need from outside the domain."""

from __future__ import annotations

from typing import Protocol


class Randomness(Protocol):
    """A source of numbers in [0, 1); injected so tests are deterministic."""

    def fraction(self) -> float:
        """Return the next number in [0, 1)."""
        ...


class Waiter(Protocol):
    """Wait without computing (a sleep)."""

    def wait(self, milliseconds: float) -> None:
        """Block for the given time."""
        ...


class Burner(Protocol):
    """Compute without waiting (a busy loop)."""

    def burn(self, milliseconds: float) -> None:
        """Keep one CPU busy for the given time."""
        ...
