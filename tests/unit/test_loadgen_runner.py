from loadgen.application.runner import LoadRunner
from loadgen.domain.outcome import Outcome, Sample


class FakeClock:
    def __init__(self):
        self.time = 0.0

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds


class Immediate:
    """Runs submitted work at once, on the caller's thread."""

    def submit(self, function):
        function()

    def shutdown(self):
        return None


class Held:
    """Keeps submitted work until released, to model senders that are all busy."""

    def __init__(self):
        self.pending = []

    def submit(self, function):
        self.pending.append(function)

    def release(self):
        for function in self.pending:
            function()
        self.pending.clear()

    def shutdown(self):
        self.release()


class ScriptedRequester:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self):
        self.calls += 1
        return Sample(self.outcomes.pop(0) if self.outcomes else Outcome.OK, 0.05)


def test_ticking_for_one_second_at_ten_per_second_sends_eleven_requests_counting_the_first():
    clock = FakeClock()
    requester = ScriptedRequester([])
    runner = LoadRunner(requester, clock, Immediate(), rate_per_second=10, max_in_flight=5)
    for _ in range(10):
        runner.tick()
        clock.time += 0.1
    runner.tick()
    assert requester.calls == 11
    assert runner.snapshot().total == 11


def test_errors_are_recorded_by_class():
    clock = FakeClock()
    runner = LoadRunner(
        ScriptedRequester([Outcome.OK, Outcome.CONNECTION, Outcome.HTTP_5XX]), clock, Immediate(), 10, 5
    )
    for _ in range(3):
        runner.tick()
        clock.time += 0.1
    snapshot = runner.snapshot()
    assert (snapshot.ok, snapshot.connection, snapshot.http_5xx, snapshot.errors) == (1, 1, 1, 2)


def test_when_every_sender_is_busy_the_request_is_skipped_not_queued():
    clock = FakeClock()
    held = Held()
    runner = LoadRunner(ScriptedRequester([]), clock, held, rate_per_second=10, max_in_flight=2)
    clock.time = 0.5
    runner.tick()  # six sends are due, two senders exist
    assert len(held.pending) == 2
    assert runner.snapshot().skipped == 4
    held.release()
    assert runner.snapshot().total == 2


def test_a_finished_request_frees_its_sender():
    clock = FakeClock()
    held = Held()
    runner = LoadRunner(ScriptedRequester([]), clock, held, rate_per_second=10, max_in_flight=1)
    runner.tick()
    held.release()
    clock.time = 0.1
    runner.tick()
    assert len(held.pending) == 1
    assert runner.snapshot().skipped == 0


def test_changing_the_rate_changes_the_pace():
    clock = FakeClock()
    requester = ScriptedRequester([])
    runner = LoadRunner(requester, clock, Immediate(), rate_per_second=10, max_in_flight=100)
    runner.tick()
    runner.set_rate(100)
    assert runner.rate == 100
    clock.time = 0.1
    runner.tick()
    assert requester.calls == 11


def test_reset_hands_back_the_window_and_stop_returns_the_final_reading():
    clock = FakeClock()
    runner = LoadRunner(ScriptedRequester([Outcome.TIMEOUT]), clock, Immediate(), 10, 5)
    runner.tick()
    assert runner.reset().timeout == 1
    assert runner.snapshot().total == 0
    runner.start()
    final = runner.stop()
    assert final.errors == 0


def test_a_stalled_pacer_thread_counts_its_overdue_requests_as_late_starts():
    clock = FakeClock()
    runner = LoadRunner(ScriptedRequester([]), clock, Immediate(), rate_per_second=10, max_in_flight=100)
    runner.tick()
    clock.time = 1.0  # the pacer thread was held up for a second
    runner.tick()
    assert runner.snapshot().late_starts == 10


def test_a_sender_that_starts_late_counts_a_late_start():
    clock = FakeClock()
    held = Held()
    runner = LoadRunner(ScriptedRequester([]), clock, held, rate_per_second=10, max_in_flight=5)
    runner.tick()
    clock.time = 0.5  # the sender threads were busy for half a second before running the request
    held.release()
    assert runner.snapshot().late_starts == 1


def test_a_prompt_sender_is_not_late():
    clock = FakeClock()
    runner = LoadRunner(ScriptedRequester([]), clock, Immediate(), rate_per_second=10, max_in_flight=5)
    for _ in range(5):
        runner.tick()
        clock.time += 0.1
    assert runner.snapshot().late_starts == 0
