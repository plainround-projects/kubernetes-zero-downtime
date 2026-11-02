# Bash (the scripts)

## 1. What it is

A **shell** is a program that reads commands you type and runs them. **Bash** is the most common shell on Linux and macOS. A **shell script** is a text file of commands that Bash runs from top to bottom, with variables, `if` tests, loops and functions added. The files in `scripts/` are Bash scripts; the first line `#!/usr/bin/env bash` (the **shebang**) tells the operating system to run the file with Bash.

Words you need:

- A **variable** holds text: `name="value"`, read back as `$name` or `${name}`. Always put double quotes around a use (`"$name"`) so spaces do not split it into several words.
- `$(command)` runs the command and gives its output as text. `$1`, `$2` are the script's arguments; `$@` is all of them; `$#` counts them; `$?` is the exit status of the last command.
- An **exit status** is a number every command returns: 0 means success, anything else failure. `if`, `&&` (and then) and `||` (or else) all work on it.
- **Standard output** and **standard error** are the two output channels. `>file` sends output to a file, `2>&1` joins error to output, `>/dev/null` throws output away, `|` (pipe) feeds one command's output to the next one's input, `<file` feeds a file in.
- `&` at the end of a command runs it in the **background**; `$!` is its process number.
- `trap 'commands' EXIT` runs the commands when the script ends, however it ends.

## 2. Why this repository uses it

`make` targets are one-liners; the real work (create a four-node cluster, build two images, roll the pods under load and read the counters, drain a node) takes several steps with checks. Bash is on every machine, needs no installation and is the natural glue around `kubectl`, `docker` and `curl`. Each script is small and does one job.

## 3. Where it appears

| Script | Job |
| --- | --- |
| `scripts/lib.sh` | Settings and helper functions, sourced by the others. |
| `scripts/tools.sh` | Download `k3d` and `kubeconform` into `.bin`, verifying checksums. |
| `scripts/cluster-start.sh` | Create the four-node cluster under a lock and wait for the nodes. |
| `scripts/cluster-down.sh` | Delete the cluster. |
| `scripts/build-image.sh` | Build the service and load-generator images and import them into every node. |
| `scripts/deploy.sh` | Apply an overlay (naive, graceful, rolling-tuned or production) and wait for it. |
| `scripts/loadctl.sh` | Talk to the in-cluster load generator through a port-forward. |
| `scripts/rollout-under-load.sh` | One or more rolling updates under load, with the verdict. |
| `scripts/drain-busiest-node.sh` | Drain the worker with the most web pods, under load. |

## 4. How it works underneath

When you run `scripts/deploy.sh local`, the operating system reads the shebang, starts Bash with the file and the argument `local`. Bash runs each line in order. External commands (`kubectl`, `docker`) are separate programs that Bash starts and waits for. `source file` (also written `. file`) runs another file's lines inside the current shell, so its variables and functions become available: that is how `lib.sh` shares settings.

`set -euo pipefail` at the top makes scripts fail loudly instead of continuing after a mistake: `-e` stops at the first command that fails, `-u` treats an unset variable as an error, and `pipefail` makes a pipeline fail if any part fails (normally only the last part counts).

`${NAME:-default}` means "the value of `NAME`, or `default` if it is unset or empty". `[ -d dir ]` tests that a directory exists, `[ -f file ]` a file, `[ -x file ]` an executable, `[ -n s ]` a non-empty string. `case "$x" in pattern) ...;; esac` chooses by pattern. `until cmd; do ...; done` repeats while `cmd` fails. Arrays are written `names=(a b c)` and read `"${names[@]}"`.

The lock in `cluster-start.sh` uses a trick: `mkdir` either creates a directory or fails, and the operating system makes that check-and-create **atomic** (it cannot be interrupted halfway), so only one script can win. The winner writes its process number into the directory and removes the directory on exit; a waiting script that finds a lock whose owner process no longer exists (`kill -0` tests existence without sending a signal) removes the stale lock.

## 5. Walkthrough of this repository's use

### `scripts/tools.sh`

```bash
#!/usr/bin/env bash
# Download the pinned command-line tools into ./.bin (k3d and kubeconform), verifying each SHA-256.
# Nothing is installed system-wide. kubectl is not downloaded: use any kubectl within one minor version of
# the cluster (the cluster here is Kubernetes 1.35).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/.bin"
K3D_VERSION="v5.9.0"
KUBECONFORM_VERSION="v0.8.0"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) platform="darwin-arm64"; k3d_sha="fe106541d5d0a3f18debcd4d432a16f8c0ce3e6ddc06f8fbb6f696a122313e00"; conform_sha="f84f4dfbebf4a6b0b230385fa065a39ea35e02608c2b50d025dcf64775a69d67" ;;
  Darwin-x86_64) platform="darwin-amd64"; k3d_sha="b4aabc37534f95b9c764e7823f2df923f50d57600837aa60a06266cce47db732"; conform_sha="71dbc87ac9f24099a62b93570e65aa06312ba6ac8aea63b7f86e9d999edf5a92" ;;
  Linux-x86_64) platform="linux-amd64"; k3d_sha="06d8f25bc3a971c4eb29e0ff08429b180402db0f4dec838c9eac427e296800a0"; conform_sha="9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883" ;;
  Linux-aarch64 | Linux-arm64) platform="linux-arm64"; k3d_sha="03cde5cf23e6e8e67de5a039ecf26e5b85aca82fba3e5d13dadf904cd218a250"; conform_sha="1f53fc8e81258197a35e8603054162a5af1de8c5af13746c71ab680d9534ed87" ;;
  *) echo "unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
```

The comment says what is downloaded and that nothing is installed system-wide. `set -euo pipefail` as explained above. `ROOT` as in `lib.sh` (this script does not source it because it runs before anything else exists). `BIN` is the `.bin` folder. `K3D_VERSION="v5.9.0"` and `KUBECONFORM_VERSION="v0.8.0"` pin the versions. The `case "$(uname -s)-$(uname -m)" in` looks at the operating system and processor architecture (`Darwin-arm64` is an Apple-silicon Mac, `Linux-x86_64` an ordinary Linux server and so on). Each branch sets `platform` (the name used in download file names) and two SHA-256 hashes: `k3d_sha` for the k3d program and `conform_sha` for the kubeconform archive; the `;;` ends the branch. These hashes were taken from the official releases; if a download differs by one bit the script refuses it. The `|` in `Linux-aarch64 | Linux-arm64` lets either name match. The `*)` branch prints an error for any other machine and exits.

```bash
check_sha() { # file expected
  local actual
  actual="$(shasum -a 256 "$1" | cut -d' ' -f1)"
  [ "$actual" = "$2" ] || { echo "checksum mismatch for $1: expected $2, got $actual" >&2; rm -f "$1"; exit 1; }
}

mkdir -p "$BIN"
if [ ! -x "$BIN/k3d" ] || ! "$BIN/k3d" version 2>/dev/null | grep -q "k3d version $K3D_VERSION"; then
  echo "downloading k3d $K3D_VERSION ($platform)"
  curl -fsSL -o "$BIN/k3d" "https://github.com/k3d-io/k3d/releases/download/$K3D_VERSION/k3d-$platform"
  check_sha "$BIN/k3d" "$k3d_sha"
  chmod +x "$BIN/k3d"
fi
if [ ! -x "$BIN/kubeconform" ] || ! "$BIN/kubeconform" -v | grep -q "${KUBECONFORM_VERSION#v}"; then
  echo "downloading kubeconform $KUBECONFORM_VERSION ($platform)"
  archive="$(mktemp)"
  curl -fsSL -o "$archive" "https://github.com/yannh/kubeconform/releases/download/$KUBECONFORM_VERSION/kubeconform-$platform.tar.gz"
  check_sha "$archive" "$conform_sha"
  tar -xzf "$archive" -C "$BIN" kubeconform
  rm -f "$archive"
fi
echo "tools ready in $BIN: $("$BIN/k3d" version | head -1), kubeconform $("$BIN/kubeconform" -v)"
```

`check_sha()` is a function: `local actual` declares a private variable, `shasum -a 256 "$1" | cut -d' ' -f1` computes the file's hash and keeps the first column, and `[ "$actual" = "$2" ] || { ...; exit 1; }` compares it with the expected value, and on a mismatch prints both, deletes the file and exits. `mkdir -p "$BIN"` makes the folder. The first `if` downloads k3d unless an executable with the right version is already there: `! -x` tests "not executable", `k3d version | grep -q` tests the version text. `curl -fsSL -o file url` downloads quietly (`-f` fail on HTTP errors, `-s` silent, `-S` still show errors, `-L` follow redirects), then `check_sha` verifies, then `chmod +x` makes it runnable. The second `if` does the same for kubeconform, which comes as a `.tar.gz` archive: `mktemp` makes a temporary file name, `tar -xzf ... -C "$BIN" kubeconform` unpacks just that one file into `.bin`, and `rm -f` deletes the archive. `${KUBECONFORM_VERSION#v}` strips a leading `v`. The last `echo` prints the versions that are ready.

### `scripts/cluster-start.sh`

```bash
#!/usr/bin/env bash
# Create the local k3d cluster (idempotent) and wait for its node to be Ready.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
```

Shebang, a comment, `set -euo pipefail`, then `# shellcheck source=lib.sh` (tells the checker where the sourced file is) and `source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"` to load the shared settings.

```bash
"$ROOT/scripts/tools.sh" >/dev/null
mkdir -p "$(dirname "$KUBECONFIG")"
if "$K3D" cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER_NAME"; then
  log "cluster $CLUSTER_NAME already exists"
  "$K3D" cluster start "$CLUSTER_NAME" >/dev/null 2>&1 || true
else
  log "creating k3d cluster $CLUSTER_NAME (Kubernetes 1.35, one server node and three agents)"
  # One cluster is created at a time on this machine: take the lock directory, wait if another run holds it.
  lock=/tmp/plainround-kind-create.lock
  until mkdir "$lock" 2>/dev/null; do
    holder="$(cat "$lock/pid" 2>/dev/null || true)"
    if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then rm -rf "$lock"; continue; fi
    log "another cluster is being created (pid ${holder:-unknown}); waiting 30 seconds"
    sleep 30
  done
  echo $$ >"$lock/pid"
  trap 'rm -rf "$lock"' EXIT
  # The project's own kubeconfig is written below; ~/.kube/config is left alone.
  "$K3D" cluster create "$CLUSTER_NAME" --config "$ROOT/cluster/k3d-config.yaml" \
    --kubeconfig-update-default=false --kubeconfig-switch-context=false
fi
"$K3D" kubeconfig write "$CLUSTER_NAME" --output "$KUBECONFIG" >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=600s
kubectl get nodes
```

`"$ROOT/scripts/tools.sh" >/dev/null` makes sure the tools exist, hiding its output; `mkdir -p "$(dirname "$KUBECONFIG")"` creates the `.kube` folder. The `if` asks k3d for its cluster list without a header (`--no-headers`), keeps the first column with `awk '{print $1}'` and tests with `grep -qx` whether a line equals the cluster name exactly (`-q` quiet, `-x` whole line). If the cluster exists, say so and try `k3d cluster start` (so a stopped cluster is revived; `|| true` ignores the failure). Otherwise: log, then take the lock described above: `lock=/tmp/plainround-kind-create.lock`; `until mkdir "$lock" 2>/dev/null; do` repeats until the directory can be created; inside, `holder` reads the owner's number; if there is one and `kill -0` shows that process is dead, the stale lock is removed and the loop continues; otherwise it logs and sleeps 30 seconds. After winning, `echo $$ >"$lock/pid"` records this script's own process number (`$$`) and `trap 'rm -rf "$lock"' EXIT` removes the lock when the script ends. The lock exists because creating several clusters at once on one laptop made all of them time out. Then `k3d cluster create` with `--config` (the YAML in `k3d.md`), `--kubeconfig-update-default=false` and `--kubeconfig-switch-context=false` (do not touch the user's own kubeconfig). After the `fi`, `k3d kubeconfig write ... --output "$KUBECONFIG"` saves the credentials in the project's file, `kubectl wait --for=condition=Ready nodes --all --timeout=600s` waits up to ten minutes for the node and `kubectl get nodes` shows it.

### `scripts/cluster-down.sh`

```bash
#!/usr/bin/env bash
# Delete the local k3d cluster and its kubeconfig.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ -x "$K3D" ] && "$K3D" cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER_NAME"; then
  "$K3D" cluster delete "$CLUSTER_NAME"
else
  log "no cluster named $CLUSTER_NAME"
fi
rm -rf "$ROOT/.kube"
```

Same header. If the k3d binary exists (`-x`) and the cluster is listed, `k3d cluster delete` removes it; otherwise it logs that there is nothing to delete. Finally `rm -rf "$ROOT/.kube"` removes the generated credentials, so after `make down` nothing of the cluster remains.

### `scripts/lib.sh`

```bash
#!/usr/bin/env bash
# shellcheck shell=bash
# Shared settings for the scripts. Sourced, not run.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-zero-downtime}"
NAMESPACE="${NAMESPACE:-zero-downtime}"
WEB_IMAGE="${WEB_IMAGE:-zero-downtime-web:0.1.0}"
LOADGEN_IMAGE="${LOADGEN_IMAGE:-zero-downtime-loadgen:0.1.0}"
K3D="$ROOT/.bin/k3d"
export ROOT CLUSTER_NAME NAMESPACE WEB_IMAGE LOADGEN_IMAGE K3D
# A kubeconfig of its own: this project never reads or changes ~/.kube/config.
export KUBECONFIG="${KUBECONFIG:-$ROOT/.kube/config}"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

require_cluster() {
  kubectl get nodes >/dev/null 2>&1 || die "no cluster reachable through $KUBECONFIG; run: make up"
}
```

Shebang and `# shellcheck shell=bash`; the file is **sourced**, never run. `ROOT` is the repository folder (found from this file's own path). Settings with overridable defaults (`${NAME:-default}` means "the variable, or the default if unset"): `CLUSTER_NAME` and `NAMESPACE` (both `zero-downtime`), `WEB_IMAGE` and `LOADGEN_IMAGE` (`zero-downtime-web:0.1.0` and `zero-downtime-loadgen:0.1.0`), and `K3D`, the downloaded binary. `export` makes them visible to programs the scripts start; `KUBECONFIG` points `kubectl` at the project's own credentials file. `log()` prints a line starting with `==>`; `die()` prints an error on standard error and exits with status 1. `require_cluster()` runs `kubectl get nodes` with output discarded and, if that fails, calls `die` with the hint to run `make up`.

### `scripts/build-image.sh`

```bash
#!/usr/bin/env bash
# Build the service and load generator images and import both into every node of the k3d cluster (there is no registry).
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "building $WEB_IMAGE"
docker build --target service --tag "$WEB_IMAGE" "$ROOT"
log "building $LOADGEN_IMAGE"
docker build --target loadgen --tag "$LOADGEN_IMAGE" "$ROOT"
if "$K3D" cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER_NAME"; then
  log "importing both images into cluster $CLUSTER_NAME"
  "$K3D" image import "$WEB_IMAGE" "$LOADGEN_IMAGE" --cluster "$CLUSTER_NAME" --mode direct
fi
```

The comment says what it does: build the two images and import both into every node of the k3d cluster, because there is no registry. `docker build --target service --tag "$WEB_IMAGE" "$ROOT"` builds the `service` stage of the Dockerfile; the second `docker build --target loadgen` builds the load-generator stage. If the cluster exists (the `k3d cluster list | awk | grep -qx` test used by the other scripts), `k3d image import` copies both images into all the cluster's nodes (`--mode direct`).

### `scripts/deploy.sh`

```bash
#!/usr/bin/env bash
# Apply an overlay (naive, graceful, rolling-tuned or production; default production) and wait for it.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster
```

The comment gives the usage: apply an overlay (naive, graceful, rolling-tuned or production; default production) and wait for it. Then the usual header: `set -euo pipefail`, `source` of the shared library and `require_cluster`.

```bash
overlay="${1:-production}"
[ -d "$ROOT/manifests/overlays/$overlay" ] || die "no overlay named $overlay"
log "applying overlay $overlay"
if [ "$overlay" != "production" ]; then
  # kubectl apply does not delete what an overlay no longer contains; the earlier steps have no autoscaler
  # or disruption budget, so remove them when going back to one of those steps.
  kubectl -n "$NAMESPACE" delete horizontalpodautoscaler,poddisruptionbudget -l app.kubernetes.io/part-of=zero-downtime --ignore-not-found || true
fi
kubectl apply -k "$ROOT/manifests/overlays/$overlay"
kubectl -n "$NAMESPACE" rollout status deployment/web --timeout=600s
kubectl -n "$NAMESPACE" rollout status deployment/loadgen --timeout=600s
# The autoscaler (production only) may still be bringing the Deployment up to its minimum.
for _ in $(seq 1 60); do
  ready="$(kubectl -n "$NAMESPACE" get deployment web -o jsonpath='{.status.readyReplicas}')"
  [ "${ready:-0}" -ge 6 ] && break
  sleep 3
done
kubectl -n "$NAMESPACE" get pods -o wide
```

`overlay="${1:-production}"` takes the first argument or `production`, and a check makes sure the overlay folder exists. For any overlay other than production, the script first deletes the HorizontalPodAutoscaler and PodDisruptionBudget of the project (`kubectl delete ... -l app.kubernetes.io/part-of=zero-downtime --ignore-not-found || true`): the comment explains `kubectl apply` does not delete what an overlay no longer contains, and the earlier steps have no autoscaler or budget. Then `kubectl apply -k` applies the overlay and `rollout status` waits (600 s each) for the `web` and `loadgen` Deployments. A loop of up to 60 tries, 3 seconds apart, waits until `web` reports at least six ready replicas (the autoscaler, in production, may still be bringing the Deployment up to its minimum): `ready="$(kubectl ... -o jsonpath='{.status.readyReplicas}')"` and `[ "${ready:-0}" -ge 6 ] && break`. Finally it lists the pods with their nodes.

### `scripts/loadctl.sh`

```bash
#!/usr/bin/env bash
# Talk to the in-cluster load generator through a short-lived port-forward.
#   loadctl.sh stats        the counters since the last reset
#   loadctl.sh reset        print the final counters of the window and start a new one
#   loadctl.sh rate 300     change the request rate (requests per second)
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster
```

The comment documents three commands: `stats`, `reset`, `rate <n>`. After the header, `require_cluster`.

```bash
port="${LOCAL_PORT:-$((19000 + RANDOM % 1000))}"
kubectl -n "$NAMESPACE" port-forward svc/loadgen "$port:9100" >/dev/null &
forward=$!
trap 'kill $forward 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -fs "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
case "${1:-stats}" in
  stats) curl -fsS "http://127.0.0.1:$port/stats" ;;
  reset) curl -fsS -X POST "http://127.0.0.1:$port/reset" ;;
  rate) curl -fsS -X POST -d "{\"rps\": ${2:?usage: loadctl.sh rate <requests per second>}}" "http://127.0.0.1:$port/rate" ;;
  *) die "usage: loadctl.sh stats|reset|rate <n>" ;;
esac
echo
```

`port="${LOCAL_PORT:-$((19000 + RANDOM % 1000))}"` picks a random local port between 19000 and 19999 unless one is given. `kubectl port-forward svc/loadgen "$port:9100" >/dev/null &` opens a tunnel to the generator's control port in the background; `forward=$!` stores its process number and the `trap` kills it on exit. The loop waits up to 20 seconds for `/healthz` to answer. The `case` on the first argument (default `stats`) calls the control API with `curl`: `GET /stats`, `POST /reset`, or `POST /rate` with a JSON body `{"rps": N}` (`${2:?usage...}` aborts with a usage message if the number is missing); any other word dies with the usage. The closing `echo` adds a newline after the JSON.

### `scripts/rollout-under-load.sh`

```bash
#!/usr/bin/env bash
# One rolling update under load, with the verdict: reset the counters, restart the web pods, wait for the
# rollout and a few seconds more, then print what the load generator counted. Usage: rollout-under-load.sh [runs]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster

runs="${1:-1}"
for run in $(seq 1 "$runs"); do
  "$ROOT/scripts/loadctl.sh" reset >/dev/null
  kubectl -n "$NAMESPACE" rollout restart deployment/web >/dev/null
  kubectl -n "$NAMESPACE" rollout status deployment/web --timeout=600s >/dev/null
  sleep 8
  printf 'rollout %s of %s: ' "$run" "$runs"
  "$ROOT/scripts/loadctl.sh" stats
done
```

The comment: one rolling update under load, with the verdict. `runs="${1:-1}"` is how many to do. For each run (`for run in $(seq 1 "$runs")`): reset the counters (output discarded), `kubectl rollout restart deployment/web` (replace every pod, as a new image would), wait for the rollout to finish, sleep 8 seconds so late failures are counted, print `rollout N of M: ` without a newline (`printf`), then print the counters with `loadctl.sh stats`.

### `scripts/drain-busiest-node.sh`

```bash
#!/usr/bin/env bash
# Step 5: show where the web pods are, drain the worker holding the most of them, show the disruption budget
# holding evictions back, then uncordon. The load generator keeps counting errors the whole time.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cluster

node="$(kubectl -n "$NAMESPACE" get pods -l app=web -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')"
log "web pods per node"
kubectl -n "$NAMESPACE" get pods -l app=web -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c
"$ROOT/scripts/loadctl.sh" reset >/dev/null
trap 'kubectl uncordon "$node" >/dev/null 2>&1 || true' EXIT
log "draining $node"
kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=600s
sleep 5
log "errors counted during the drain:"
"$ROOT/scripts/loadctl.sh" stats
log "web pods per node after the drain"
kubectl -n "$NAMESPACE" get pods -l app=web -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c
```

The comment: show where the web pods are, drain the worker holding the most of them, show the budget holding evictions back, then uncordon. The first command finds the busiest node: list the web pods' node names with `jsonpath` (`{range .items[*]}{.spec.nodeName}{"\n"}{end}` prints one name per line), `sort | uniq -c` counts duplicates, `sort -rn` puts the biggest first, `head -1 | awk '{print $2}'` keeps the name. It logs and prints the per-node counts, resets the load counters, and sets `trap 'kubectl uncordon "$node" ...' EXIT` so the node is made schedulable again however the script ends. `kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout=600s` marks the node unschedulable (**cordon**) and evicts its pods one at a time, respecting the PodDisruptionBudget; `--ignore-daemonsets` skips pods that run on every node, `--delete-emptydir-data` allows pods with scratch volumes to go. After 5 seconds it prints the errors counted during the drain and the pods per node after the drain.


## 6. Try it yourself

```bash
bash -n scripts/deploy.sh && echo syntax ok
```

`-n` reads the script without running it: prints `syntax ok`.

```bash
.venv/bin/shellcheck -x scripts/*.sh && echo clean
```

No output other than `clean` (see `shellcheck.md`).

```bash
scripts/deploy.sh nonsense
```

Prints `error: no cluster reachable ...` without a cluster, or `error: no overlay named nonsense` with one, and exits with status 1 (`echo $?`).

```bash
make up && scripts/loadctl.sh stats && scripts/rollout-under-load.sh 1
```

Prints the counters, then restarts the web pods and prints `rollout 1 of 1: {...}` with `"errors": 0`.

## 7. Mistakes people make

- **Unquoted variables.** `rm -rf $dir/` with an empty `dir` becomes `rm -rf /`. Always `"$dir"`.
- **No `set -e`.** The script carries on after a failed step and reports success.
- **Leaving background jobs behind.** `loadctl.sh` kills its port-forward in a `trap`, and `drain-busiest-node.sh` uncordons the node in a `trap` even if the drain fails.
- **Assuming macOS and Linux tools match.** The checksum command is `shasum -a 256` on both here, not `sha256sum`.
- **Measuring before the rollout settles.** `rollout-under-load.sh` waits for the rollout and eight seconds more before reading the counters.

## 8. Check yourself

1. What does `set -euo pipefail` do? *Stop on errors, on unset variables and on a failing pipeline part.*
2. Why does `deploy.sh` delete the autoscaler and budget when going back to an earlier overlay? *`kubectl apply` does not delete what an overlay no longer contains.*
3. Why does `drain-busiest-node.sh` use `trap ... EXIT`? *The node must be uncordoned however the script ends.*
4. How does the lock stay safe if a script crashes? *The next script sees the owner process is gone and removes the lock.*
5. What does `loadctl.sh rate 300` do? *Tells the load generator to send 300 requests per second.*

## 9. Further reading

- Bash reference manual: https://www.gnu.org/software/bash/manual/bash.html
- The set builtin: https://www.gnu.org/software/bash/manual/bash.html#The-Set-Builtin
- Shell parameter expansion: https://www.gnu.org/software/bash/manual/bash.html#Shell-Parameter-Expansion
- Conditional expressions: https://www.gnu.org/software/bash/manual/bash.html#Bash-Conditional-Expressions
