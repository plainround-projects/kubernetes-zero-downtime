"""Render the Kustomize overlays once per session so the policy tests read plain dictionaries."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
Doc = dict[str, Any]


def render(overlay: str) -> list[Doc]:
    """kubectl kustomize manifests/overlays/<overlay>, parsed into one dictionary per resource."""
    output = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "manifests" / "overlays" / overlay)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(output) if doc]


def workloads_of(docs: list[Doc]) -> list[Doc]:
    """The Deployments, StatefulSets and DaemonSets among rendered resources."""
    return [doc for doc in docs if doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet"}]


def named(docs: list[Doc], kind: str, name: str) -> Doc:
    """The one resource of a kind and name."""
    return next(doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name)


@pytest.fixture(scope="session")
def production() -> list[Doc]:
    return render("production")


@pytest.fixture(scope="session")
def overlays() -> dict[str, list[Doc]]:
    return {name: render(name) for name in ("naive", "graceful", "rolling-tuned", "production")}
