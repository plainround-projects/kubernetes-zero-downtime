"""Composition root: validate the environment, run the warm-up, and build the WSGI app gunicorn serves.

gunicorn calls the factory (`zero_downtime.interfaces.wsgi:build_app()`) with `--preload`, so the warm-up runs
in the master process *before* gunicorn opens its listening socket. That is the lesson's one-second startup
delay made real: for that second the port refuses connections, which is exactly what a pod without a
readiness probe is sent traffic into.
"""

from __future__ import annotations

import logging
import os

from flask import Flask

from zero_downtime.application.use_cases import HandleRequest, WarmUp
from zero_downtime.infrastructure.logging import configure_logging
from zero_downtime.infrastructure.runtime import SleepWaiter, SpinBurner, SystemRandomness
from zero_downtime.infrastructure.settings import ConfigurationError, Settings
from zero_downtime.interfaces.http import create_app

log = logging.getLogger("zero_downtime")


def build_app(env: dict[str, str] | None = None) -> Flask:
    """Assemble the app; exit with status 1 after one FATAL line if the environment is invalid."""
    environment = dict(os.environ) if env is None else env
    try:
        settings = Settings.from_env(environment)
    except ConfigurationError as error:
        configure_logging("INFO")
        log.critical(f"FATAL invalid configuration: {error}")
        raise SystemExit(1) from error
    configure_logging(settings.log_level)
    waiter = SleepWaiter()
    warmed_ms = WarmUp(waiter, settings.startup_delay_seconds * 1000)()
    log.info("warm-up complete", extra={"warm_up_ms": warmed_ms})
    handle = HandleRequest(settings.profile, SystemRandomness(), SpinBurner(), waiter)
    return create_app(handle)
