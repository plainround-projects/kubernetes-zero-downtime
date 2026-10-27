import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loadgen.application.runner import LoadRunner
from loadgen.domain.outcome import Outcome
from loadgen.infrastructure.http_requester import HttpRequester
from loadgen.infrastructure.runtime import PoolExecutor, SystemClock
from loadgen.infrastructure.settings import ConfigurationError, Settings
from loadgen.interfaces.control import make_server


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = int(self.path.strip("/") or 200)
        self.send_response(status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


@pytest.fixture
def target():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    ("path", "outcome"),
    [
        ("/200", Outcome.OK),
        ("/204", Outcome.OK),
        ("/500", Outcome.HTTP_5XX),
        ("/503", Outcome.HTTP_5XX),
        ("/404", Outcome.HTTP_OTHER),
    ],
)
def test_the_requester_classifies_status_codes(target, path, outcome):
    assert HttpRequester(target + path, 2).request().outcome is outcome


def test_a_closed_port_is_a_connection_error():
    assert HttpRequester("http://127.0.0.1:1/", 1).request().outcome is Outcome.CONNECTION


def test_a_server_that_never_answers_is_a_timeout():
    import socket

    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        port = silent.getsockname()[1]
        assert HttpRequester(f"http://127.0.0.1:{port}/", 0.3).request().outcome is Outcome.TIMEOUT


def test_a_non_http_target_is_refused():
    with pytest.raises(ValueError, match="TARGET_URL"):
        HttpRequester("https://example.test/", 1)


def test_settings_require_a_target_and_validate_the_rest():
    assert Settings.from_env({"TARGET_URL": "http://web"}).rate_per_second == 100.0
    for env in ({}, {"TARGET_URL": "ftp://x"}, {"TARGET_URL": "http://web", "RATE_PER_SECOND": "0"},
                {"TARGET_URL": "http://web", "TIMEOUT_SECONDS": "x"}, {"TARGET_URL": "http://web", "LOG_LEVEL": "LOUD"},
                {"TARGET_URL": "http://web", "CONTROL_PORT": "0"}):  # fmt: skip
        with pytest.raises(ConfigurationError):
            Settings.from_env(env)


def _call(url, method="GET", body=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_control_api_reads_resets_and_changes_the_rate(target):
    runner = LoadRunner(HttpRequester(target + "/200", 2), SystemClock(), PoolExecutor(8), 50, 8)
    server = make_server(runner, 0, "127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    runner.start()
    try:
        assert _call(base + "/healthz") == (200, {"status": "alive"})
        assert _call(base + "/rate", "POST", {"rps": 80}) == (200, {"rps": 80.0})
        assert _call(base + "/rate", "POST", {"rps": 0})[0] == 400
        assert _call(base + "/rate", "POST", {"nope": 1})[0] == 400
        assert _call(base + "/nothing")[0] == 404
        assert _call(base + "/nothing", "POST", {})[0] == 404
        threading.Event().wait(0.5)
        status, reset = _call(base + "/reset", "POST", {})
        assert status == 200
        assert reset["total"] > 5
        assert reset["errors"] == 0
        assert _call(base + "/stats")[1]["total"] < reset["total"]
    finally:
        runner.stop()
        server.shutdown()
        server.server_close()
