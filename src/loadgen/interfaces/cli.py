"""The load generator program: send at a fixed rate, serve the control API, print the final counts on SIGTERM."""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
from types import FrameType

from loadgen.application.runner import LoadRunner
from loadgen.infrastructure.http_requester import HttpRequester
from loadgen.infrastructure.logging import configure_logging
from loadgen.infrastructure.runtime import PoolExecutor, SystemClock
from loadgen.infrastructure.settings import ConfigurationError, Settings
from loadgen.interfaces.control import make_server

log = logging.getLogger("loadgen")


def main() -> int:
    """Run until SIGTERM or SIGINT, then print the final counts as one JSON line and return the exit code."""
    try:
        settings = Settings.from_env(dict(os.environ))
    except ConfigurationError as error:
        configure_logging("INFO")
        log.critical(f"FATAL invalid configuration: {error}")
        return 1
    configure_logging(settings.log_level)
    runner = LoadRunner(
        HttpRequester(settings.target_url, settings.timeout_seconds),
        SystemClock(),
        PoolExecutor(settings.max_in_flight),
        settings.rate_per_second,
        settings.max_in_flight,
    )
    server = make_server(runner, settings.control_port)
    stopping = threading.Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    runner.start()
    threading.Thread(target=server.serve_forever, name="control", daemon=True).start()
    log.info(
        "sending",
        extra={"target": settings.target_url, "rps": settings.rate_per_second, "control_port": settings.control_port},
    )
    stopping.wait()
    server.shutdown()
    server.server_close()
    final = runner.stop()
    print(json.dumps({"final": final.as_dict()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
