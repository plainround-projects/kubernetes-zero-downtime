import pytest

from loadgen.domain.errors import InvalidRateError
from loadgen.domain.outcome import Outcome, Sample
from loadgen.domain.pacer import Pacer
from loadgen.domain.stats import StatsWindow, percentile


def test_only_ok_is_not_an_error():
    assert [outcome.is_error for outcome in Outcome] == [False, True, True, True, True]


def test_percentile_uses_nearest_rank():
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    assert percentile(values, 0.5) == 0.5
    assert percentile(values, 0.99) == 1.0
    assert percentile([], 0.99) == 0.0


def test_an_empty_window_reads_as_zeros():
    snapshot = StatsWindow().snapshot()
    assert (snapshot.total, snapshot.errors, snapshot.p99_ms, snapshot.max_ms) == (0, 0, 0.0, 0.0)


def test_every_error_class_is_counted_on_its_own_and_summed():
    window = StatsWindow()
    for outcome in [Outcome.OK, Outcome.OK, Outcome.CONNECTION, Outcome.TIMEOUT, Outcome.HTTP_5XX, Outcome.HTTP_OTHER]:
        window.record(Sample(outcome, 0.1))
    snapshot = window.snapshot()
    assert (snapshot.total, snapshot.ok, snapshot.errors) == (6, 2, 4)
    assert (snapshot.connection, snapshot.timeout, snapshot.http_5xx, snapshot.http_other) == (1, 1, 1, 1)


def test_latency_percentiles_are_in_milliseconds():
    window = StatsWindow()
    for index in range(1, 101):
        window.record(Sample(Outcome.OK, index / 1000))
    snapshot = window.snapshot()
    assert snapshot.p50_ms == pytest.approx(50)
    assert snapshot.p99_ms == pytest.approx(99)
    assert snapshot.max_ms == pytest.approx(100)


def test_reset_returns_the_final_reading_and_starts_over():
    window = StatsWindow()
    window.record(Sample(Outcome.CONNECTION, 0.1))
    window.skip()
    final = window.reset()
    assert (final.errors, final.skipped) == (1, 1)
    assert window.snapshot().total == 0
    assert window.snapshot().skipped == 0


def test_the_dictionary_form_includes_the_error_total():
    window = StatsWindow()
    window.record(Sample(Outcome.HTTP_5XX, 0.2))
    assert window.snapshot().as_dict()["errors"] == 1


def test_the_pacer_makes_sends_due_at_a_fixed_interval():
    pacer = Pacer(10, start=0.0)
    assert pacer.due(0.0) == 1
    assert pacer.due(0.05) == 0
    assert pacer.due(0.1) == 1
    assert pacer.seconds_until_next(0.1) == pytest.approx(0.1)


def test_a_stall_is_caught_up_by_sending_the_missed_requests():
    pacer = Pacer(10, start=0.0)
    assert pacer.due(1.0) == 11


def test_changing_the_rate_neither_bursts_nor_leaves_a_gap():
    pacer = Pacer(10, start=0.0)
    assert pacer.due(0.0) == 1
    pacer.set_rate(100, now=0.0)
    assert pacer.due(0.05) == 5


@pytest.mark.parametrize("rate", [0, -5, 10_001])
def test_a_rate_outside_the_range_is_rejected(rate):
    with pytest.raises(InvalidRateError):
        Pacer(rate, 0.0)
    with pytest.raises(InvalidRateError):
        Pacer(10, 0.0).set_rate(rate, 0.0)
