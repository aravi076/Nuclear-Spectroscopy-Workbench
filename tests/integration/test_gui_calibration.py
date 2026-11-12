"""GUI workflow tests for the Energy Calibration page (offscreen)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from scipy.special import ndtr

from nsw.config import PathSettings, Settings
from nsw.core import CalibrationStatus
from nsw.services import calibration as cal_service
from nsw.simulation import presets
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.calibration_page import ASSIGN_HINT, NO_PEAKS_HINT
from nsw.ui.main_window import PAGE_CALIBRATION, MainWindow
from tests.fixtures import builders as b
from tests.integration.test_gui_peaks import fit, key_of, search
from tests.integration.test_gui_viewer import wait_until

pytestmark = pytest.mark.gui

X = np.arange(1, 1025)
PEAKS = (150.0, 400.0, 800.0)  # channels; "energies" below are arbitrary test values
SRC = "arbitrary test value (not nuclear data)"


def spectrum_counts(shift: float = 0.0, scale: int = 1) -> list[int]:
    y = np.full(X.size, 20.0 * scale)
    for c in PEAKS:
        y += 20000 * scale * (ndtr((X + 0.5 - c - shift) / 2.0) - ndtr((X - 0.5 - c - shift) / 2.0))
    return np.round(y).astype(int).tolist()


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


@pytest.fixture
def window(app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "experimental"
    root.mkdir()
    times = (
        "Tue Mar 10 10:00:00 GMT+0530 2026",
        "Tue Mar 10 12:00:00 GMT+0530 2026",
        "Tue Mar 10 14:00:00 GMT+0530 2026",
    )
    (root / "run A.csv").write_bytes(
        b.lynx_csv(spectrum_counts(), start=times[0], live=1000, real=1001.5)
    )
    (root / "run B.csv").write_bytes(
        b.lynx_csv(spectrum_counts(scale=2), start=times[1], live=2000, real=2003.5)
    )
    (root / "run C shifted.csv").write_bytes(
        b.lynx_csv(spectrum_counts(shift=0.6), start=times[2], live=1000, real=1002.5)
    )
    syn = tmp_path / "synthetic"
    specs = {s.synthetic_id: s for s in presets.hpge_scenarios()}
    write_synthetic(generate(specs["hpge-isolated-peaks-no-continuum"]), syn)
    settings = Settings(paths=PathSettings(experimental_roots=(root,), workspace=tmp_path / "ws"))
    w = MainWindow(settings)
    w.show()
    w.load_experimental(root)
    wait_until(app, lambda: w.state.experimental is not None and not w.is_busy())
    w.load_synthetic(syn)
    wait_until(app, lambda: w.state.synthetic is not None and not w.is_busy())
    yield w
    w.close()


def fit_all(app: QApplication, w: MainWindow, title: str) -> str:
    search(app, w, title)
    w.peak_fit_page.select(key_of(w, title))
    fit(app, w, "fit_all_button")
    return key_of(w, title)


def test_page_needs_fitted_peaks(app: QApplication, window: MainWindow) -> None:
    page = window.calibration_page
    assert window.nav.item(PAGE_CALIBRATION).text() == "Energy Calibration"
    page.select(key_of(window, "run A.csv"))
    assert page.hint.text() == NO_PEAKS_HINT and page.peak_table.rowCount() == 0
    assert not page.fit_button.isEnabled() and not page.export_button.isEnabled()
    fit_all(app, window, "run A.csv")
    app.processEvents()
    assert page.hint.text() == ASSIGN_HINT and page.peak_table.rowCount() == 3
    assert page.fit_button.isEnabled()


def test_synthetic_calibration_is_verified_and_recovers_the_generator_scale(
    app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    key = fit_all(app, window, "hpge-isolated-peaks-no-continuum")
    page = window.calibration_page
    page.select(key)
    assert page.peak_table.rowCount() == 5
    for energy in (100.0, 300.0, 600.0, 1000.0, 1500.0):  # generator line energies
        row = page.row_near((energy - 0.5) / 0.25)
        page.set_reference(row, energy, 0.0, "synthetic ground truth", f"test-{energy:g}")
    page.fit_button.click()
    f = page.fit
    assert f is not None and f.n_points == 5 and f.ndf == 3
    pulls = (f.coefficients - np.array([0.5, 0.25])) / f.coefficient_uncertainties
    assert np.all(np.abs(pulls) < 4)
    assert page.record is not None and page.record.status is CalibrationStatus.VERIFIED
    assert page.status.text().startswith("SYNTHETIC") and "verified" in page.status.text()
    rows = dict(page.result_table.rows())
    assert rows["Status"].startswith("SYNTHETIC") and "extrapolated" in rows["Valid range"]
    assert rows["gain c1 (keV/channel)"].startswith("0.25")
    assert page.point_table.rowCount() == 5
    np.testing.assert_allclose(page.plot.residuals(), f.residuals_kev)
    assert page.history_table.rowCount() == 1
    assert page.history_table.column_values("Status") == ["verified"]
    assert page.history_table.column_values("Data") == ["synthetic"]

    page.order_button.click()
    assert page.comparison is not None and not page.comparison.justified
    assert "not significant" in page.status.text()

    out = window.export_calibration(tmp_path / "cal.csv")
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "# status: SYNTHETIC" in text
    c1 = next(ln for ln in text.splitlines() if ln.startswith("# c1 = "))
    assert float(c1.split()[3]) == f.coefficients[1]  # full precision
    assert text.count("synthetic ground truth") == 5
    assert (
        page.suggested_filename()
        == "hpge-isolated-peaks-no-continuum.nswsyn_energy_calibration.csv"
    )

    # the verified record gives a synthetic spectrum fitted energies
    s = page.current_spectrum()
    assert s is not None
    calibrated = cal_service.apply_calibration(s, page.record)
    assert calibrated.energies()[399] == pytest.approx(100.5, abs=0.01)  # channel 400


def test_experimental_calibration_stays_provisional(app: QApplication, window: MainWindow) -> None:
    key = fit_all(app, window, "run A.csv")
    page = window.calibration_page
    page.select(key)
    for row, energy in enumerate((75.0, 200.0, 400.0)):  # E = 0.5 ch exactly
        page.set_reference(row, energy, 0.01, SRC)
    page.run()
    f = page.fit
    assert f is not None and f.verifiable  # statistically fine ...
    assert f.coefficients[1] == pytest.approx(0.5, abs=1e-3)
    # ... but never verified for experimental data at this stage
    assert page.record is not None and page.record.status is CalibrationStatus.UNVERIFIED
    assert "PROVISIONAL" in page.status.text() and "laboratory confirms" in page.status.text()
    assert "Not verified" in page.record.note
    assert page.history_table.column_values("Status") == ["unverified (provisional)"]
    s = page.current_spectrum()
    assert s is not None
    still_channels = cal_service.apply_calibration(s, page.record)
    assert still_channels.calibration_status is CalibrationStatus.UNVERIFIED
    with pytest.raises(Exception, match="use channel numbers"):
        still_channels.energies()


def test_refusals_are_shown_in_the_page(app: QApplication, window: MainWindow) -> None:
    key = fit_all(app, window, "run A.csv")
    page = window.calibration_page
    page.select(key)
    page.run()
    assert "no peak is ticked" in page.status.text() and page.fit is None
    page.set_reference(0, 75.0, 0.01, SRC)
    page.run()
    assert "cannot determine both gain and offset" in page.status.text()
    page.set_reference(1, 200.0, 0.01, "   ")
    page.run()
    assert "has no source" in page.status.text() and page.fit is None
    page.set_reference(1, 200.0, 0.01, SRC)
    page.peak_table.item(1, 6).setText("two hundred")
    page.run()
    assert "must be numbers" in page.status.text()
    page.set_reference(1, 200.0, 0.01, SRC)
    page.run()  # two points: fits, but cannot be checked
    assert page.fit is not None and page.fit.ndf == 0
    assert dict(page.result_table.rows())["Probability"] == "not defined (no redundancy)"
    page.order_box.setCurrentIndex(1)
    page.run()
    assert "needs at least 3" in page.status.text() and page.fit is None
    assert not page.export_button.isEnabled() and page.point_table.rowCount() == 0


def test_stability_of_a_peak_position_across_runs(app: QApplication, window: MainWindow) -> None:
    page = window.calibration_page
    page.stability_channel.setValue(400.0)
    page.run_stability()
    assert page.stability is None and "fewer than two" in page.stability_status.text()
    fit_all(app, window, "run A.csv")
    fit_all(app, window, "run B.csv")
    page.run_stability()
    r = page.stability
    assert r is not None and r.n == 2 and r.stable and r.max_deviation < 0.02
    assert page.stability_table.rowCount() == 2
    fit_all(app, window, "run C shifted.csv")
    page.run_stability()
    r = page.stability
    assert r is not None and r.n == 3 and not r.stable
    assert r.max_deviation == pytest.approx(0.4, abs=0.1)  # 0.6 channel shift vs weighted mean
    assert "not constant" in page.stability_status.text()
