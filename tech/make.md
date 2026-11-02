# Make (the Makefile)

## 1. What it is

`make` is a command-line tool, older than most programming languages, that runs named recipes. A **Makefile** is the file where those recipes are written. Each recipe is a **target**: a name, a colon, optional **prerequisites** (other targets or files that must be ready first) and then indented lines of shell commands. You run one with `make <target>`; for example `make test`. In this repository `make` is not used to compile anything; it is the short, uniform front door to every task: `make up`, `make run`, `make test`, `make lint`, `make down`.

## 2. Why this repository uses it

The project must be runnable by someone who has never seen it. One memorable command per job (and `make help` to list them) is easier than a page of long commands: `make up`, `make run`, `make drain`, `make test`, `make down`. Make also remembers what is already done: the virtual environment target only reruns when the lock file changes.

## 3. Where it appears

The single file `Makefile` at the repository root. The README's "run it" section and the CI workflow call these targets.

## 4. How it works underneath

When you run `make up`, make reads the Makefile, finds the target `up`, first makes sure each prerequisite (`tools`) is up to date by running its recipe, then runs the recipe lines of `up` in order, one shell per line. If any line exits with a non-zero status, make stops and reports an error.

A target that names a file (like `$(STAMP)`, the file `.venv/.installed`) is rebuilt only if a prerequisite file is newer than it. A target that does not name a file (like `test`) is declared **`.PHONY`**, which tells make "this is only a name, always run the recipe". Without that, a file called `test` in the folder would make `make test` do nothing.

Variables: `NAME := value` sets a variable immediately; `NAME ?= value` sets it only if it is not already set (so `make deploy OVERLAY=naive` overrides the default). `$(NAME)` reads a variable. `$(shell ...)` runs a command and uses its output. Inside a recipe, `$@` means "the target's own name". A double dollar `$$overlay` passes a single `$` to the shell, so shell variables survive make's own expansion. A leading `@` on a recipe line hides the command from the output. `export NAME := value` also puts the variable in the environment of every command the recipe runs; that is how `KUBECONFIG` and `PATH` reach `kubectl`.

A comment that starts with `##` on a target line is just a comment to make; this file uses it as documentation that `make help` finds with `grep`.

Make requires recipe lines to start with a **tab** character, not spaces.

## 5. Walkthrough of this repository's use

### `Makefile`

```make
# make up | run | smoke | down      (make help lists everything)
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
VENV := .venv
PY := $(VENV)/bin/python
STAMP := $(VENV)/.installed
K8S_VERSION := 1.35.0
OVERLAYS := naive graceful rolling-tuned production
OVERLAY ?= production

# The project's own kubeconfig and the pinned tools in .bin: nothing here touches ~/.kube or the system.
export KUBECONFIG := $(CURDIR)/.kube/config
export PATH := $(CURDIR)/.bin:$(PATH)

.PHONY: help install tools up image deploy run stats rate drain lint format conform test test-integration \
        smoke audit check docker-build down clean

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort
```

Line 1 lists the main targets. `PYTHON ?=` picks the first Python found (`command -v` prints a command's path if it exists; `||` tries the next). `VENV`, `PY`, `STAMP` name the virtual environment, its Python and the marker file whose age shows when the install finished. `K8S_VERSION := 1.35.0` is the schema version for kubeconform. `OVERLAYS` lists the four overlays and `OVERLAY ?= production` is the default one. `export KUBECONFIG := $(CURDIR)/.kube/config` makes every `kubectl` call use this project's own credentials; `export PATH := $(CURDIR)/.bin:$(PATH)` puts the pinned tools first. `.PHONY` lists the targets that are names. `help:` prints each target with a `##` comment.

```make
$(STAMP): requirements-dev.lock pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --require-hashes -r requirements-dev.lock
	$(VENV)/bin/pip install -q --no-deps -e .
	touch $@

install: $(STAMP) ## create .venv from the lock file

tools: ## download the pinned k3d and kubeconform into .bin (checksum-verified)
	scripts/tools.sh

up: tools ## create the 4-node k3d cluster, build and import both images, deploy
	scripts/cluster-start.sh
	scripts/build-image.sh
	scripts/deploy.sh $(OVERLAY)

image: ## rebuild both images and load them into the cluster
	scripts/build-image.sh

deploy: ## apply an overlay: make deploy OVERLAY=naive|graceful|rolling-tuned|production
	scripts/deploy.sh $(OVERLAY)
```

`$(STAMP): requirements-dev.lock pyproject.toml` rebuilds the virtual environment when either file is newer: create it, install the hash-pinned development packages, install the project editable without dependencies (`-e .`) and `touch $@`. `install:` gives it a friendly name. `tools:` runs `scripts/tools.sh`. `up: tools` creates the four-node cluster, builds and imports both images, and deploys `$(OVERLAY)`. `image:` rebuilds only the images. `deploy:` applies the chosen overlay.

```make
run: ## three rolling updates in a row under 100 requests a second; prints the error count of each
	scripts/rollout-under-load.sh 3

stats: ## the load generator's counters since its last reset
	scripts/loadctl.sh stats

rate: ## change the request rate: make rate RPS=300
	scripts/loadctl.sh rate $(RPS)

drain: ## drain the worker holding the most web pods, under load, and print the error count
	scripts/drain-busiest-node.sh

lint: $(STAMP) conform ## ruff, format check, mypy, import rules, yamllint, shellcheck, hadolint, kubeconform
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/mypy
	$(VENV)/bin/lint-imports
	$(VENV)/bin/yamllint -s .
	$(VENV)/bin/shellcheck -x scripts/*.sh
	docker run --rm -i hadolint/hadolint:v2.12.0 < Dockerfile
```

`run:` runs `scripts/rollout-under-load.sh 3`: three rolling updates in a row under 100 requests a second, printing the error count of each. `stats:` prints the load generator's counters. `rate:` changes the request rate (`make rate RPS=300`). `drain:` runs the drain script. `lint:` depends on the environment and on `conform`, then runs `ruff check .`, `ruff format --check .`, `mypy`, `lint-imports`, `yamllint -s .`, `shellcheck -x scripts/*.sh` and hadolint on the Dockerfile in a throwaway container.

```make
conform: tools ## validate every rendered overlay against the Kubernetes 1.35 schemas with kubeconform
	@mkdir -p .kubeconform-cache
	@for overlay in $(OVERLAYS); do \
	  echo "kubeconform manifests/overlays/$$overlay"; \
	  kubectl kustomize manifests/overlays/$$overlay | .bin/kubeconform -strict -summary -kubernetes-version $(K8S_VERSION) -cache .kubeconform-cache || exit 1; \
	done

format: $(STAMP) ## apply ruff's formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

# Unit tests and the manifest policy checks need no cluster and no Docker. The domain and application stay at 95% or more.
test: $(STAMP) ## unit tests with coverage and the manifest policy checks (no cluster needed)
	$(PY) -m pytest tests/unit tests/policy --cov=zero_downtime --cov=loadgen --cov-report=term
	$(PY) -m coverage report --include='src/*/domain/*,src/*/application/*' --fail-under=95

test-integration: $(STAMP) ## the real gunicorn command and the load generator against each other (needs no cluster)
	$(PY) -m pytest tests/integration -m integration

smoke: $(STAMP) ## all four operations on the cluster, every Done when line (make up first; about 20 minutes)
	$(PY) -m pytest tests/e2e -m e2e -s -v
```

`conform:` creates the cache folder and, for each overlay, renders it with `kubectl kustomize` and validates it with kubeconform (`-strict`, the Kubernetes version, a cache folder, a summary), stopping at the first failure. `format:` applies `ruff check --fix` and `ruff format`. `test:` runs pytest on the unit and policy folders with coverage on both packages, then fails if the domain and application layers (`src/*/domain/*`, `src/*/application/*`) are under 95 percent. `test-integration:` runs the tests marked `integration`. `smoke:` runs the `e2e` tests on the cluster.

```make
audit: $(STAMP) ## check the locked dependencies for known vulnerabilities
	$(VENV)/bin/pip-audit --no-deps --disable-pip -r requirements-dev.lock

check: lint test ## lint plus unit and policy tests

docker-build: ## build both images without loading them anywhere
	docker build --target service -t zero-downtime-web:0.1.0 .
	docker build --target loadgen -t zero-downtime-loadgen:0.1.0 .

down: ## delete the k3d cluster and its kubeconfig
	scripts/cluster-down.sh

clean: down
	rm -rf $(VENV) .bin .pytest_cache .mypy_cache .ruff_cache .coverage .kubeconform-cache
```

`audit:` runs `pip-audit` on the locked packages. `check:` is lint plus test. `docker-build:` builds both images without loading them. `down:` deletes the cluster. `clean: down` deletes the cluster and then removes the virtual environment, the downloaded tools and every cache folder.


## 6. Try it yourself

```bash
make help
```

Prints one line per target with its `##` description, sorted.

```bash
make -n up
```

`-n` means "show the commands without running them". It prints `scripts/tools.sh`, `scripts/cluster-start.sh`, `scripts/build-image.sh`, `scripts/deploy.sh production` in order.

```bash
make install && make install
```

The first run builds `.venv`; the second prints `make: Nothing to be done for 'install'`.

```bash
make deploy OVERLAY=naive && make run; make deploy && make run
```

The first `make run` shows nonzero `errors` in each rollout; after `make deploy` (the finished service) the second shows `"errors": 0`.

## 7. Mistakes people make

- **Spaces instead of a tab** at the start of a recipe line: `*** missing separator`.
- **Forgetting `.PHONY`** for a target whose name matches a file.
- **Writing `$VAR` for a shell variable** inside a recipe; make eats it. Write `$$VAR`.
- **Assuming each line shares state.** Every recipe line runs in its own shell; a `cd` on one line does not carry to the next. The loops here use a backslash to keep one shell.
- **Hiding errors.** A recipe that ends with `|| true` can never fail; this Makefile uses it nowhere.

## 8. Check yourself

1. What does `.PHONY` do? *It marks targets that are names, not files, so they always run.*
2. Why does `make install` do nothing the second time? *`.venv/.installed` is newer than `requirements-dev.lock` and `pyproject.toml`.*
3. What does `OVERLAY ?= production` allow? *A default that `make deploy OVERLAY=naive` can override.*
4. Why `$$overlay` inside the loop? *To hand a real `$` to the shell.*
5. What makes `make lint` run `conform` first? *`conform` is a prerequisite of `lint`.*

## 9. Further reading

- GNU Make manual: https://www.gnu.org/software/make/manual/make.html
- Writing rules: https://www.gnu.org/software/make/manual/make.html#Rules
- Phony targets: https://www.gnu.org/software/make/manual/make.html#Phony-Targets
- Variables: https://www.gnu.org/software/make/manual/make.html#Using-Variables
