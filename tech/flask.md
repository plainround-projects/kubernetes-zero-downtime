# Flask

## 1. What it is

Flask is a **web framework** for Python: a library that lets a program answer web requests. A web request is a message a client (a browser, `curl`, another program) sends over the network using the **HTTP** protocol: it names a **method** (`GET` to read something) and a **path** (`/healthz`). The program replies with a **status code** (200 means success, 404 not found, 503 service unavailable) and usually a body. Flask matches the path of each request to a Python function you wrote (a **route**) and turns what the function returns into the reply. This repository uses Flask 3.1.

## 2. Why this repository uses it

The service under test needs three HTTP endpoints: one that does a request's worth of work (a random latency, part of it real computation), one for the "am I alive" probe and one for the "can I serve traffic" probe. Flask is the smallest well-known way to write that. Here the readiness route is deliberately trivial: the application object only exists once the start-up delay is over, so if it can answer at all it is ready.

## 3. Where it appears

| File | Role |
| --- | --- |
| `src/zero_downtime/interfaces/http.py` | Defines the app and its three routes. |
| `src/zero_downtime/interfaces/wsgi.py` | Builds the app for the server (see `gunicorn.md`). |
| `tests/unit/test_http.py` | Calls the routes with Flask's test client, without a network. |
| `pyproject.toml`, `requirements.lock` | Pin Flask and its exact version. |

## 4. How it works underneath

A Flask application is a Python object (`Flask(__name__)`). It does not listen on the network itself. It follows **WSGI**, a standard that describes how a web server and a Python application talk: the server hands the application a description of the request, the application hands back the status, headers and body. That is why a separate server (gunicorn) is used in production. In tests there is no server: Flask offers a **test client** that builds the request description in memory and calls the application directly.

A route is registered with a **decorator**, a line starting with `@` above a function. `@app.get("/healthz")` tells the app "when a `GET` request for `/healthz` arrives, call this function". Whatever the function returns becomes the reply. `jsonify(...)` builds a reply whose body is JSON text and whose `Content-Type` header says so. Returning a tuple `(reply, 503)` sets the status code.

Each request is handled by a function call; between requests the process keeps its memory. The app here keeps no counter in memory: all state lives in Redis, so many copies of the process can run at once and agree.

## 5. Walkthrough of this repository's use

### `src/zero_downtime/interfaces/http.py`

```text
"""The HTTP surface: GET / does one request's work, GET /healthz is liveness, GET /readyz is readiness."""

from __future__ import annotations

import socket

from flask import Flask, Response, jsonify

from zero_downtime.application.use_cases import HandleRequest


def create_app(handle_request: HandleRequest) -> Flask:
    """Build the Flask app around the use case. The app only exists once warm-up is over, so it is always ready."""
    app = Flask(__name__)
    pod = socket.gethostname()

    @app.get("/")
    def work() -> Response:
        handled = handle_request()
        return jsonify(status="ok", pod=pod, latency_ms=round(handled.latency_ms, 1))
```

The docstring lists the three routes. Imports: `socket`; from Flask the application class, the `Response` type and `jsonify` (builds a JSON reply); and the `HandleRequest` use case. `create_app(handle_request)` is a **factory**: it receives the use case, so it never builds clocks or random sources itself. Its docstring notes the key idea: the app only exists once warm-up is over, so it is always ready. `app = Flask(__name__)` creates the application and `pod = socket.gethostname()` stores the host name once; in Kubernetes the host name of a container is the pod's name, so every answer shows which pod served it. `@app.get("/")` registers `work()`: it calls the use case (which burns CPU and sleeps for the random latency) and returns `jsonify(status="ok", pod=pod, latency_ms=round(..., 1))`.

```text
    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify(status="alive")

    @app.get("/readyz")
    def readyz() -> Response:
        return jsonify(status="ready")

    return app
```

`@app.get("/healthz")` is liveness and answers `{"status": "alive"}`; `@app.get("/readyz")` is readiness and answers `{"status": "ready"}`. `return app` hands the finished application back to the caller (the start-up code or a test).


## 6. Try it yourself

```bash
.venv/bin/python - <<'PY'
from zero_downtime.application.use_cases import HandleRequest
from zero_downtime.domain.profile import RequestProfile
from zero_downtime.interfaces.http import create_app
class Rand:
    def fraction(self): return 0.5
class Nothing:
    def burn(self, ms): pass
    def wait(self, ms): pass
app = create_app(HandleRequest(RequestProfile(), Rand(), Nothing(), Nothing()))
client = app.test_client()
print(client.get("/").get_json(), client.get("/healthz").get_json(), client.get("/readyz").get_json())
PY
```

Prints `{'latency_ms': 125.0, 'pod': '<host name>', 'status': 'ok'} {'status': 'alive'} {'status': 'ready'}`. No server was involved and no time was spent.

```bash
.venv/bin/python -m pytest tests/unit/test_http.py -q
```

The route tests pass.

```bash
make up && kubectl -n zero-downtime port-forward svc/web 18080:80 &
curl -s localhost:18080/ ; kill %1
```

Prints one answer such as `{"latency_ms":132.6,"pod":"web-6f9c...","status":"ok"}` after a pause of the random latency.

## 7. Mistakes people make

- **Using Flask's built-in development server in production.** This repository serves the app with gunicorn (`gunicorn.md`).
- **A readiness route that does not mean anything.** Here it means "start-up is complete" because the app is built only after warm-up; without that, readiness would answer before the service was truly ready.
- **Making the health check depend on other services.** `/healthz` and `/readyz` call nothing else.
- **Starting the app on import.** `create_app` is a function; nothing starts until it is called.

## 8. Check yourself

1. What does `@app.get("/")` do? *It registers the function below it as the handler for `GET` requests to `/`.*
2. Why is `/readyz` always "ready"? *The app is only built after the start-up delay, so an app that answers is ready.*
3. Why is there no `app.run()` anywhere? *gunicorn runs the app; Flask only describes the routes.*
4. What does the answer to `GET /` contain? *`status`, the serving `pod` and the `latency_ms` of that request.*
5. What is the test client for? *Calling routes in memory, with no network and no server.*

## 9. Further reading

- Flask 3.1 documentation: https://flask.palletsprojects.com/en/stable/
- Quickstart: https://flask.palletsprojects.com/en/stable/quickstart/
- Testing: https://flask.palletsprojects.com/en/stable/testing/
- WSGI: https://wsgi.readthedocs.io/en/latest/
