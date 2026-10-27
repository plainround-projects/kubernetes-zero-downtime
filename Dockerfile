# Two images from one file:
#   docker build --target service  -t zero-downtime-web:0.1.0 .
#   docker build --target loadgen  -t zero-downtime-loadgen:0.1.0 .
# Both run as a non-root user with nothing but the interpreter and the code they need. The service is built
# in two stages so the final image holds the virtual environment and no pip or compiler; the load generator
# uses only the standard library, so its image is the interpreter plus its own package.

FROM python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530 AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

FROM python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530 AS service
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
COPY --from=builder /opt/venv /opt/venv
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
# --preload runs the app factory, and so the startup delay, in the master process before gunicorn opens its
# socket: for that second the port refuses connections. SIGTERM makes gunicorn stop accepting and finish the
# requests in flight within --graceful-timeout. /tmp is the only writable path (an emptyDir in the cluster).
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "16", "--worker-class", "gthread", \
     "--worker-tmp-dir", "/tmp", "--graceful-timeout", "10", "--timeout", "30", "--preload", \
     "--access-logfile", "-", "zero_downtime.interfaces.wsgi:build_app()"]

FROM python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530 AS loadgen
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
COPY src/loadgen /app/loadgen
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001:10001
EXPOSE 9100
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9100/healthz', timeout=2)"]
CMD ["python", "-m", "loadgen.interfaces.cli"]
