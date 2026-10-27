import pytest

from zero_downtime.domain.errors import InvalidProfileError
from zero_downtime.domain.profile import RequestProfile


def test_the_default_profile_is_fifty_to_two_hundred_milliseconds():
    profile = RequestProfile()
    assert (profile.min_ms, profile.max_ms) == (50.0, 200.0)


@pytest.mark.parametrize(("fraction", "expected"), [(0.0, 50.0), (0.5, 125.0), (1.0, 200.0)])
def test_latency_maps_the_random_fraction_onto_the_range(fraction, expected):
    assert RequestProfile().latency_ms(fraction) == pytest.approx(expected)


def test_a_latency_splits_into_computing_and_waiting():
    assert RequestProfile(cpu_ms=3).split(100.0) == (3, 97.0)


@pytest.mark.parametrize(
    "bad",
    [
        {"min_ms": -1},
        {"min_ms": 100, "max_ms": 50},
        {"cpu_ms": -1},
        {"min_ms": 10, "max_ms": 20, "cpu_ms": 11},
    ],
)
def test_impossible_bounds_are_rejected(bad):
    with pytest.raises(InvalidProfileError):
        RequestProfile(**bad)
