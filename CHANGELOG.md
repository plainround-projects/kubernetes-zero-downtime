# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Drawn architecture and flow figures in `docs/diagrams/`, light and dark, shown in the README.

## [0.1.0] - 2026-10-27

### Added

- A web service (Python, Flask, gunicorn) with a one-second warm-up before its port opens, 50 to 200 ms requests and graceful shutdown, and a fixed-rate load generator that counts every failed request by class and keeps latency percentiles.
- Kustomize overlays for each step of the lesson: `naive`, `graceful`, `rolling-tuned` and `production` (autoscaler, disruption budget, topology spread).
- `tech/`: one note per technology (31 files) explaining every file of the repository from first principles, with an index and a ten-minute reading list.
- The load generator counts `late_starts` and never reuses a source port that failed within 125 seconds (stale connection-tracking entries had made a clean graceful rollout look as if it lost requests).
- Cluster creation waits for a machine-wide lock so only one cluster is created at a time on a shared machine.
- Scripts for the runbook (create the four-node cluster, load the images, roll under load, drain the busiest node) and a cluster test that runs all four operations and records the error count of each.
- Two layered packages with the dependency rule enforced by import-linter, unit, policy and integration tests, ruff, mypy, hadolint, yamllint, shellcheck, kubeconform, a hash-pinned lock file and a CI workflow.
