"""The load runner: sends requests at a fixed rate and records every outcome."""

from __future__ import annotations

import threading

from loadgen.application.ports import Clock, Executor, Requester
from loadgen.domain.pacer import Pacer
from loadgen.domain.stats import Snapshot, StatsWindow


class LoadRunner:
    """Drives a Pacer on one thread and hands each due request to an Executor.

    At most `max_in_flight` requests run at once. A request that is due when all senders are busy is not
    queued (queuing would hide an overloaded generator as extra latency); it is counted as `skipped`, and a
    verdict is only valid when `skipped` is zero.
    """

    def __init__(
        self, requester: Requester, clock: Clock, executor: Executor, rate_per_second: float, max_in_flight: int
    ) -> None:
        """Bind the collaborators; nothing is sent until start()."""
        self._requester = requester
        self._clock = clock
        self._executor = executor
        self._max_in_flight = max_in_flight
        self._window = StatsWindow()
        self._pacer = Pacer(rate_per_second, clock.now())
        self._rate = rate_per_second
        self._lock = threading.Lock()
        self._in_flight = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def rate(self) -> float:
        """The current target rate in requests per second."""
        with self._lock:
            return self._rate

    def start(self) -> None:
        """Begin sending on a background thread."""
        self._thread = threading.Thread(target=self._loop, name="pacer", daemon=True)
        self._thread.start()

    def stop(self) -> Snapshot:
        """Stop sending, wait for requests in flight, and return the window's final reading."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._executor.shutdown()
        return self._window.reset()

    def set_rate(self, rate_per_second: float) -> None:
        """Change the rate without a burst or a gap."""
        with self._lock:
            self._pacer.set_rate(rate_per_second, self._clock.now())
            self._rate = rate_per_second

    def snapshot(self) -> Snapshot:
        """Read the current window."""
        return self._window.snapshot()

    def reset(self) -> Snapshot:
        """Return the current window's reading and start a new one."""
        return self._window.reset()

    def tick(self) -> float:
        """Send everything that is due now and return how long to wait for the next one."""
        with self._lock:
            now = self._clock.now()
            due = self._pacer.due(now)
            wait = self._pacer.seconds_until_next(now)
        for _ in range(due):
            self._send_one()
        return wait

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._clock.sleep(min(self.tick(), 0.05))

    def _send_one(self) -> None:
        with self._lock:
            if self._in_flight >= self._max_in_flight:
                self._window.skip()
                return
            self._in_flight += 1
        self._executor.submit(self._run_request)

    def _run_request(self) -> None:
        try:
            self._window.record(self._requester.request())
        finally:
            with self._lock:
                self._in_flight -= 1
