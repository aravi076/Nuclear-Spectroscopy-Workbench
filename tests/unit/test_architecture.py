"""The layering rule from docs/architecture.md, enforced by inspecting imports."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "nsw"

# package -> nsw sub-packages it may import
ALLOWED: dict[str, set[str]] = {
    "core": {"core"},
    "detectors": {"core", "detectors"},
    "io": {"core", "io"},
    "analysis": {"core", "detectors", "analysis"},
    "simulation": {"core", "detectors", "simulation"},
    "database": {"core", "detectors", "database"},
    "reporting": {"core", "detectors", "analysis", "reporting"},
    "services": {
        "core",
        "detectors",
        "io",
        "analysis",
        "simulation",
        "database",
        "reporting",
        "services",
        "config",
    },
    "ui": {"core", "detectors", "services", "ui", "config"},
}
# third-party modules the scientific layers must never import
FORBIDDEN_IN_SCIENCE = {"PySide6", "pyqtgraph", "sqlite3", "matplotlib", "yaml"}
SCIENCE = {"core", "detectors", "io", "analysis", "simulation"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("package", sorted(ALLOWED))
def test_layer_dependencies(package: str) -> None:
    problems: list[str] = []
    for path in sorted((SRC / package).rglob("*.py")):
        for mod in _imports(path):
            parts = mod.split(".")
            if parts[0] == "nsw" and len(parts) > 1 and parts[1] not in ALLOWED[package]:
                problems.append(f"{path.relative_to(SRC)} imports {mod}")
            if package in SCIENCE and parts[0] in FORBIDDEN_IN_SCIENCE:
                problems.append(f"{path.relative_to(SRC)} imports {mod}")
    assert not problems, "\n".join(problems)
