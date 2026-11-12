"""Render README screenshots of the application from **synthetic spectra only**.

No laboratory data are loaded: the window is started with empty experimental roots and
the generated synthetic HPGe scenarios. Output: docs/images/*.png. Uses the normal
Windows display (the offscreen Qt platform has no fonts), so a window appears briefly.

    .venv\\Scripts\\python.exe scripts\\dev\\make_screenshots.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from nsw.config import PathSettings, Settings
from nsw.simulation import presets
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.main_window import (
    PAGE_BACKGROUND,
    PAGE_CALIBRATION,
    PAGE_EXPLORER,
    PAGE_PEAK_FIT,
    PAGE_PEAK_SEARCH,
    PAGE_RESOLUTION,
    MainWindow,
)

OUT = Path(__file__).resolve().parents[2] / "docs" / "images"
SPECTRUM = "hpge-isolated-peaks"


def wait(app: QApplication, done: Callable[[], bool], timeout: float = 120.0) -> None:
    end = time.monotonic() + timeout
    while not done():
        app.processEvents()
        time.sleep(0.01)
        if time.monotonic() > end:
            raise TimeoutError("the application did not finish in time")
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = create_application([])
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)  # type: ignore[method-assign,assignment]
    with tempfile.TemporaryDirectory() as tmp:
        syn = Path(tmp) / "synthetic"
        for spec in presets.hpge_scenarios():
            write_synthetic(generate(spec), syn)
        window = MainWindow(Settings(paths=PathSettings(workspace=Path(tmp) / "ws")))
        window.resize(1500, 900)
        window.show()
        window.load_synthetic(syn)
        wait(app, lambda: window.state.synthetic is not None and not window.is_busy())

        def shot(page: int, name: str) -> None:
            window.nav.setCurrentRow(page)
            wait(app, lambda: not window.is_busy())
            window.grab().save(str(OUT / f"{name}.png"))
            print("wrote", OUT / f"{name}.png")

        key = next(e.key for e in window.state.all_entries() if e.title.startswith(SPECTRUM))
        # no dashboard picture: that page prints the folder path of the loaded data
        blank = next(
            e.key for e in window.state.all_entries() if e.title.startswith("hpge-background-only")
        )
        window.explorer.select_keys([f"syn:{key}", key], 2)
        shot(PAGE_EXPLORER, "spectrum_explorer")
        window.background_page.select(key, blank)
        window.background_page.run()
        shot(PAGE_BACKGROUND, "background_subtraction")

        search = window.peak_search_page
        search.select(key)
        window.nav.setCurrentRow(PAGE_PEAK_SEARCH)
        search.search_button.click()
        wait(app, lambda: not window.is_busy() and search.search_button.isEnabled())
        shot(PAGE_PEAK_SEARCH, "peak_search")

        fit = window.peak_fit_page
        window.nav.setCurrentRow(PAGE_PEAK_FIT)
        fit.select(key)
        wait(app, lambda: not window.is_busy())
        fit.fit_all_button.click()
        wait(app, lambda: not window.is_busy() and not fit._busy)
        shot(PAGE_PEAK_FIT, "peak_fitting")
        window.calibration_page.select(key)
        shot(PAGE_CALIBRATION, "energy_calibration")
        window.resolution_page.select(key)
        window.resolution_page.run()
        shot(PAGE_RESOLUTION, "resolution_analysis")
        window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
