import time

import pytest

from zero_downtime.interfaces.wsgi import build_app


def test_warm_up_delays_the_return_of_the_app():
    started = time.monotonic()
    app = build_app({"STARTUP_DELAY_SECONDS": "0.2", "LATENCY_MIN_MS": "0", "LATENCY_MAX_MS": "0", "CPU_WORK_MS": "0"})
    assert time.monotonic() - started >= 0.2
    assert app.test_client().get("/healthz").status_code == 200


def test_a_bad_configuration_exits_naming_the_variable(capfd):
    with pytest.raises(SystemExit) as exit_info:
        build_app({"CPU_WORK_MS": "oops"})
    assert exit_info.value.code == 1
    assert "CPU_WORK_MS" in capfd.readouterr().err
