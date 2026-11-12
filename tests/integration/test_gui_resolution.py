"""GUI workflow tests for the Resolution Analysis page and fixed-shape fits (offscreen)."""

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
from nsw.services import resolution as res_service
from nsw.simulation import presets
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.main_window import PAGE_RESOLUTION, MainWindow
from nsw.ui.resolution_page import NO_PEAKS_HINT, USE_HINT
from tests.fixtures import builders as b
from tests.integration.test_gui_calibration import fit_all
from tests.integration.test_gui_peaks import fit, key_of
from tests.integration.test_gui_viewer import wait_until

pytestmark = pytest.mark.gui

X = np.arange(1, 2049)
# widths follow FWHM^2 = 4 + 0.01 ch; the peak at 1200 is deliberately twice as wide
LINES = {200.0: 1.0, 600.0: 1.0, 1200.0: 2.0, 1800.0: 1.0}


def counts() -> list[int]:
    y = np.full(X.size, 20.0)
    for c, factor in LINES.items():
        sigma = factor * np.sqrt(4 + 0.01 * c) / 2.3548200450309493
        y += 40000 * (ndtr((X + 0.5 - c) / sigma) - ndtr((X - 0.5 - c) / sigma))
    return np.round(y).astype(int).tolist()


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


@pytest.fixture
def window(app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "experimental"
    root.mkdir()
    (root / "run.csv").write_bytes(
        b.lynx_csv(counts(), start="Tue Mar 10 10:00:00 GMT+0530 2026", live=1000, real=1001.5)
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


def test_experimental_resolution_in_channels_is_provisional(
    app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    page = window.resolution_page
    assert window.nav.item(PAGE_RESOLUTION).text() == "Resolution Analysis"
    page.select(key_of(window, "run.csv"))
    assert page.hint.text() == NO_PEAKS_HINT and not page.fit_button.isEnabled()
    fit_all(app, window, "run.csv")
    app.processEvents()
    assert page.hint.text() == USE_HINT and page.peak_table.rowCount() == 4
    assert page.variable_box.count() == 1  # no verified calibration: channels only
    assert page.variable_box.currentData() == "channel"

    page.fit_button.click()  # all four peaks, including the broadened one
    f = page.fit
    assert f is not None and f.poor_fit
    assert "not consistent with the model" in page.status.text()
    broad = int(np.argmax(np.abs(f.normalised_residuals)))
    assert f.points[broad].x == pytest.approx(1200.0, abs=0.1)  # the culprit stands out

    page.set_used(2, False)  # the analyst removes the broadened peak
    page.run()
    f = page.fit
    assert f is not None and not f.poor_fit and len(f.points) == 3
    assert abs(f.coefficients[0] - 4.0) < 4 * f.coefficient_uncertainties[0]
    assert abs(f.coefficients[1] - 0.01) < 4 * f.coefficient_uncertainties[1]
    assert "PROVISIONAL" in page.status.text() and "channel units" in page.status.text()
    rows = dict(page.result_table.rows())
    assert "FWHM in channels" in rows["Model"] and rows["c"] == "0 (term not requested)"
    assert page.point_table.rowCount() == 3 and page.plot.model_curve().size == 200
    assert res_service.verification(page.current_spectrum(), f)[0] is False  # type: ignore[arg-type]

    out = window.export_resolution(tmp_path / "res.csv")
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "PROVISIONAL" in text and "x: channel" in text and "np.float64" not in text
    assert page.suggested_filename() == "run_resolution.csv"

    page.set_used(0, False)
    page.set_used(1, False)
    page.run()
    assert "refused" in page.status.text() and page.fit is None
    assert not page.export_button.isEnabled()


def test_synthetic_resolution_in_kev_is_verified(app: QApplication, window: MainWindow) -> None:
    key = fit_all(app, window, "hpge-isolated-peaks-no-continuum")
    page = window.resolution_page
    page.select(key)
    assert page.peak_table.rowCount() == 5 and page.variable_box.count() == 2
    assert page.variable_box.currentData() == "energy_keV"
    page.run()
    f = page.fit
    assert f is not None and f.unit == "keV" and not f.poor_fit
    # generator truth: FWHM^2 = 0.75 + 0.0025 E
    assert abs(f.coefficients[0] - 0.75) < 4 * f.coefficient_uncertainties[0]
    assert abs(f.coefficients[1] - 0.0025) < 4 * f.coefficient_uncertainties[1]
    assert page.status.text().startswith("SYNTHETIC — verified")
    rows = dict(page.result_table.rows())
    assert "% of the energy" in rows["FWHM at E 100.0"]
    page.variable_box.setCurrentIndex(1)
    page.run()
    assert page.fit is not None and page.fit.unit == "channels"
    assert "channel units" in page.status.text()


def test_fixed_shape_options_on_the_fit_page(app: QApplication, window: MainWindow) -> None:
    page = window.peak_fit_page
    page.select(key_of(window, "run.csv"))
    page.first_spin.setValue(180)
    page.last_spin.setValue(220)
    page.centroids_edit.setText("200")
    page.fwhm_edit.setText("2.449")  # sqrt(4 + 0.01 * 200)
    page.fix_position_check.setChecked(True)
    page.fix_width_check.setChecked(True)
    fit(app, window)
    r = page.last_result
    assert r is not None and r.ok
    p = r.peaks[0]
    assert p.centroid == 200.0 and p.centroid_unc == 0.0 and p.fwhm_unc == 0.0
    assert p.area == pytest.approx(40000, rel=0.01)
    assert any("held fixed" in text for _, text in page.summary_table.rows())
    # a fixed width is not a resolution measurement: it is not offered on the resolution page
    window.resolution_page.select(key_of(window, "run.csv"))
    assert window.resolution_page.peak_table.rowCount() == 0
