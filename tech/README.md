# Technology notes

These notes explain every technology used in this repository, from the first principle up, for a reader who has never met any of them. Each file has the same nine headings: what it is, why it is used here, where it appears, how it works underneath, a walkthrough of this repository's own files, commands to try, common mistakes, questions to check yourself, and further reading.

## Ten-minute shortlist

If you only read three files, read these, in this order:

1. [`rolling-updates.md`](rolling-updates.md): why a naive rolling update drops requests and the three settings that fix it.
2. [`loadgen.md`](loadgen.md): the measuring instrument, and why its error count can be trusted.
3. [`autoscaling.md`](autoscaling.md): how the replica count follows load, and what the percentage is a percentage of.

## Reading order

| # | File | What it covers |
| --- | --- | --- |
| 1 | [`python.md`](python.md) | The language, the web service's domain and use cases, and the load generator's domain and settings. |
| 2 | [`flask.md`](flask.md) | The three web routes. |
| 3 | [`gunicorn.md`](gunicorn.md) | The server, its start-up order and its graceful shutdown. |
| 4 | [`loadgen.md`](loadgen.md) | The fixed-rate load generator and its control API. |
| 5 | [`environment.md`](environment.md) | How settings reach the programs. |
| 6 | [`docker.md`](docker.md) | The two images: Dockerfile, `.dockerignore`, `.env.example`. |
| 7 | [`yaml.md`](yaml.md) | The file format of almost everything else, and yamllint. |
| 8 | [`kubernetes.md`](kubernetes.md) | The base manifests, line by line. |
| 9 | [`kustomize.md`](kustomize.md) | Base and overlays: each step of the lesson is one overlay. |
| 10 | [`rolling-updates.md`](rolling-updates.md) | Steps 1 to 3: probes, graceful shutdown, the rollout strategy. |
| 11 | [`autoscaling.md`](autoscaling.md) | Step 4: the HorizontalPodAutoscaler. |
| 12 | [`disruption-budget.md`](disruption-budget.md) | Steps 5 and 6: the budget, the spread and draining a node. |
| 13 | [`kubectl.md`](kubectl.md) | The client commands used everywhere. |
| 14 | [`k3d.md`](k3d.md) | The four-node local cluster. |
| 15 | [`bash.md`](bash.md) | Every script, line by line. |
| 16 | [`make.md`](make.md) | The Makefile targets. |
| 17 | [`toml.md`](toml.md) | `pyproject.toml`: the packages and the tool settings. |
| 18 | [`pip.md`](pip.md) | Virtual environment, lock files and hashes. |
| 19 | [`pytest.md`](pytest.md) | The test runner and coverage. |
| 20 | [`tests-unit.md`](tests-unit.md) | The unit tests. |
| 21 | [`tests-policy.md`](tests-policy.md) | Rules checked on the rendered manifests. |
| 22 | [`tests-integration.md`](tests-integration.md) | The real server command and the real load generator. |
| 23 | [`tests-e2e.md`](tests-e2e.md) | The Done-when proofs on the cluster. |
| 24 | [`ruff.md`](ruff.md) | Linting and formatting. |
| 25 | [`mypy.md`](mypy.md) | Type checking. |
| 26 | [`import-linter.md`](import-linter.md) | The layer rules and the independence rule. |
| 27 | [`shellcheck.md`](shellcheck.md) | Checking the scripts. |
| 28 | [`hadolint.md`](hadolint.md) | Checking the Dockerfile. |
| 29 | [`kubeconform.md`](kubeconform.md) | Checking the manifests against the schemas. |
| 30 | [`github-actions.md`](github-actions.md) | The CI workflow. |
| 31 | [`git.md`](git.md) | `.gitignore`, `.editorconfig`, license and history. |

All versions named here are the ones pinned in this repository (Python 3.13, Flask 3.1, gunicorn 25.3, k3d 5.9.0, Kubernetes 1.35.8, kubeconform 0.8.0, hadolint 2.12.0).
