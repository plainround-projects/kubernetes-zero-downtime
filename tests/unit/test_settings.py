import pytest

from zero_downtime.infrastructure.settings import ConfigurationError, Settings


def test_defaults_are_the_lessons_service():
    settings = Settings.from_env({})
    assert settings.startup_delay_seconds == 1.0
    assert (settings.profile.min_ms, settings.profile.max_ms) == (50.0, 200.0)


def test_values_come_from_the_environment():
    settings = Settings.from_env(
        {
            "STARTUP_DELAY_SECONDS": "0.5",
            "LATENCY_MIN_MS": "10",
            "LATENCY_MAX_MS": "20",
            "CPU_WORK_MS": "1",
            "LOG_LEVEL": "warning",
        }
    )
    assert settings.startup_delay_seconds == 0.5
    assert (settings.profile.min_ms, settings.profile.max_ms, settings.profile.cpu_ms) == (10, 20, 1)
    assert settings.log_level == "WARNING"


@pytest.mark.parametrize(
    ("env", "variable"),
    [
        ({"STARTUP_DELAY_SECONDS": "x"}, "STARTUP_DELAY_SECONDS"),
        ({"STARTUP_DELAY_SECONDS": "-1"}, "STARTUP_DELAY_SECONDS"),
        ({"LATENCY_MIN_MS": "300"}, "LATENCY_MIN_MS"),
        ({"CPU_WORK_MS": "100"}, "CPU_WORK_MS"),
        ({"LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
    ],
)
def test_a_bad_value_names_the_variable(env, variable):
    with pytest.raises(ConfigurationError, match=variable):
        Settings.from_env(env)
