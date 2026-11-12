"""Slow modules stay out of the application's start-up (recommendation R1, TD-053)."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("PySide6")

NOT_AT_STARTUP = ("scipy.stats", "scipy.signal", "matplotlib", "pyqtgraph.exporters", "pandas")


def test_main_window_import_does_not_load_slow_modules() -> None:
    code = (
        "import sys, nsw.ui.main_window\n"
        f"print(','.join(m for m in {NOT_AT_STARTUP!r} if m in sys.modules))\n"
    )
    out = subprocess.run(  # noqa: S603 - own interpreter, fixed code
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert out == "", f"loaded at start-up: {out}"
