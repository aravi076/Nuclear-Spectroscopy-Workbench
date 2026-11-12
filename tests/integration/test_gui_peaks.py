"""GUI workflow tests for the Peak Analysis and Peak Fitting pages (offscreen)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from scipy.special import ndtr

from nsw.config import PathSettings, Settings
from nsw.services.export import CALIBRATION_WARNING
from nsw.simulation import presets
from nsw.simulation.generator import SyntheticResult, generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.main_window import PAGE_PEAK_FIT, PAGE_PEAK_SEARCH, MainWindow
from nsw.ui.peak_fit_page import PeakFitPage
from nsw.ui.peak_search_page import PeakSearchPage, parse_numbers
from tests.fixtures import builders as b
from tests.integration.test_gui_viewer import wait_until

pytestmark = pytest.mark.gui

X = np.arange(1, 513)
# "measured" spectrum: one peak of 20000 counts at channel 200.3 (sigma 2) on 30 counts/channel
REAL = np.round(
    20000 * (ndtr((X + 0.5 - 200.3) / 2.0) - ndtr((X - 0.5 - 200.3) / 2.0)) + 30
).astype(int)
BLANK = np.full(512, 60)
T_S = "Tue Mar 10 09:15:27 GMT+0530 2026"
T_B = "Tue Mar 10 16:00:00 GMT+0530 2026"


def synthetic(name: str) -> SyntheticResult:
    specs = {s.synthetic_id: s for s in presets.hpge_scenarios()}
    return generate(specs[name])


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


@pytest.fixture
def window(app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "experimental"
    root.mkdir()
    (root / "source 1000sec.csv").write_bytes(
        b.lynx_csv(REAL.tolist(), start=T_S, live=1000, real=1010.5)
    )
    (root / "blank 2000sec.csv").write_bytes(
        b.lynx_csv(BLANK.tolist(), start=T_B, live=2000, real=2001.5)
    )
    syn = tmp_path / "synthetic"
    for name in ("hpge-isolated-peaks-no-continuum", "hpge-overlapping-doublet"):
        write_synthetic(synthetic(name), syn)
    settings = Settings(paths=PathSettings(experimental_roots=(root,), workspace=tmp_path / "ws"))
    w = MainWindow(settings)
    w.show()
    w.load_experimental(root)
    wait_until(app, lambda: w.state.experimental is not None and not w.is_busy())
    w.load_synthetic(syn)
    wait_until(app, lambda: w.state.synthetic is not None and not w.is_busy())
    yield w
    w.close()


def key_of(w: MainWindow, title: str) -> str:
    return next(e.key for e in w.state.all_entries() if e.title.startswith(title))


def search(app: QApplication, w: MainWindow, title: str, scales: str = "4, 8") -> PeakSearchPage:
    page = w.peak_search_page
    page.select(key_of(w, title))
    page.scales_edit.setText(scales)
    page.search_button.click()
    wait_until(app, lambda: not w.is_busy() and page.search_button.isEnabled())
    return page


def fit(app: QApplication, w: MainWindow, button: str = "fit_button") -> PeakFitPage:
    page = w.peak_fit_page
    getattr(page, button).click()
    wait_until(app, lambda: not w.is_busy() and not page._busy)
    return page


def test_garbage_collection_is_confined_to_the_gui_thread(app: QApplication) -> None:
    import gc

    from PySide6.QtCore import QTimer

    from nsw.ui.app import GC_INTERVAL_MS

    assert not gc.isenabled()  # no automatic collection in whichever thread allocates
    timer = app.property("nsw_gc_timer")
    assert isinstance(timer, QTimer) and timer.isActive() and timer.interval() == GC_INTERVAL_MS
    assert timer.thread() is app.thread()
    assert create_application([]) is app and app.property("nsw_gc_timer") is timer  # idempotent


def test_pages_exist_and_start_empty(window: MainWindow) -> None:
    assert window.nav.item(PAGE_PEAK_SEARCH).text() == "Peak Analysis"
    assert window.nav.item(PAGE_PEAK_FIT).text() == "Peak Fitting"
    page = window.peak_fit_page
    assert page.spectrum_box.count() == 4
    assert not page.fit_button.isEnabled()  # no peak position entered
    assert not page.fit_all_button.isEnabled() and not page.export_button.isEnabled()
    assert window.peak_search_page.result is None
    assert page.export_button.text() == "Export fitted peaks as CSV…"  # text not mangled
    assert parse_numbers("4, 8; 12  16") == (4.0, 8.0, 12.0, 16.0)


def test_search_on_synthetic_spectrum_marks_and_lists_candidates(
    app: QApplication, window: MainWindow
) -> None:
    truth = synthetic("hpge-isolated-peaks-no-continuum").truth["lines"]
    page = search(app, window, "hpge-isolated-peaks-no-continuum")
    assert page.result is not None and len(page.result.candidates) == 5
    channels = [float(c) for c in page.table.column_values("Channel")]
    for line, ch, marker in zip(truth, channels, page.plot.markers(), strict=True):
        assert abs(ch - line["centroid_channel"]) < 0.2
        assert abs(marker - line["centroid_channel"]) < 0.2
    assert "SYNTHETIC" in page.status.text() and "5 candidates" in page.status.text()
    assert "not confirmed peaks" in page.messages.toPlainText()
    assert page.plot.x_axis_label() == "Channel"  # channel axis even for synthetic data
    # the result is kept when coming back to the spectrum
    page.select(key_of(window, "source 1000sec.csv"))
    assert page.result is None and page.table.rowCount() == 0 and page.plot.markers() == []
    page.select(key_of(window, "hpge-isolated-peaks-no-continuum"))
    assert page.result is not None and page.table.rowCount() == 5


def test_search_input_errors_are_reported_not_raised(app: QApplication, window: MainWindow) -> None:
    page = window.peak_search_page
    page.select(key_of(window, "source 1000sec.csv"))
    page.scales_edit.setText("abc")
    page.run()
    assert "not started" in page.status.text() and not window.is_busy()
    page.scales_edit.setText("1")
    page.run()
    assert "at least 2 channels" in page.status.text()
    page.suggest_scales()
    assert parse_numbers(page.scales_edit.text())[0] == pytest.approx(4.7, abs=0.6)  # FWHM 4.71


def test_experimental_results_are_provisional_and_in_channels(
    app: QApplication, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    spage = search(app, window, "source 1000sec.csv")
    assert spage.result is not None and len(spage.result.candidates) == 1
    assert "Energy (keV)" not in spage.table.headers()
    assert "PROVISIONAL" in spage.status.text()
    assert spage.plot.banner.text() == CALIBRATION_WARNING

    page = window.peak_fit_page
    page.select(key_of(window, "source 1000sec.csv"))
    assert page.group_box.count() == 2 and page.fit_all_button.isEnabled()
    page.select_group(0)
    assert abs(parse_numbers(page.centroids_edit.text())[0] - 200.3) < 0.1
    fit(app, window)
    r = page.last_result
    assert r is not None and r.ok and r.method == "poisson"
    assert r.peaks[0].area == pytest.approx(20000, rel=0.01) and r.peaks[0].energy_kev is None
    assert "Energy (keV)" not in page.peak_table.headers()
    assert page.peak_table.column_values("Quality") == ["ok"]
    assert "PROVISIONAL" in page.plot.banner.text()
    assert window.statusBar().currentMessage() == "Ready."
    rows = dict(page.summary_table.rows())
    assert "PROVISIONAL" in rows["Result status"] and "Fisher" in rows["Uncertainties"]
    np.testing.assert_allclose(page.plot.curve(0), r.data)
    np.testing.assert_allclose(page.plot.curve(1), r.fitted)
    np.testing.assert_allclose(page.plot.residual_points(), r.residuals)

    out = window.export_peaks(tmp_path / "peaks.csv")
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "PROVISIONAL" in text and CALIBRATION_WARNING in text and "energy_keV" not in text
    assert text.count("\n") == sum(1 for ln in text.splitlines() if ln.startswith("#")) + 2
    assert page.suggested_filename() == "source 1000sec_peaks.csv"
    # nothing may be written into the dataset folder
    root = window.state.experimental.root  # type: ignore[union-attr]
    assert window.export_peaks(root / "peaks.csv") is None
    assert shown and "read-only experimental dataset" in shown[-1]
    assert sorted(p.name for p in root.iterdir()) == ["blank 2000sec.csv", "source 1000sec.csv"]


def test_fit_all_groups_and_multiplet_assessment_on_synthetic_doublet(
    app: QApplication, window: MainWindow
) -> None:
    search(app, window, "hpge-overlapping-doublet")
    page = window.peak_fit_page
    page.select(key_of(window, "hpge-overlapping-doublet"))
    fit(app, window, "fit_all_button")
    results = page.results()
    doublet = next(r for r in results if r.n_peaks == 2 and 2380 < r.peaks[0].centroid < 2430)
    assert doublet.ok and not doublet.poor_fit
    assert "groups fitted" in page.status.text()
    assert "SYNTHETIC" in page.plot.banner.text()
    assert "Energy (keV)" in page.peak_table.headers()  # verified synthetic-truth calibration
    assert page.export_button.isEnabled()

    group = next(
        i for i in range(page.group_box.count()) if "2 candidates" in page.group_box.itemText(i)
    )
    page.group_box.setCurrentIndex(group)
    assert page.evidence_button.isEnabled()
    fit(app, window, "evidence_button")
    assert page.last_evidence is not None and page.last_evidence.supported
    assert "not proof" in page.status.text()

    page.discard_results()
    assert page.results() == [] and not page.export_button.isEnabled()
    assert page.peak_table.rowCount() == 0 and page.plot.result() is None


def test_invalid_fit_requests_are_shown_in_the_page(app: QApplication, window: MainWindow) -> None:
    page = window.peak_fit_page
    page.select(key_of(window, "source 1000sec.csv"))
    page.first_spin.setValue(180)
    page.last_spin.setValue(220)
    page.centroids_edit.setText("300")
    fit(app, window)
    assert (
        page.status.text().startswith("Refused") and "outside the fit region" in page.status.text()
    )
    assert page.last_result is None and page.fit_button.isEnabled()
    page.centroids_edit.setText("200")
    page.fwhm_edit.setText("x")
    page.run()
    assert "not started" in page.status.text()


def test_joint_fit_requires_a_designated_background(app: QApplication, window: MainWindow) -> None:
    page = window.peak_fit_page
    page.select(key_of(window, "source 1000sec.csv"))
    page.first_spin.setValue(180)
    page.last_spin.setValue(220)
    page.centroids_edit.setText("200")
    page.fwhm_edit.setText("4.7")
    page.method_box.setCurrentIndex(page.method_box.findData("joint"))
    assert not page.fit_button.isEnabled() and page.background_box.count() == 0
    blank = next(e for e in window.state.all_entries() if e.title == "blank 2000sec.csv")
    window.state.designate_background(blank.spectrum)
    app.processEvents()
    page.select(key_of(window, "source 1000sec.csv"))
    page.centroids_edit.setText("200")
    page.method_box.setCurrentIndex(page.method_box.findData("joint"))
    assert page.fit_button.isEnabled() and page.background_box.count() == 1
    fit(app, window)
    r = page.last_result
    assert r is not None and r.method == "joint-poisson" and r.ok
    assert r.peaks[0].area == pytest.approx(20000, rel=0.01)
    # ambient background 60 counts / 2000 s -> 30 counts in 1000 s: source continuum ~ 0
    assert all(abs(value) < 3 * unc for _, value, unc in r.background)
