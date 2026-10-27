# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-27

### Added

- A web service (Python, Flask, gunicorn) with a one-second warm-up before its port opens, 50 to 200 ms requests and graceful shutdown, and a fixed-rate load generator that counts every failed request by class and keeps latency percentiles.
- Kustomize overlays for each step of the lesson: `naive`, `graceful`, `rolling-tuned` and `production` (autoscaler, disruption budget, topology spread).
- Scripts for the runbook (create the four-node cluster, load the images, install metrics-server, roll under load, drain the busiest node) and a cluster test that runs all four operations and records the error count of each.
- Two layered packages with the dependency rule enforced by import-linter, unit, policy and integration tests, ruff, mypy, hadolint, yamllint, shellcheck, kubeconform, a hash-pinned lock file and a CI workflow.
