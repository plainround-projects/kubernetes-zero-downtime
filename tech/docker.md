# Docker (Dockerfile and .dockerignore)

## 1. What it is

A **container** is a way to run a program in a box: the program sees its own file system, its own process list and its own network, although it shares the computer's operating-system core (the **kernel**) with everything else. An **image** is the frozen, read-only content of that box's file system plus a note saying which command to run. **Docker** is the tool that builds images and runs containers. A **Dockerfile** is the text recipe for building an image: one instruction per line, run from the top. A **`.dockerignore`** file lists paths that must not be sent to the builder at all.

## 2. Why this repository uses it

Kubernetes runs containers, so both programs have to be images first. One Dockerfile builds both, as two **targets**: `service` (the web app, with its virtual environment) and `loadgen` (only the standard library, so the interpreter plus its own package). Both run as a non-root user, and the base image is pinned by a content hash so a rebuild tomorrow gives the same bytes.

## 3. Where it appears

| File | Role |
| --- | --- |
| `Dockerfile` | The recipe for both images. |
| `.dockerignore` | What the builder must not see. |
| `.env.example` | The settings the programs and the scripts read. |
| `scripts/build-image.sh` | Runs both `docker build --target ...` and imports the images (see `bash.md`). |
| `.github/workflows/ci.yml` | Lints the Dockerfile with hadolint (see `github-actions.md`). |

## 4. How it works underneath

An image is made of **layers**. Each `RUN`, `COPY` and `ADD` instruction produces a layer: the set of file changes it made. Layers are cached; if an instruction and everything before it are unchanged, Docker reuses the stored layer instead of running it again. That is why the Dockerfile copies `requirements.lock` and installs from it *before* copying the source code: the slow install step is reused when only the source changed.

A **multi-stage build** has several `FROM` lines. Each starts a fresh stage. A later stage can copy files out of an earlier one with `COPY --from=<stage>`, and everything else in the earlier stage is thrown away. The first stage (named `builder`) needs tools to install packages; the final stage (`runtime`) receives only the finished virtual environment.

`FROM image@sha256:...` names a base image by the **digest**, the SHA-256 hash of its manifest. A tag such as `3.13-slim-bookworm` can be moved to a different image later; a digest cannot.

When a container starts, Docker applies the image's `ENV` values, switches to the `USER`, changes into the `WORKDIR` and runs the `CMD` (or `ENTRYPOINT`). The `CMD` written as a JSON list (the **exec form**) starts the program directly as process number 1, so the program receives signals such as `SIGTERM` itself. The text form (`CMD gunicorn ...`) would start a shell first, and the shell would swallow the signal.

Docker sends the build **context** (the folder you build from) to the builder before it reads the Dockerfile. `.dockerignore` shrinks that context and keeps files such as `.git` or a local `.venv` out of the image.

## 5. Walkthrough of this repository's use

### `.dockerignore`

```text
.venv
.bin
.kube
.git
.github
.pytest_cache
.mypy_cache
.ruff_cache
.coverage
.kubeconform-cache
tests
manifests
cluster
scripts
docs
**/__pycache__
*.egg-info
```

One pattern per line; each is a path the builder does not receive. `.venv` is the developer's virtual environment; `.bin` the downloaded tools; `.kube` the generated cluster configuration (it holds credentials); `.git` and `.github` the history and CI files; `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.coverage` and `.kubeconform-cache` are caches that tools leave behind. `tests`, `manifests`, `cluster`, `scripts` and `docs` are not needed at run time. `**/__pycache__` matches Python's cache folders at any depth and `*.egg-info` the packaging metadata folders. Besides keeping the image small, this means the `.kube` credentials can never be copied into an image by accident.

### `Dockerfile`

```dockerfile
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
```

The comments explain that two images come from one file and give the two build commands with `--target`; both run as a non-root user with nothing but the interpreter and the code they need; the service is built in two stages so the final image holds the virtual environment and no pip or compiler, and the load generator needs only the standard library. `FROM python:3.13-slim-bookworm@sha256:... AS builder` starts the first stage from the slim Debian 12 image of Python 3.13, pinned by digest. `ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1` tells pip not to keep downloads or check for a newer pip. `WORKDIR /build`; `RUN python -m venv /opt/venv` creates a virtual environment and `ENV PATH=...` puts it first on the command path. `COPY requirements.lock ./` then `RUN pip install --require-hashes -r requirements.lock` installs exactly the pinned packages, refusing any whose hash is not listed. `COPY pyproject.toml README.md ./` and `COPY src ./src`, then `RUN pip install --no-deps .` installs the project (the lock already installed the dependencies).

```dockerfile
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
```

The `service` stage starts again from the same pinned base. `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=...` stops `.pyc` files (the file system is read-only in the cluster), makes output immediate and puts the virtual environment on the path. `COPY --from=builder /opt/venv /opt/venv` copies the finished environment out of the first stage. `RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app` creates a user with the fixed number 10001 and `USER 10001:10001` runs as it. `EXPOSE 8080` documents the port. `HEALTHCHECK` tells plain Docker to fetch `/healthz` every 30 seconds with a 2-second timeout (Kubernetes uses its own probes). The comment explains `--preload` (the app factory, and so the start-up delay, runs in the master before gunicorn opens its socket: for that second the port refuses connections), the `SIGTERM` behaviour (stop accepting and finish requests in flight within `--graceful-timeout`) and that `/tmp` is the only writable path. The `CMD` list starts gunicorn: bind `0.0.0.0:8080`, one worker with 16 threads of the `gthread` class, heartbeat folder `/tmp`, a 10-second graceful timeout, a 30-second worker timeout, `--preload`, access log to standard output and the factory `zero_downtime.interfaces.wsgi:build_app()` (every option is explained in `gunicorn.md`).

```dockerfile
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
```

The `loadgen` stage starts from the same base: environment as before plus `PYTHONPATH=/app` (where Python looks for packages), `COPY src/loadgen /app/loadgen` (only the generator's package), the same non-root user, `EXPOSE 9100` for the control port, a `HEALTHCHECK` against `http://127.0.0.1:9100/healthz` and `CMD ["python", "-m", "loadgen.interfaces.cli"]`, which runs the program's `main()` (see `loadgen.md`).

### `.env.example`

```text
# Variables the services and the scripts read. Export one in your shell to override it; every value shown is
# the default and there are no secrets in this project.

# The web service (set in manifests/base/web.yaml).
STARTUP_DELAY_SECONDS=1
LATENCY_MIN_MS=50
LATENCY_MAX_MS=200
# Part of each request's latency that is real computation; it is what the autoscaler's CPU metric responds to.
CPU_WORK_MS=2
# DEBUG, INFO, WARNING or ERROR.
LOG_LEVEL=INFO

# The load generator (set in manifests/base/loadgen.yaml).
TARGET_URL=http://web.zero-downtime.svc.cluster.local/
RATE_PER_SECOND=100
TIMEOUT_SECONDS=2
MAX_IN_FLIGHT=256
CONTROL_PORT=9100

# The scripts.
CLUSTER_NAME=zero-downtime
NAMESPACE=zero-downtime
```

A template of the settings the project reads (see `environment.md`). For the web service (set in `manifests/base/web.yaml`): `STARTUP_DELAY_SECONDS=1`, `LATENCY_MIN_MS=50`, `LATENCY_MAX_MS=200`, `CPU_WORK_MS=2` (the part of each request that is real computation, which the autoscaler's CPU metric responds to) and `LOG_LEVEL=INFO`. For the load generator (set in `manifests/base/loadgen.yaml`): `TARGET_URL` (the web Service's DNS name), `RATE_PER_SECOND=100`, `TIMEOUT_SECONDS=2`, `MAX_IN_FLIGHT=256` and `CONTROL_PORT=9100`. For the scripts: `CLUSTER_NAME=zero-downtime` and `NAMESPACE=zero-downtime`.


## 6. Try it yourself

```bash
make docker-build
docker image ls | grep zero-downtime
docker run --rm zero-downtime-web:0.1.0 python -c "import os; print(os.getuid())"
```

The build ends with `naming to ... zero-downtime-loadgen:0.1.0`; the list shows both images (the load generator is the smaller one); the third command prints `10001`, proof that the process is not root.

```bash
docker run --rm zero-downtime-loadgen:0.1.0 sh -c "ls /app; ls /opt 2>&1"
```

Shows only the `loadgen` package and no virtual environment: nothing but the standard library is needed.

```bash
docker history zero-downtime-web:0.1.0 | head -8
```

Lists each layer with the instruction that made it and its size.

```bash
docker run --rm -e CPU_WORK_MS=1000 -e LATENCY_MIN_MS=10 zero-downtime-web:0.1.0; echo "exit $?"
```

Prints `FATAL invalid configuration: LATENCY_MIN_MS, LATENCY_MAX_MS or CPU_WORK_MS: cpu_ms must be between 0 and min_ms, got 1000.0` and `exit 1`.

## 7. Mistakes people make

- **Using a moving tag.** `python:latest` changes under you. This file pins a digest.
- **Running as root.** A process with user id 0 can do far more damage if it is taken over. Here `USER 10001:10001`.
- **Copying everything first.** `COPY . .` before the install invalidates the cache on every source edit.
- **Leaving build tools in the final image.** A compiler and `pip` help an attacker; the multi-stage build drops them.
- **The shell form of `CMD`.** Signals do not reach the app, so shutdown becomes a hard kill after the timeout.
- **Forgetting `.dockerignore`.** The `.git` folder or secrets in the working directory end up inside the image.

## 8. Check yourself

1. Why does the Dockerfile have three `FROM` lines? *A build stage, then one stage for each of the two images.*
2. Why does the load generator image need no virtual environment? *It uses only the standard library.*
3. What does `--target service` do? *Builds the image only up to the stage named `service`.*
4. What does `USER 10001:10001` change? *The process runs as an ordinary numeric user and group, not root.*
5. Why does `service` use `--preload`? *The one-second warm-up then runs before the listening socket is opened (see `gunicorn.md`).*

## 9. Further reading

- Dockerfile reference: https://docs.docker.com/reference/dockerfile/
- Multi-stage builds: https://docs.docker.com/build/building/multi-stage/
- Build cache: https://docs.docker.com/build/cache/
- `.dockerignore`: https://docs.docker.com/build/concepts/context/#dockerignore-files
- Best practices: https://docs.docker.com/build/building/best-practices/
