"""Keep future V2 implementations independent of UI and evaluation answers."""

from __future__ import annotations

import ast
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "domain": set(),
    "data_sources": {"domain", "ingest"},
    "network": {"domain"},
    "detectors": {"domain", "network"},
    "evidence": {"domain"},
    "scoring": {"domain", "evidence"},
    "business": {"domain"},
    "replay": {"domain", "network"},
    "benchmark": {"domain", "network", "detectors", "evidence", "scoring", "business", "replay", "simulation"},
}
UI_MODULES = {"app", "streamlit", "folium", "pydeck", "quality_mvp"}


def import_targets(source: str, package: str) -> set[str]:
    targets = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            if node.level:
                base = resolve_name(base, package)
            targets.update(base + "." + alias.name for alias in node.names)
    return targets


def violations(source: str, layer: str, package: str) -> list[str]:
    rejected = []
    for target in import_targets(source, package):
        parts = target.split(".")
        imported_layer = parts[1] if len(parts) > 1 else "*"
        if (
            parts[0] in UI_MODULES
            or (parts[0] == "duckdb" and layer != "data_sources")
            or (parts[0] == "src" and imported_layer not in ALLOWED[layer] | {layer})
        ):
            rejected.append(target)
    return sorted(rejected)


@pytest.mark.parametrize("layer", ALLOWED)
def test_v2_layer_imports_follow_dependency_boundaries(layer: str) -> None:
    location = ROOT / "src" / layer
    files = sorted(location.rglob("*.py")) if location.is_dir() else [location.with_suffix(".py")]
    for path in files:
        package = ".".join(path.parent.relative_to(ROOT).parts)
        errors = violations(path.read_text(encoding="utf-8"), layer, package)
        assert not errors, f"{path.relative_to(ROOT)} has forbidden imports: {errors}"


@pytest.mark.parametrize("statement", [
    "import src.benchmark",
    "from src import benchmark",
    "from src.benchmark import metrics",
    "from .. import benchmark",
    "from ..benchmark import metrics",
    "from src import *",
    "import src",
    "import streamlit as st",
    "from duckdb import connect",
    "from src.storage import initialize_database",
    "from src.simulation import generate_simulated_records",
])
def test_guard_catches_answer_and_storage_coupling(statement: str) -> None:
    assert violations(statement, "detectors", "src.detectors")


def test_v2_skeleton_imports_without_third_party_packages() -> None:
    modules = ["src." + layer for layer in ALLOWED]
    script = "import importlib; " + "; ".join(f"importlib.import_module({module!r})" for module in modules)
    completed = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
