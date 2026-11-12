"""Render the main pages at 1366×768 and 1920×1080 from synthetic spectra (U8 aid).

    .venv\\Scripts\\python.exe scripts\\ux_screenshots.py OUTPUT_FOLDER

Uses the real Windows display (a window appears briefly). For each size it records
whether the window's minimum size fits the screen size and saves screenshots of the
Spectrum Explorer (regions, coloured candidates, fit, workflow panel), the Nuclear Data
Explorer (reference lines) and the Energy Calibration page (suggested assignments).
This is an aid to the manual walk-through, not a replacement for it.
"""

from __future__ import annotations

import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from nsw.config import PathSettings, Settings
from nsw.simulation import sources
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.main_window import (
    PAGE_CALIBRATION,
    PAGE_EXPLORER,
    PAGE_NUCLEAR_DATA,
    MainWindow,
)
from nsw.ui.workflow_panel import ADVANCED

SIZES = ((1366, 768), (1920, 1080))


def wait(app: QApplication, done: Callable[[], bool], timeout: float = 120.0) -> None:
    end = time.monotonic() + timeout
    while not done():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("operation did not finish")
        time.sleep(0.02)


def settle(app: QApplication, seconds: float = 0.4) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/ux")
    out.mkdir(parents=True, exist_ok=True)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)  # type: ignore[method-assign]
    app = create_application([])
    lines = []
    with tempfile.TemporaryDirectory() as tmp:
        syn = Path(tmp) / "synthetic"
        for spec in sources.source_scenarios():
            write_synthetic(generate(spec), syn)
        settings = Settings(paths=PathSettings(experimental_roots=(), workspace=Path(tmp)))
        for width, height in SIZES:
            w = MainWindow(settings)
            w.resize(width, height)
            w.show()
            w.load_synthetic(syn)
            wait(app, lambda w=w: w.state.synthetic is not None and not w.is_busy())
            hint = w.minimumSizeHint()
            fits = hint.width() <= width and hint.height() <= height
            lines.append(
                f"{width}×{height}: minimum size {hint.width()}×{hint.height()} — "
                + ("fits" if fits else "DOES NOT FIT")
            )
            entry = next(e for e in w.state.synthetic_entries() if "syn-x" in e.title)
            w.nav.setCurrentRow(PAGE_EXPLORER)
            w.explorer.select_keys([f"syn:{entry.key}"], tab=2)
            settle(app)
            controller = w.explorer.roi_controller
            found = w.state.peak_search(entry.key)
            if found and found.candidates:
                c = found.candidates[0].channel
                controller.create(int(c - 15), int(c + 15))
                controller.fit_selected()
            w.explorer.info_tabs.setCurrentIndex(w.explorer.info_tabs.count() - 1)
            settle(app)
            w.grab().save(str(out / f"explorer-{width}x{height}.png"))
            w.workflow.set_mode(ADVANCED)
            settle(app)
            w.grab().save(str(out / f"explorer-advanced-{width}x{height}.png"))
            w.nav.setCurrentRow(PAGE_NUCLEAR_DATA)
            page = w.nuclear_data_page
            page.select_spectrum(entry.key)
            page.select_nuclides(["SYN-X"])
            page.show_overlay()
            settle(app)
            w.grab().save(str(out / f"nuclear-data-{width}x{height}.png"))
            w.nav.setCurrentRow(PAGE_CALIBRATION)
            settle(app)
            w.grab().save(str(out / f"calibration-{width}x{height}.png"))
            w.close()
    (out / "sizes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
