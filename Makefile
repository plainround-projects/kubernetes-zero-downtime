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

$(STAMP): requirements-dev.lock pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --require-hashes -r requirements-dev.lock
	$(VENV)/bin/pip install -q --no-deps -e .
	touch $@

install: $(STAMP) ## create .venv from the lock file

tools: ## download the pinned kind and kubeconform into .bin (checksum-verified)
	scripts/tools.sh

up: tools ## create the 4-node kind cluster, install metrics-server, build and load both images, deploy
	scripts/cluster-start.sh
	scripts/metrics-server.sh
	scripts/build-image.sh
	scripts/deploy.sh $(OVERLAY)

image: ## rebuild both images and load them into the cluster
	scripts/build-image.sh

deploy: ## apply an overlay: make deploy OVERLAY=naive|graceful|rolling-tuned|production
	scripts/deploy.sh $(OVERLAY)

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

audit: $(STAMP) ## check the locked dependencies for known vulnerabilities
	$(VENV)/bin/pip-audit --no-deps --disable-pip -r requirements-dev.lock

check: lint test ## lint plus unit and policy tests

docker-build: ## build both images without loading them anywhere
	docker build --target service -t zero-downtime-web:0.1.0 .
	docker build --target loadgen -t zero-downtime-loadgen:0.1.0 .

down: ## delete the kind cluster and its kubeconfig
	scripts/cluster-down.sh

clean: down
	rm -rf $(VENV) .bin .pytest_cache .mypy_cache .ruff_cache .coverage .kubeconform-cache
