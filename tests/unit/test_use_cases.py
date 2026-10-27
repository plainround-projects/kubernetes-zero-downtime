from zero_downtime.application.use_cases import HandleRequest, WarmUp
from zero_downtime.domain.profile import RequestProfile


class FixedRandomness:
    def __init__(self, value):
        self.value = value

    def fraction(self):
        return self.value


class Recorder:
    def __init__(self):
        self.burned, self.waited = [], []

    def burn(self, milliseconds):
        self.burned.append(milliseconds)

    def wait(self, milliseconds):
        self.waited.append(milliseconds)


def test_a_request_computes_for_cpu_ms_and_waits_for_the_rest():
    recorder = Recorder()
    handled = HandleRequest(RequestProfile(cpu_ms=2), FixedRandomness(0.5), recorder, recorder)()
    assert handled.latency_ms == 125.0
    assert recorder.burned == [2]
    assert recorder.waited == [123.0]


def test_the_slowest_request_takes_the_upper_bound():
    recorder = Recorder()
    assert HandleRequest(RequestProfile(), FixedRandomness(1.0), recorder, recorder)().latency_ms == 200.0


def test_warm_up_waits_out_the_delay_and_reports_it():
    recorder = Recorder()
    assert WarmUp(recorder, 1000.0)() == 1000.0
    assert recorder.waited == [1000.0]
