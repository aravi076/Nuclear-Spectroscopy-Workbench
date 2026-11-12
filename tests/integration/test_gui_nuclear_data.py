"""Nuclear Data Explorer page: browsing, overlay gate, overlay, matching, decisions."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from nsw.io.reference_data import library_document
from nsw.ui.main_window import PAGE_NUCLEAR_DATA, MainWindow
from nsw.ui.plot import PlotMode
from tests.integration.test_gui_quantification import app, fit_everything, window  # noqa: F401
from tests.unit.test_identification_service import real_like_library

pytestmark = pytest.mark.gui


def page_for(w: MainWindow, key: str):  # type: ignore[no-untyped-def]
    w.nav.setCurrentRow(PAGE_NUCLEAR_DATA)
    page = w.nuclear_data_page
    page.select_spectrum(key)
    return page


def test_browsing_the_fictitious_library(window: MainWindow) -> None:  # noqa: F811
    page = window.nuclear_data_page
    assert "fictitious test library" in page.library_box.currentText()
    names = page.nuclide_table.column_values("Nuclide")
    assert "SYN-X" in names and "DECOY-A" in names
    page.select_nuclides(["SYN-X"])
    assert page.selected_nuclides() == ["SYN-X"]
    assert len(page.line_table.rows()) == 3
    assert dict(page.provenance_table.rows())["Evaluation"] == "FICTITIOUS"
    assert page.conflict_table.rows() == []


def test_experimental_spectrum_gets_no_overlay(window: MainWindow) -> None:  # noqa: F811
    key = window.state.experimental_entries()[0].key
    page = page_for(window, key)
    assert not page.overlay_button.isEnabled() and not page.match_button.isEnabled()
    assert "verified energy calibration" in page.gate.text()


def test_overlay_match_and_decision_on_synthetic(app: QApplication, window: MainWindow) -> None:  # noqa: F811
    key = fit_everything(app, window, "syn-x-sample")
    page = page_for(window, key)
    assert page.overlay_button.isEnabled()
    page.select_nuclides(["SYN-X"])
    page.show_overlay()
    labels = [label for _, label in page.plot.reference_lines()]
    assert len(labels) == 3 and all(label.startswith("SYN-X") for label in labels)
    spectrum = page.current_spectrum()
    cal = spectrum.energy_calibration
    marker = min(page.plot.markers(), key=lambda c: abs(float(cal.energy_at(c)) - 650.0))
    page.plot.set_mode(PlotMode.SELECT)
    assert page.plot.click_at(marker, additive=False)
    page.match_selected_peak()
    rows = page.match_table.rows()
    assert rows and rows[0][0] == "SYN-X", rows
    page.match_table.selectRow(0)
    page.accept_button.click()
    decisions = window.state.assignments(key)
    assert decisions[-1]["nuclide"] == "SYN-X" and decisions[-1]["accepted"] is True
    page.clear_overlay()
    assert page.plot.reference_lines() == []


def test_loaded_library_becomes_current_and_is_gated(window: MainWindow, tmp_path: Path) -> None:  # noqa: F811
    path = tmp_path / "real-like.json"
    path.write_text(json.dumps(library_document(real_like_library())), encoding="utf-8")
    assert window.load_library(path)
    page = window.nuclear_data_page
    assert page.library_box.count() == 2 and page.library_box.currentIndex() == 1
    key = next(e.key for e in window.state.synthetic_entries())
    page = page_for(window, key)
    assert not page.overlay_button.isEnabled()
    assert "not compared with real nuclear data" in page.gate.text()
