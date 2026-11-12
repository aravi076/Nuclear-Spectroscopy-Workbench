"""Regions of interest on the plot, driven by real mouse and keyboard events (pytest-qt)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from nsw.ui.plot import PlotItemSpec, PlotMode, SpectrumPlot
from nsw.ui.roi_controller import RoiController
from nsw.ui.roi_panel import RoiPanel
from nsw.ui.state import AppState
from tests.conftest import make_raw

pytestmark = pytest.mark.gui


def spectrum_counts() -> np.ndarray:
    x = np.arange(1, 201)
    peak = 2000 * np.exp(-0.5 * ((x - 100) / 2.0) ** 2)
    return np.rint(300 + peak).astype(int)


class Harness(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self.plot = SpectrumPlot()
        self.controller = RoiController(self.state, self.plot, self)
        self.panel = RoiPanel(self.controller)
        layout = QVBoxLayout(self)
        layout.addWidget(self.plot)
        layout.addWidget(self.panel)
        self.resize(1366, 768)
        self.spectrum = make_raw(spectrum_counts())
        self.plot.set_spectra([PlotItemSpec(self.spectrum, "test")])
        self.controller.set_spectrum(self.spectrum)
        self.panel.refresh()


@pytest.fixture
def harness(qtbot):  # type: ignore[no-untyped-def]
    h = Harness()
    qtbot.addWidget(h)
    h.show()
    qtbot.waitExposed(h)
    h.plot.plot.getPlotItem().vb.setRange(xRange=(0.5, 200.5), padding=0)
    QApplication.processEvents()
    return h


def to_viewport(plot: SpectrumPlot, x: float) -> QPoint:
    vb = plot.view_box
    (_, _), (y0, y1) = vb.viewRange()
    scene = vb.mapViewToScene(QPointF(x, 0.5 * (y0 + y1)))
    return plot.plot.mapFromScene(scene)


NO_MOD = Qt.KeyboardModifier.NoModifier


def send(
    widget: QWidget,
    kind: QEvent.Type,
    pos: QPoint,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
    modifiers: Qt.KeyboardModifier = NO_MOD,
) -> None:
    event = QMouseEvent(
        kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)), button, buttons, modifiers
    )
    QApplication.sendEvent(widget, event)
    QApplication.processEvents()


def drag(plot: SpectrumPlot, x0: float, x1: float) -> None:
    viewport = plot.plot.viewport()
    a, b = to_viewport(plot, x0), to_viewport(plot, x1)
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    send(viewport, QEvent.Type.MouseButtonPress, a, left, left)
    for t in np.linspace(0.1, 1.0, 8):
        p = QPoint(round(a.x() + t * (b.x() - a.x())), a.y())
        send(viewport, QEvent.Type.MouseMove, p, none, left)
    send(viewport, QEvent.Type.MouseButtonRelease, b, left, none)


def click(
    plot: SpectrumPlot,
    x: float,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    modifiers: Qt.KeyboardModifier = NO_MOD,
) -> None:
    viewport = plot.plot.viewport()
    p = to_viewport(plot, x)
    send(viewport, QEvent.Type.MouseButtonPress, p, button, button, modifiers)
    send(viewport, QEvent.Type.MouseButtonRelease, p, button, Qt.MouseButton.NoButton, modifiers)


def test_keyboard_switches_modes(harness, qtbot) -> None:  # type: ignore[no-untyped-def]
    harness.plot.plot.setFocus()
    qtbot.keyClick(harness.plot.plot, Qt.Key.Key_R)
    assert harness.plot.mode is PlotMode.REGION
    qtbot.keyClick(harness.plot.plot, Qt.Key.Key_Z)
    assert harness.plot.mode is PlotMode.ZOOM_BOX
    qtbot.keyClick(harness.plot.plot, Qt.Key.Key_Escape)
    assert harness.plot.mode is PlotMode.NAVIGATE


def test_drag_draws_a_region_and_undo_redo_delete(harness, qtbot) -> None:  # type: ignore[no-untyped-def]
    harness.plot.set_mode(PlotMode.REGION)
    drag(harness.plot, 80.0, 120.0)
    rois = harness.controller.rois()
    assert len(rois) == 1
    roi = rois[0]
    assert abs(roi.first_channel - 80) <= 1 and abs(roi.last_channel - 120) <= 1
    assert harness.controller.selected == roi.roi_id
    assert list(harness.plot.regions()) == [roi.roi_id]
    rows = harness.panel.table.rows()
    assert len(rows) == 1 and "above the decision threshold" in rows[0][5]
    # a navigate-mode drag pans instead of drawing
    harness.plot.set_mode(PlotMode.NAVIGATE)
    drag(harness.plot, 30.0, 50.0)
    assert len(harness.controller.rois()) == 1
    # keyboard: delete, undo, redo
    harness.plot.plot.setFocus()
    qtbot.keyClick(harness.plot.plot, Qt.Key.Key_Delete)
    assert harness.controller.rois() == []
    qtbot.keyClick(harness.plot.plot, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert [r.roi_id for r in harness.controller.rois()] == [roi.roi_id]
    qtbot.keyClick(harness.plot.plot, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert harness.controller.rois() == []


def test_moving_an_edge_is_validated_and_undoable(harness) -> None:  # type: ignore[no-untyped-def]
    roi = harness.controller.create(60, 140)
    assert roi is not None
    item = harness.plot.region_item(roi.roi_id)
    item.setRegion((69.5, 140.5))  # what a drag of the left edge does
    item.sigRegionChangeFinished.emit(item)
    moved = harness.state.roi(harness.spectrum.data_sha256, roi.roi_id)
    assert (moved.first_channel, moved.last_channel) == (70, 140)
    item.setRegion((150.5, 400.5))  # beyond the spectrum: refused, drawn back
    item.sigRegionChangeFinished.emit(item)
    assert harness.plot.regions()[roi.roi_id] == (70, 140)
    assert "refused" in harness.panel.status.text()
    harness.controller.undo()
    back = harness.state.roi(harness.spectrum.data_sha256, roi.roi_id)
    assert (back.first_channel, back.last_channel) == (60, 140)


def test_click_selects_region_and_marker(harness) -> None:  # type: ignore[no-untyped-def]
    a = harness.controller.create(20, 40)
    b = harness.controller.create(150, 180)
    assert a is not None and b is not None
    click(harness.plot, 30.0)
    assert harness.controller.selected == a.roi_id
    harness.plot.set_markers([100.0, 160.0])
    harness.plot.set_mode(PlotMode.SELECT)
    click(harness.plot, 100.2)
    assert harness.plot.selected_markers() == [100.0]
    click(harness.plot, 160.0, modifiers=Qt.KeyboardModifier.ControlModifier)
    assert harness.plot.selected_markers() == [100.0, 160.0]


def test_context_menu_offers_the_region_actions(harness) -> None:  # type: ignore[no-untyped-def]
    roi = harness.controller.create(90, 110)
    assert roi is not None
    texts = [a.text() for a in harness.plot.context_menu_actions(100.0)]
    assert "Select this region" in texts and "Delete selected region (Del)" in texts
    added: list[float] = []
    harness.plot.candidate_add_requested.connect(added.append)
    action = next(
        a for a in harness.plot.context_menu_actions(55.0) if a.text().startswith("Add peak")
    )
    action.trigger()
    assert added == [55.0]


def test_limit_method_switch_changes_the_limits(harness) -> None:  # type: ignore[no-untyped-def]
    harness.controller.create(80, 120)
    harness.panel.set_method("iso11929")
    iso = harness.panel.table.rows()[0]
    harness.panel.set_method("currie")
    currie = harness.panel.table.rows()[0]
    assert "ISO 11929" in iso[6] and "Currie" in currie[6]
    assert iso[3] == currie[3]  # identical for a counting model with exact w


def test_region_too_small_is_refused(harness) -> None:  # type: ignore[no-untyped-def]
    assert harness.controller.create(10, 11) is None
    assert "at least" in harness.panel.status.text()


def test_fit_key_fits_the_selected_region(harness, qtbot) -> None:  # type: ignore[no-untyped-def]
    harness.plot.set_mode(PlotMode.REGION)
    drag(harness.plot, 85.0, 115.0)
    harness.plot.plot.setFocus()
    qtbot.keyClick(harness.plot.plot, Qt.Key.Key_F)
    fits = harness.state.fits(harness.spectrum.data_sha256)
    assert len(fits) == 1 and fits[0].converged
    assert abs(fits[0].peaks[0].centroid - 100.0) < 0.2
    assert harness.panel.fit_plot.result() is fits[0]
    roi = harness.controller.selected_roi()
    assert roi is not None and len(roi.peak_channels) == 1  # peaks were found and kept
    harness.controller.undo()  # undoes the peak assignment, not the fit
    assert harness.controller.selected_roi().peak_channels == ()


def test_context_menu_adds_a_peak_and_compare_models(harness) -> None:  # type: ignore[no-untyped-def]
    roi = harness.controller.create(80, 120)
    assert roi is not None
    action = next(
        a for a in harness.plot.context_menu_actions(100.0) if a.text().startswith("Add peak")
    )
    action.trigger()
    assert harness.controller.selected_roi().peak_channels == (100.0,)
    harness.panel.compare_button.click()
    rows = harness.panel.comparison_table.rows()
    assert len(rows) == 3 and sum(r[5] == "preferred" for r in rows) == 1
    assert harness.panel.comparison_label.text().startswith("Model comparison:")
    assert harness.state.fits(harness.spectrum.data_sha256)


def test_fit_without_region_explains(harness) -> None:  # type: ignore[no-untyped-def]
    assert harness.controller.fit_selected() is None
    assert "Select a region" in harness.panel.status.text()
    harness.plot.candidate_add_requested.emit(30.0)
    assert "Draw or select a region" in harness.panel.status.text()


def test_automatic_search_paints_classified_markers(harness) -> None:  # type: ignore[no-untyped-def]
    harness.panel.spectrum_opened()
    found = harness.state.peak_search(harness.spectrum.data_sha256)
    assert found is not None and len(found.candidates) == 1
    assert harness.plot.markers() == [found.candidates[0].channel]
    assert "1 peak candidates: 1 strong" in harness.panel.candidates_label.text()
    # opening again does not search again (the analyst's search is kept)
    harness.state.set_peak_search(found)
    harness.panel.auto_box.setChecked(False)
    harness.panel.spectrum_opened()
    assert harness.state.peak_search(harness.spectrum.data_sha256) is found


def test_exclude_restore_with_undo_and_fit_ignores_excluded(harness) -> None:  # type: ignore[no-untyped-def]
    harness.panel.spectrum_opened()
    key = harness.spectrum.data_sha256
    harness.plot.set_mode(PlotMode.SELECT)
    marker = harness.plot.markers()[0]
    click(harness.plot, marker)
    assert harness.plot.selected_markers() == [marker]
    harness.panel.exclude_button.click()
    assert harness.state.excluded_candidates(key)
    assert "1 excluded" in harness.panel.candidates_label.text()
    # a region over the excluded candidate finds no usable search candidate: it searches
    # the region itself instead, and says so
    roi = harness.controller.create(85, 115)
    assert roi is not None
    harness.controller.fit_selected()
    assert "searching the region" in harness.panel.status.text()
    harness.controller.undo()  # peak assignment
    harness.controller.undo()  # region creation
    harness.controller.undo()  # exclusion
    assert not harness.state.excluded_candidates(key)
    harness.panel.exclude_button.click()  # nothing selected any more after repaint
    assert "Select candidates first" in harness.panel.status.text()


def test_search_selected_region_only(harness) -> None:  # type: ignore[no-untyped-def]
    roi = harness.controller.create(20, 60)  # flat part: nothing to find
    assert roi is not None
    harness.panel.search_region()
    found = harness.state.peak_search(harness.spectrum.data_sha256)
    assert found is not None and found.candidates == ()
    assert harness.plot.markers() == []
