"""Counters for a measurement window: every error by class, and latency percentiles."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from loadgen.domain.outcome import Outcome, Sample


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending list; 0.0 for an empty list."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An immutable reading of a window."""

    total: int
    ok: int
    connection: int
    timeout: int
    http_5xx: int
    http_other: int
    skipped: int
    p50_ms: float
    p99_ms: float
    max_ms: float

    @property
    def errors(self) -> int:
        """Every failed request: connection errors, timeouts, 5xx and any other non-2xx status."""
        return self.connection + self.timeout + self.http_5xx + self.http_other

    def as_dict(self) -> dict[str, float | int]:
        """The snapshot as plain JSON-ready values, with the error total included."""
        return {
            "total": self.total,
            "ok": self.ok,
            "errors": self.errors,
            "connection": self.connection,
            "timeout": self.timeout,
            "http_5xx": self.http_5xx,
            "http_other": self.http_other,
            "skipped": self.skipped,
            "p50_ms": round(self.p50_ms, 1),
            "p99_ms": round(self.p99_ms, 1),
            "max_ms": round(self.max_ms, 1),
        }


class StatsWindow:
    """Thread-safe accumulator. `skipped` counts requests the generator itself could not send in time."""

    def __init__(self) -> None:
        """Start an empty window."""
        self._lock = threading.Lock()
        self._counts = dict.fromkeys(Outcome, 0)
        self._latencies: list[float] = []
        self._skipped = 0

    def record(self, sample: Sample) -> None:
        """Count one finished request."""
        with self._lock:
            self._counts[sample.outcome] += 1
            self._latencies.append(sample.latency_seconds)

    def skip(self) -> None:
        """Count one request that was due but not sent because every sender was busy."""
        with self._lock:
            self._skipped += 1

    def snapshot(self) -> Snapshot:
        """Read the window without changing it."""
        with self._lock:
            return self._build()

    def reset(self) -> Snapshot:
        """Return the window's final reading and start a new, empty one."""
        with self._lock:
            final = self._build()
            self._counts = dict.fromkeys(Outcome, 0)
            self._latencies = []
            self._skipped = 0
            return final

    def _build(self) -> Snapshot:
        ordered = sorted(self._latencies)
        return Snapshot(
            total=sum(self._counts.values()),
            ok=self._counts[Outcome.OK],
            connection=self._counts[Outcome.CONNECTION],
            timeout=self._counts[Outcome.TIMEOUT],
            http_5xx=self._counts[Outcome.HTTP_5XX],
            http_other=self._counts[Outcome.HTTP_OTHER],
            skipped=self._skipped,
            p50_ms=percentile(ordered, 0.50) * 1000,
            p99_ms=percentile(ordered, 0.99) * 1000,
            max_ms=(ordered[-1] * 1000) if ordered else 0.0,
        )
