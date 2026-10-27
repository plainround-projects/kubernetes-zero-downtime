"""Real clock and a thread pool behind the runner's ports."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor


class SystemClock:
    """The monotonic clock and time.sleep."""

    def now(self) -> float:
        """Seconds on the monotonic clock."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Block for the given time."""
        time.sleep(seconds)


class PoolExecutor:
    """A fixed thread pool."""

    def __init__(self, workers: int) -> None:
        """Create the pool."""
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sender")

    def submit(self, function: Callable[[], None]) -> Future[None]:
        """Schedule the function on a pool thread."""
        return self._pool.submit(function)

    def shutdown(self) -> None:
        """Wait for scheduled work and release the threads."""
        self._pool.shutdown(wait=True)
