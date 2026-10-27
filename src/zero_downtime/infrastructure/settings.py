"""Configuration from the environment (twelve-factor), validated once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from zero_downtime.domain.errors import InvalidProfileError
from zero_downtime.domain.profile import RequestProfile

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class ConfigurationError(Exception):
    """An environment variable is missing or malformed. The message names the variable."""


@dataclass(frozen=True, slots=True)
class Settings:
    """What the service reads from its environment."""

    startup_delay_seconds: float = 1.0
    profile: RequestProfile = field(default_factory=RequestProfile)
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Build settings from a mapping such as os.environ, applying defaults and validating each value."""
        log_level = env.get("LOG_LEVEL", "INFO").upper()
        if log_level not in LOG_LEVELS:
            raise ConfigurationError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {log_level!r}")
        defaults = RequestProfile()
        try:
            profile = RequestProfile(
                min_ms=_number(env, "LATENCY_MIN_MS", defaults.min_ms, 0, 60_000),
                max_ms=_number(env, "LATENCY_MAX_MS", defaults.max_ms, 0, 60_000),
                cpu_ms=_number(env, "CPU_WORK_MS", defaults.cpu_ms, 0, 60_000),
            )
        except InvalidProfileError as error:
            raise ConfigurationError(f"LATENCY_MIN_MS, LATENCY_MAX_MS or CPU_WORK_MS: {error}") from error
        return cls(
            startup_delay_seconds=_number(env, "STARTUP_DELAY_SECONDS", 1.0, 0, 600),
            profile=profile,
            log_level=log_level,
        )


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from None
    if not low <= value <= high:
        raise ConfigurationError(f"{name} must be between {low} and {high}, got {value}")
    return value
