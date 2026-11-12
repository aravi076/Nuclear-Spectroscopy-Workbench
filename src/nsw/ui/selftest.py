"""End-to-end self-test of an installed NSW (``nsw --self-test FOLDER``).

Runs the main workflow on generated synthetic spectra inside the real application
window: load, peak search, peak fits, efficiency curve, activity, nuclide candidates,
project file (create, save, reopen with reproduced fits), PDF report, SVG figure. It
writes only into FOLDER and prints one line per step. Exit status 0 means every step
worked; the steps are also written to FOLDER/selftest.log (a windowed executable has
no console). Its purpose is to check a packaged executable on a clean machine, where
the test suite is not available.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QApplication

from nsw.config import PathSettings, Settings
from nsw.services import peaks as peaks_service
from nsw.services import selftest_data
from nsw.ui.main_window import PAGE_IDENTIFICATION, MainWindow


def _wait(app: QApplication, done: Callable[[], bool], timeout: float = 120.0) -> None:
    end = time.monotonic() + timeout
    while not done():
        app.processEvents()
        time.sleep(0.01)
        if time.monotonic() > end:
            raise TimeoutError("the application did not finish in time")


def run(app: QApplication, folder: Path) -> int:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    steps: list[tuple[str, bool, str]] = []

    log = (folder / "selftest.log").open("w", encoding="utf-8")

    def say(line: str) -> None:
        # a windowed executable has no console: the log file is the record
        log.write(line + "\n")
        log.flush()
        if sys.stdout is not None:
            try:
                print(line, flush=True)
            except (OSError, UnicodeError):
                pass

    def step(name: str, ok: bool, detail: str = "") -> None:
        steps.append((name, ok, detail))
        say(f"[{'ok' if ok else 'FAILED'}] {name}{': ' + detail if detail else ''}")

    window: MainWindow | None = None
    try:
        synthetic = selftest_data.write_source_spectra(folder / "synthetic")
        step("generate synthetic spectra", True, str(synthetic))
        window = MainWindow(Settings(paths=PathSettings(workspace=folder)))
        window.show()
        window.load_synthetic(synthetic)
        _wait(app, lambda: window is not None and window.state.synthetic is not None
              and not window.is_busy())  # fmt: skip
        entries = {e.title.split(".")[0]: e for e in window.state.synthetic_entries()}
        step("load spectra", len(entries) == 2, ", ".join(sorted(entries)))

        for name in ("syn-cal-source", "syn-x-sample"):
            spectrum = entries[name].spectrum
            found = peaks_service.search(
                spectrum, peaks_service.PeakSearchSettings(fwhm_channels=(4.0, 8.0))
            )
            window.state.set_peak_search(found)
            fits = peaks_service.fit_all(spectrum, found, peaks_service.FitSettings())
            for item in fits:
                if item.result is not None:
                    window.state.store_fit(spectrum.data_sha256, item.result)
            n_ok = sum(1 for f in window.state.fits(spectrum.data_sha256) if f.ok)
            step(f"peak search and fits: {name}", n_ok >= 3, f"{n_ok} acceptable fits")

        eff = window.efficiency_page
        eff.select(entries["syn-cal-source"].key)
        eff.run()
        step("efficiency curve", eff.curve is not None, eff.status.text())
        act = window.activity_page
        act.select(entries["syn-x-sample"].key)
        act.run()
        step("activity", act.result is not None, act.status.text())
        nid = window.identification_page
        nid.select(entries["syn-x-sample"].key)
        nid.run()
        first = nid.result.candidates[0].nuclide if nid.result and nid.result.candidates else ""
        step("nuclide candidates", first == "SYN-X", f"first candidate {first or 'none'}")

        project = folder / "selftest.nswproj"
        project.unlink(missing_ok=True)
        ok = window.new_project(project, "NSW self-test")
        report = window.save_session() if ok else None
        step("project save", report is not None, report.text() if report else "")
        window.close()
        window = MainWindow(Settings(paths=PathSettings(workspace=folder)))
        reopened = window.open_project(project)
        problems = window.project_page.problems.toPlainText()
        step("project reopen", reopened and not problems, window.project_page.status_label.text())

        window.nav.setCurrentRow(PAGE_IDENTIFICATION)  # report of its spectrum
        window.identification_page.select(entries["syn-x-sample"].key)
        pdf = folder / "selftest-report.pdf"
        svg = folder / "selftest-figure.svg"
        for path in (pdf, svg):
            path.unlink(missing_ok=True)
        out_pdf = window.export_report(pdf)
        out_svg = window.export_figure(svg)
        step("PDF report", out_pdf is not None and pdf.stat().st_size > 10_000, str(pdf))
        step("SVG figure", out_svg is not None and svg.stat().st_size > 1_000, str(svg))
    except Exception as exc:  # report any failure as a failed step
        step("unexpected error", False, f"{type(exc).__name__}: {exc}")
    finally:
        if window is not None:
            window.close()
    failed = [s for s in steps if not s[1]]
    say(f"self-test: {len(steps) - len(failed)} of {len(steps)} steps passed")
    log.close()
    return 1 if failed else 0
