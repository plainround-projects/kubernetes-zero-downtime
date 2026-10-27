"""Configuration from the environment, validated once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from loadgen.domain.errors import InvalidRateError
from loadgen.domain.pacer import Pacer

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class ConfigurationError(Exception):
    """An environment variable is missing or malformed. The message names the variable."""


@dataclass(frozen=True, slots=True)
class Settings:
    """What the load generator reads from its environment."""

    target_url: str
    rate_per_second: float = 100.0
    timeout_seconds: float = 2.0
    max_in_flight: int = 128
    control_port: int = 9100
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Build settings from a mapping such as os.environ; TARGET_URL is required."""
        target = env.get("TARGET_URL", "").strip()
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            raise ConfigurationError(f"TARGET_URL must be an http:// URL with a host, got {target!r}")
        log_level = env.get("LOG_LEVEL", "INFO").upper()
        if log_level not in LOG_LEVELS:
            raise ConfigurationError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {log_level!r}")
        rate = _number(env, "RATE_PER_SECOND", 100.0)
        try:
            Pacer(rate, 0.0)
        except InvalidRateError as error:
            raise ConfigurationError(f"RATE_PER_SECOND: {error}") from error
        return cls(
            target_url=target,
            rate_per_second=rate,
            timeout_seconds=_bounded(_number(env, "TIMEOUT_SECONDS", 2.0), "TIMEOUT_SECONDS", 0.1, 60),
            max_in_flight=int(_bounded(_number(env, "MAX_IN_FLIGHT", 128), "MAX_IN_FLIGHT", 1, 10_000)),
            control_port=int(_bounded(_number(env, "CONTROL_PORT", 9100), "CONTROL_PORT", 1, 65_535)),
            log_level=log_level,
        )


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from None


def _bounded(value: float, name: str, low: float, high: float) -> float:
    if not low <= value <= high:
        raise ConfigurationError(f"{name} must be between {low} and {high}, got {value}")
    return value
