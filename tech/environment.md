# Environment variables and configuration

## 1. What it is

An **environment variable** is a named text value that the operating system hands to every program it starts: `LATENCY_MAX_MS=200`. A program reads it by name (in Python, `os.environ["LATENCY_MAX_MS"]`). The set of variables a process receives is its **environment**; it is copied from the parent that started it, which is how a Kubernetes pod, a Docker container, a shell and `make` can all steer the same program without changing its code.

## 2. Why this repository uses it

Container platforms deliver configuration this way. The same image runs everywhere; only the environment differs. Here it also makes the lesson adjustable without rebuilding: `STARTUP_DELAY_SECONDS` is the warm-up the lesson's first failure is built on, `CPU_WORK_MS` sets how much CPU each request really uses (and so what percentage the autoscaler sees), and `RATE_PER_SECOND` sets the load.

## 3. Where it appears

| Place | Variables |
| --- | --- |
| `.env.example` | The documented list with defaults (walkthrough in `docker.md`). |
| `src/zero_downtime/infrastructure/settings.py` | Reads and validates `STARTUP_DELAY_SECONDS`, `LATENCY_MIN_MS`, `LATENCY_MAX_MS`, `CPU_WORK_MS`, `LOG_LEVEL` (see `python.md`). |
| `src/loadgen/infrastructure/settings.py` | Reads and validates `TARGET_URL`, `RATE_PER_SECOND`, `TIMEOUT_SECONDS`, `MAX_IN_FLIGHT`, `CONTROL_PORT`, `LOG_LEVEL`. |
| `manifests/base/web.yaml`, `loadgen.yaml` | `env` entries that set them in the pods. |
| `scripts/lib.sh` | `CLUSTER_NAME`, `NAMESPACE`, `WEB_IMAGE`, `LOADGEN_IMAGE`, `KUBECONFIG`. |
| `scripts/loadctl.sh` | `LOCAL_PORT`. |
| `Makefile` | `export KUBECONFIG` and `PATH`; `OVERLAY`, `RPS`, `PYTHON`; the tests read `SPIKE_SECONDS`. |

## 4. How it works underneath

When a process starts, the operating system gives it a block of `NAME=value` strings. A child process inherits a copy, so a change in the child never affects the parent. In a shell, `export NAME=value` marks a variable for inheritance; `NAME=value command` sets it for that one command only. In a Kubernetes pod, the kubelet builds the block from the pod spec: `env` entries one by one, `envFrom` all keys of a ConfigMap, and `valueFrom.secretKeyRef` takes a key from a Secret. Values from a Secret are base64-encoded when stored (an encoding, not encryption) and decoded when delivered.

The app validates its environment once at start. A bad value makes it print a `FATAL invalid configuration: ...` line and exit 1, which Kubernetes shows as a crash.

## 5. Walkthrough of this repository's use



## 6. Try it yourself

```bash
STARTUP_DELAY_SECONDS=0.2 .venv/bin/python -c "from zero_downtime.infrastructure.settings import Settings; import os; print(Settings.from_env(os.environ).startup_delay_seconds)"
```

Prints `0.2`: the one-command prefix set the variable only for that command.

```bash
kubectl -n zero-downtime exec deploy/web -- env | grep -E "STARTUP|LATENCY|CPU_WORK"
```

Shows the four settings the Deployment passes.

```bash
SPIKE_SECONDS=60 make smoke
```

Runs the cluster tests with a one-minute spike instead of five minutes (the test reads the variable).

## 7. Mistakes people make

- **Committing a real `.env` file.** `.gitignore` excludes `.env`; `.env.example` has no secrets (this project has none).
- **Reading a variable at import time.** Here settings are read by a function, so tests can pass a dictionary.
- **Assuming a number.** Every variable is text; both programs convert and check ranges.
- **Letting Kubernetes inject names.** `enableServiceLinks: false` stops variables such as `WEB_PORT=tcp://...` from appearing in the pods.

## 8. Check yourself

1. Does a child process change its parent's environment? *No, it has a copy.*
2. Where does the web service's warm-up delay come from? *`STARTUP_DELAY_SECONDS`, set in the Deployment.*
3. What happens on an invalid value? *The program logs a FATAL line and exits with status 1.*
4. Why does the load generator call `http://web.zero-downtime.svc.cluster.local/`? *That is the web Service's full DNS name inside the cluster.*
5. How is the spike shortened for CI? *`SPIKE_SECONDS=180` in the workflow's environment.*

## 9. Further reading

- Python `os.environ`: https://docs.python.org/3.13/library/os.html#os.environ
- Kubernetes ConfigMaps: https://kubernetes.io/docs/concepts/configuration/configmap/
- Kubernetes Secrets: https://kubernetes.io/docs/concepts/configuration/secret/
- Environment variables in pods: https://kubernetes.io/docs/tasks/inject-data-application/define-environment-variable-container/
