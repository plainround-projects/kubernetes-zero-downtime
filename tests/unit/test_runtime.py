import time

from zero_downtime.infrastructure.runtime import SleepWaiter, SpinBurner, SystemRandomness


def test_seeded_randomness_is_repeatable_and_in_range():
    first, second = SystemRandomness(7), SystemRandomness(7)
    values = [first.fraction() for _ in range(5)]
    assert values == [second.fraction() for _ in range(5)]
    assert all(0 <= value < 1 for value in values)


def test_the_waiter_blocks_for_about_the_requested_time_and_ignores_zero():
    started = time.monotonic()
    SleepWaiter().wait(30)
    SleepWaiter().wait(0)
    assert 0.025 <= time.monotonic() - started < 0.5


def test_the_burner_loops_until_the_deadline_on_its_clock():
    reads: list[int] = []

    def clock():
        reads.append(len(reads))
        return len(reads) / 1000  # every read advances the clock by one millisecond

    SpinBurner(clock).burn(40)
    assert 40 <= len(reads) <= 43


def test_the_burner_with_the_real_clock_takes_at_least_the_requested_time():
    started = time.monotonic()
    SpinBurner().burn(30)
    assert time.monotonic() - started >= 0.03
