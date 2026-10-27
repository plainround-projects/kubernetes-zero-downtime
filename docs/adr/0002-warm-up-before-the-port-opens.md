# 0002. The startup delay runs before the server opens its port

Date: 2026-10-27. Status: accepted.

## Context

The lesson gives the service a one-second startup delay so that a pod with no readiness probe is sent traffic it cannot yet take. Where the delay sits in the process decides whether that failure is visible. If the server opens its port first and the app loads second, connections wait in the kernel's accept queue for the second and succeed late: slow, but not an error.

## Decision

`build_app()` waits out `STARTUP_DELAY_SECONDS` and gunicorn runs with `--preload`, so the app factory (and the delay) completes in the master process before gunicorn creates its listening socket. For that second the port refuses connections. `tests/integration/test_gunicorn.py` asserts exactly that, and that the same command serves once the delay is over.

## Alternatives considered

- **Delay at import time with the lesson's `time.sleep(1)` in module scope.** Same effect under `--preload`, but hides the delay in a side effect of importing; a use case `WarmUp` with an injected `Waiter` is testable.
- **Open the port first and answer `/readyz` with 503 until warm.** The better production design, and the stretch goal's startup probe covers it; it would not show the lesson's naive failure.

## Consequences

A pod is not reachable for the warm-up second even for its liveness probe, so the `graceful` overlay gives liveness an `initialDelaySeconds` of five. A startup probe (a stretch goal) is the production answer for slow starts.
