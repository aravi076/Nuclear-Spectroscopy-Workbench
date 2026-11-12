"""Energy Calibration page: suggested assignments from the library, then a fit."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from nsw.ui.calibration_page import COL_ENERGY, COL_SOURCE
from nsw.ui.main_window import PAGE_CALIBRATION, MainWindow
from tests.integration.test_gui_quantification import app, fit_everything, window  # noqa: F401

pytestmark = pytest.mark.gui


def test_suggest_then_fit(app: QApplication, window: MainWindow) -> None:  # noqa: F811
    key = fit_everything(app, window, "syn-cal-source")
    window.nav.setCurrentRow(PAGE_CALIBRATION)
    page = window.calibration_page
    page.select(key)
    spectrum = page.current_spectrum()
    c0, c1 = spectrum.energy_calibration.coefficients[:2]
    page.assist_gain.setValue(c1 * 1.003)
    page.assist_offset.setValue(c0)
    page.assist_unc.setValue(0.4)
    page.suggest_assignments()
    filled = [
        r
        for r in range(page.peak_table.rowCount())
        if page.peak_table.item(r, COL_ENERGY).text().strip()
    ]
    assert filled, page.assist_status.text()
    assert "filled in" in page.assist_status.text()
    assert all("FICTITIOUS" in page.peak_table.item(r, COL_SOURCE).text() for r in filled)
    page.run()
    assert page.fit is not None


def test_suggestions_explain_missing_inputs(window: MainWindow) -> None:  # noqa: F811
    window.nav.setCurrentRow(PAGE_CALIBRATION)
    page = window.calibration_page
    page.select(window.state.experimental_entries()[0].key)
    page.suggest_assignments()
    assert "Fit peaks" in page.assist_status.text() or "fictitious" in page.assist_status.text()
