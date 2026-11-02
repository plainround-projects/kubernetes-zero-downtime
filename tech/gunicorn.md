# gunicorn

## 1. What it is

gunicorn ("Green Unicorn") is a **WSGI HTTP server** for Python. A server here is a long-running program that listens on a network port, accepts connections, reads web requests and hands each one to your Python application. The command is `gunicorn`; this repository uses version 25.3 (pinned in `requirements.lock`).

## 2. Why this repository uses it

Flask can serve requests by itself, but only through a development server that is not meant for real traffic. gunicorn is the standard production choice, and here it is also the instrument of the lesson: its start-up order (build the app, then open the port) is the one-second window in which a pod refuses connections, and its handling of the `SIGTERM` signal (stop accepting, finish what is in flight) is what makes a graceful shutdown possible.

## 3. Where it appears

| File | Role |
| --- | --- |
| `Dockerfile` | The `CMD` line of the `service` image starts gunicorn with its options (see `docker.md`). |
| `src/zero_downtime/interfaces/wsgi.py` | The factory `build_app()` that gunicorn calls to get the application. |
| `manifests/base/web.yaml` | Sets `terminationGracePeriodSeconds` and the `preStop` delay that must cover gunicorn's graceful timeout (see `kubernetes.md`). |
| `tests/integration/test_gunicorn.py` | Starts the real gunicorn command and calls it. |
| `requirements.lock` | The exact version and hashes. |

## 4. How it works underneath

When gunicorn starts it runs a **master** process. The master reads the options, imports your application, opens the listening socket (a socket is the operating system's handle for a network port) and then starts one or more **worker** processes. Workers do the request handling; the master only watches them and restarts any that crash.

The `gthread` worker class used here lets each worker run several **threads**. A thread is a path of execution inside a process that shares the process's memory; four threads in one worker can handle four requests at once.

**Signals** are small messages the operating system sends to a process. `SIGTERM` means "please stop". gunicorn answers by finishing the requests it is working on, up to a time limit (`--graceful-timeout`), then exiting. That is what makes a rolling update in Kubernetes polite.

With `--preload`, the master imports (and so builds) the application *before* it starts the workers. If building the app fails, the master exits with an error instead of starting workers that fail one after another.

A command like `zero_downtime.interfaces.wsgi:build_app()` is gunicorn's "application factory" syntax: `module:callable()` means "import the module and call this function to obtain the application".

## 5. Walkthrough of this repository's use

### `src/zero_downtime/interfaces/wsgi.py`

```text
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
```

The docstring explains the design. gunicorn calls the factory with `--preload`, so the warm-up runs in the master process before gunicorn opens its listening socket: for that second the port refuses connections, exactly what a pod without a readiness probe is sent traffic into. Imports: `logging`, `os`, `Flask`, both use cases, logging setup, the real runtime objects, the settings and `create_app`. This file is the **composition root**: the one place where concrete pieces are chosen and connected.

```text
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
```

`build_app(env=None)` reads the environment (`dict(os.environ)` copies the real one, or a test passes a dictionary). If `Settings.from_env` raises `ConfigurationError` it sets up logging at `INFO`, logs `FATAL invalid configuration: ...` and raises `SystemExit(1)`, which ends the process with exit code 1. After a good read it configures JSON logging, creates a `SleepWaiter`, runs `WarmUp(waiter, delay in milliseconds)()` (this is the one-second pause) and logs `warm-up complete` with the time. Finally it builds the `HandleRequest` use case from the profile and the real random source, busy loop and waiter, and returns `create_app(handle)` to gunicorn.


### The command line, option by option

The `CMD` of the `service` image in the `Dockerfile` is the only place the options are written:

- `gunicorn` the program.
- `--bind 0.0.0.0:8080` listen on port 8080 on every network address of the container.
- `--workers 1` one worker process.
- `--threads 16` sixteen threads in that worker, so up to sixteen requests are handled at once (each spends most of its time sleeping, which uses no CPU).
- `--worker-class gthread` the worker kind that supports threads.
- `--worker-tmp-dir /tmp` where workers keep the heartbeat files they use to tell the master they are alive; the root file system is read-only, so the Deployment mounts a writable `emptyDir` at `/tmp`.
- `--graceful-timeout 10` after `SIGTERM`, wait up to ten seconds for requests in progress.
- `--timeout 30` a worker that has not shown a heartbeat for 30 seconds is considered stuck and restarted.
- `--preload` build the app in the master process, which runs the one-second warm-up before gunicorn opens its listening socket.
- `--access-logfile -` write one line per request to standard output.
- `zero_downtime.interfaces.wsgi:build_app()` the factory to call.

## 6. Try it yourself

```bash
kubectl -n zero-downtime logs deploy/web | head -8
```

Shows gunicorn's start-up lines (`Starting gunicorn`, `Listening at: http://0.0.0.0:8080`, `Using worker: gthread`, `Booting worker with pid: ...`) and the JSON line `"msg": "warm-up complete"`, in that order: the warm-up ran before the port opened.

```bash
.venv/bin/python -m pytest tests/integration/test_gunicorn.py -q
```

Starts the real command and checks it refuses connections during the warm-up, then serves, and stops cleanly on `SIGTERM`.

```bash
docker run --rm -e STARTUP_DELAY_SECONDS=bad zero-downtime-web:0.1.0; echo "exit $?"
```

(after `make docker-build`) Prints a JSON line with `FATAL invalid configuration: STARTUP_DELAY_SECONDS must be a number, got 'bad'` and `exit 1`.

## 7. Mistakes people make

- **Opening the port before the app is ready.** Without `--preload` (or with a slow start inside a worker) the port can accept connections the app cannot yet serve. Here `--preload` makes the port stay closed until warm-up ends, and Kubernetes' readiness probe keeps traffic away until it answers.
- **Binding to `127.0.0.1` in a container.** Nothing outside the container can reach it.
- **A graceful timeout longer than the pod's grace period.** Kubernetes would kill the container before gunicorn finished; the policy tests check the grace period covers the `preStop` sleep plus gunicorn's graceful timeout.
- **Writing the heartbeat files to a read-only file system.** The worker cannot start; hence `--worker-tmp-dir /tmp` and the writable `/tmp` volume.
- **Too few threads.** Each request here sleeps up to 200 ms, so sixteen threads are needed for tens of requests per second per pod.

## 8. Check yourself

1. What are the master and the workers? *The master watches and restarts; workers handle requests.*
2. What does `--preload` change here? *The app, and its warm-up, are built in the master before the listening socket is opened.*
3. What does `0.0.0.0` mean in `--bind`? *Listen on every network address of the machine.*
4. What happens on `SIGTERM`? *gunicorn stops accepting, finishes requests in progress (up to 10 seconds) and exits.*
5. Why 16 threads? *Requests spend most of their time sleeping, so many can overlap in one worker.*

## 9. Further reading

- gunicorn 25.x documentation: https://gunicorn.org/
- Settings reference: https://gunicorn.org/reference/settings/
- Signals: https://gunicorn.org/signals/
- WSGI: https://wsgi.readthedocs.io/en/latest/
