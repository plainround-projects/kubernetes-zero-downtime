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

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify(status="alive")

    @app.get("/readyz")
    def readyz() -> Response:
        return jsonify(status="ready")

    return app
