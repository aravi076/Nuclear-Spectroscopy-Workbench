"""GUI workflows of the Efficiency Calibration, Activity Calculation and Nuclide
Identification pages: synthetic spectra of fictitious sources, and the refusal for
experimental spectra."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from nsw.config import PathSettings, Settings
from nsw.io.reference_data import library_document
from nsw.simulation import sources
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.main_window import PAGE_PEAK_FIT, MainWindow
from tests.fixtures import builders as b
from tests.integration.test_gui_peaks import REAL, T_S, search
from tests.integration.test_gui_viewer import wait_until
from tests.unit.test_identification_service import real_like_library

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


@pytest.fixture
def window(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MainWindow]:
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: None))
    root = tmp_path / "experimental"
    root.mkdir()
    (root / "source 1000sec.csv").write_bytes(
        b.lynx_csv(REAL.tolist(), start=T_S, live=1000, real=1010.5)
    )
    syn = tmp_path / "synthetic"
    for spec in sources.source_scenarios():
        write_synthetic(generate(spec), syn)
    settings = Settings(paths=PathSettings(experimental_roots=(root,), workspace=tmp_path))
    w = MainWindow(settings)
    w.show()
    w.load_experimental(root)
    wait_until(app, lambda: w.state.experimental is not None and not w.is_busy())
    w.load_synthetic(syn)
    wait_until(app, lambda: w.state.synthetic is not None and not w.is_busy())
    yield w
    w.close()


def fit_everything(app: QApplication, w: MainWindow, title: str) -> str:
    search(app, w, title)
    key = next(e.key for e in w.state.all_entries() if e.title.startswith(title))
    page = w.peak_fit_page
    w.nav.setCurrentRow(PAGE_PEAK_FIT)
    page.select(key)
    page.fit_all_button.click()
    wait_until(app, lambda: not w.is_busy() and not page._busy)
    assert w.state.fits(key)
    return key


def test_experimental_spectra_are_refused_on_all_three_pages(window: MainWindow) -> None:
    key = window.state.experimental_entries()[0].key
    for page in (window.efficiency_page, window.activity_page, window.identification_page):
        page.select(key)
        assert not page.run_button.isEnabled()
        assert "Missing" in page.gate.text()
    assert "certified source activity" in window.efficiency_page.gate.text()
    assert "verified energy calibration" in window.identification_page.gate.text()


def test_efficiency_then_activity_then_candidates(app: QApplication, window: MainWindow) -> None:
    w = window
    cal_key = fit_everything(app, w, "syn-cal-source")
    eff = w.efficiency_page
    eff.select(cal_key)
    assert eff.run_button.isEnabled() and eff.days_box.value() == 40.0
    assert eff.source_box.currentData() == "SYN-CAL"
    eff.geometry_edit.setText("synthetic")
    eff.run()
    assert eff.curve is not None and len(eff.curve.points) == 8, eff.status.text()
    assert eff.status.text().startswith("SYNTHETIC") and eff.plot.n_items == 4
    assert w.state.efficiency is not None

    x_key = fit_everything(app, w, "syn-x-sample")
    act = w.activity_page
    act.select(x_key)
    assert act.run_button.isEnabled() and act.days_box.value() == 10.0
    assert act.source_box.currentData() == "SYN-X"
    act.run()
    assert act.result is not None, act.status.text()
    q = act.result.activity
    assert abs(q.value - sources.SYN_X.activity_bq) < 4 * q.uncertainty
    assert dict(act.result_table.rows())["Status"].startswith("SYNTHETIC")

    nid = w.identification_page
    nid.select(x_key)
    assert nid.run_button.isEnabled() and nid.efficiency_check.isEnabled()
    nid.efficiency_check.setChecked(True)
    nid.run()
    assert nid.result is not None, nid.status.text()
    assert nid.candidate_table.rows()[0][0] == "SYN-X"
    assert dict(nid.evidence_table.rows())["Candidate"].startswith("SYN-X")
    out = w.export_identification(w.settings.paths.workspace / "candidates.csv")
    assert out is not None and "# status: SYNTHETIC" in out.read_text(encoding="utf-8")


def test_real_reference_data_are_not_applied_to_synthetic_spectra(
    app: QApplication, window: MainWindow
) -> None:
    w = window
    x_key = fit_everything(app, w, "syn-x-sample")
    path = w.settings.paths.workspace / "lib.json"
    path.write_text(json.dumps(library_document(real_like_library())), encoding="utf-8")
    assert w.load_library(path)
    page = w.identification_page
    page.select(x_key)
    assert not page.run_button.isEnabled()
    assert "not compared with real nuclear data" in page.gate.text()
    bad = w.settings.paths.workspace / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert not w.load_library(bad)
