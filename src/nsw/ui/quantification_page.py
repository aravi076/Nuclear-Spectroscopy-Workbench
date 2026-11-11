"""Efficiency Calibration and Activity Calculation pages.

Both calculations are refused for experimental spectra: the page lists what is missing
(source certificates, geometry, detector model, nuclear data, verified calibration,
authorisation) and keeps the calculation disabled. For synthetic spectra the fictitious
test sources SYN-CAL and SYN-X are offered; every result is labelled synthetic.
Calculations are instantaneous and run on the GUI thread; logic is in
``nsw.services.efficiency``.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from nsw.core.spectrum import Spectrum
from nsw.services import efficiency as es
from nsw.ui.explorer import KeyValueTable
from nsw.ui.peak_search_page import describe_entry
from nsw.ui.state import AppState, Entry
from nsw.ui.tables import DataTable

NEEDS_FITS = "Fit the peaks of this spectrum on the Peak Fitting page first."
SYNTHETIC_NOTE = (
    "Synthetic spectra are compared with the fictitious test sources SYN-CAL and SYN-X "
    "(invented values, not nuclear data). Synthetic spectra carry no start time: enter the "
    "days between the source reference time and the measurement."
)


class _SpectrumPage(QWidget):
    """Spectrum selector, gate message and status line shared by both pages."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._entries: list[Entry] = []
        self.spectrum_box = QComboBox()
        self.spectrum_box.setMinimumContentsLength(40)
        self.spectrum_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source_box = QComboBox()
        self.days_box = QDoubleSpinBox()
        self.days_box.setRange(-36500.0, 36500.0)
        self.days_box.setDecimals(3)
        self.days_box.setSuffix(" days after the source reference time")
        self.gate = QLabel()
        self.gate.setWordWrap(True)
        self.gate.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.line_table = DataTable()
        self.result_table = KeyValueTable()
        self.spectrum_box.currentIndexChanged.connect(self._spectrum_changed)
        state.changed.connect(self.refresh)
        state.fits_changed.connect(self._fits_changed)

    def refresh(self) -> None:
        self._entries = self.state.all_entries()
        current = self.spectrum_box.currentData()
        self.spectrum_box.blockSignals(True)
        self.spectrum_box.clear()
        for e in self._entries:
            self.spectrum_box.addItem(describe_entry(e), e.key)
        index = self.spectrum_box.findData(current)
        self.spectrum_box.setCurrentIndex(index if index >= 0 else (0 if self._entries else -1))
        self.spectrum_box.blockSignals(False)
        self._spectrum_changed()

    def select(self, key: str) -> None:
        self.spectrum_box.setCurrentIndex(self.spectrum_box.findData(key))

    def current_spectrum(self) -> Spectrum | None:
        i = self.spectrum_box.currentIndex()
        return self._entries[i].spectrum if 0 <= i < len(self._entries) else None

    def _fits_changed(self, key: str) -> None:
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._spectrum_changed()

    def _set_status(self, text: str, ok: bool) -> None:
        self.status.setText(text)
        colour = "#0b5cad" if ok else "#b00020"
        self.status.setStyleSheet(f"color: {colour}; font-weight: bold;")

    def _gate(self, s: Spectrum | None) -> bool:
        """Show what is missing; True if the calculation may run."""
        self.source_box.clear()
        if s is None:
            self.gate.setText("Load a dataset first.")
            return False
        missing = es.missing_inputs(s)
        if missing:
            self.gate.setText(
                "<b>Not available for this experimental spectrum.</b> Missing:<ul>"
                + "".join(f"<li>{m}</li>" for m in missing)
                + "</ul>"
            )
            self.gate.setStyleSheet("color: #b00020;")
            return False
        self.gate.setStyleSheet("")
        self.gate.setText(SYNTHETIC_NOTE)
        for name in es.fictitious_sources():
            self.source_box.addItem(f"{name} (fictitious)", name)
        self.days_box.setValue(es.suggested_start_days(s))
        return True

    def _matches(self, s: Spectrum) -> tuple[es.FictitiousSource, list[es.LineArea]]:
        source = es.fictitious_sources()[str(self.source_box.currentData())]
        fits = self.state.fits(s.data_sha256)
        if not fits:
            raise es.ProcessingError(NEEDS_FITS)
        matches = es.match_lines(s, fits, source.lines)
        self.line_table.set_table(*es.line_rows(s, matches))
        return source, matches

    def _spectrum_changed(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class EfficiencyPlot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLogMode(x=True, y=True)
        self.plot.setLabel("bottom", "Energy (keV)")
        self.plot.setLabel("left", "Full-energy-peak efficiency")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)
        self.n_items = 0

    def show_curve(self, curve: es.EfficiencyCurve | None) -> None:
        self.plot.clear()
        self.n_items = 0
        if curve is None:
            return
        lo, hi = curve.energy_range_kev
        e = np.geomspace(lo * 0.8, hi * 1.2, 200)
        value, rel, _ = curve.efficiency(e)
        dash = Qt.PenStyle.DashLine
        self.plot.plot(e, value, pen=pg.mkPen("#c00000", width=2))
        self.plot.plot(e, value * (1 + rel), pen=pg.mkPen("#c00000", width=1, style=dash))
        self.plot.plot(e, value * (1 - rel), pen=pg.mkPen("#c00000", width=1, style=dash))
        pts_e = np.array([p.energy_kev for p in curve.points])
        pts_v = np.array([p.efficiency for p in curve.points])
        self.plot.plot(pts_e, pts_v, pen=None, symbol="o", symbolBrush="#1f77b4", symbolSize=7)
        self.n_items = 4


class EfficiencyPage(_SpectrumPage):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent)
        self.curve: es.EfficiencyCurve | None = None
        self.order_box = QSpinBox()
        self.order_box.setRange(1, 4)
        self.order_box.setValue(2)
        self.geometry_edit = QLineEdit()
        self.geometry_edit.setPlaceholderText("source-detector geometry (recorded with the curve)")
        self.run_button = QPushButton("Fit efficiency curve")
        self.run_button.clicked.connect(self.run)
        form = QFormLayout()
        form.addRow("Spectrum:", self.spectrum_box)
        form.addRow("Reference source:", self.source_box)
        form.addRow("Measurement start:", self.days_box)
        form.addRow("Curve order (ln ε in ln E):", self.order_box)
        form.addRow("Geometry:", self.geometry_edit)
        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addStretch(1)
        self.plot = EfficiencyPlot()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(form)
        left_layout.addWidget(self.gate)
        left_layout.addLayout(buttons)
        left_layout.addWidget(self.status)
        left_layout.addWidget(self.line_table, 1)
        upper = QSplitter(Qt.Orientation.Horizontal)
        upper.addWidget(left)
        upper.addWidget(self.plot)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(upper)
        split.addWidget(self.result_table)
        QVBoxLayout(self).addWidget(split)
        self.refresh()

    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self.curve = None
        self.plot.show_curve(None)
        self.result_table.set_rows([])
        self.line_table.set_table([], [])
        self._set_status("", True)
        allowed = self._gate(s)
        if (
            allowed
            and s is not None
            and "SYN-CAL" in [self.source_box.itemData(i) for i in range(self.source_box.count())]
        ):
            self.source_box.setCurrentIndex(self.source_box.findData("SYN-CAL"))
        self.run_button.setEnabled(allowed)

    def run(self) -> None:
        s = self.current_spectrum()
        if s is None:
            return
        try:
            source, matches = self._matches(s)
            used = [m for m in matches if m.matched]
            timing = es.timing_of(s, es.start_time_for(s, self.days_box.value()))
            curve = es.efficiency_curve(
                s,
                [(m.area, m.area_unc) for m in used],
                [m.line for m in used],
                source.source,
                timing,
                self.order_box.value(),
                self.geometry_edit.text(),
            )
        except (ValueError, es.ProcessingError) as exc:
            self.curve = None
            self.plot.show_curve(None)
            self.result_table.set_rows([])
            self._set_status(f"Efficiency curve refused: {exc}", False)
            return
        self.curve = curve
        self.state.set_efficiency(s.data_sha256, curve)
        self.plot.show_curve(curve)
        self.result_table.set_rows(es.curve_rows(s, curve))
        n_missing = sum(1 for m in matches if not m.matched)
        self._set_status(
            es.result_label(s)
            + f" — curve from {len(curve.points)} lines"
            + (f"; {n_missing} lines without a fitted peak were left out" if n_missing else ""),
            not curve.poor_fit,
        )


class ActivityPage(_SpectrumPage):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent)
        self.result: es.ActivityResult | None = None
        self.curve_label = QLabel()
        self.curve_label.setWordWrap(True)
        self.run_button = QPushButton("Calculate activity")
        self.run_button.clicked.connect(self.run)
        form = QFormLayout()
        form.addRow("Spectrum:", self.spectrum_box)
        form.addRow("Nuclide (source data):", self.source_box)
        form.addRow("Measurement start:", self.days_box)
        form.addRow("Efficiency curve:", self.curve_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.gate)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.line_table)
        split.addWidget(self.result_table)
        layout.addWidget(split, 1)
        state.efficiency_changed.connect(self._spectrum_changed)
        self.refresh()

    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self.result = None
        self.result_table.set_rows([])
        self.line_table.set_table([], [])
        self._set_status("", True)
        allowed = self._gate(s)
        if allowed:
            self.source_box.setCurrentIndex(max(self.source_box.findData("SYN-X"), 0))
        stored = self.state.efficiency
        if stored is None:
            self.curve_label.setText("none — fit one on the Efficiency Calibration page")
        else:
            entry = self.state.entry(stored[0])
            name = entry.title if entry else "a spectrum no longer loaded"
            lo, hi = stored[1].energy_range_kev
            self.curve_label.setText(f"from {name}, {lo:.0f}–{hi:.0f} keV")
        self.run_button.setEnabled(allowed and stored is not None)

    def run(self) -> None:
        s = self.current_spectrum()
        stored = self.state.efficiency
        if s is None or stored is None:
            return
        try:
            source, matches = self._matches(s)
            used = [m for m in matches if m.matched]
            if not used:
                raise es.ProcessingError("no line of this nuclide has a fitted peak")
            timing = es.timing_of(s, es.start_time_for(s, self.days_box.value()))
            result = es.activity(
                s,
                [(m.area, m.area_unc) for m in used],
                [m.line for m in used],
                stored[1],
                timing,
                source.source.half_life_s,
                source.source.half_life_unc_s,
                reference_time=source.source.reference_time,
            )
        except (ValueError, es.ProcessingError) as exc:
            self.result = None
            self.result_table.set_rows([])
            self._set_status(f"Activity refused: {exc}", False)
            return
        self.result = result
        self.result_table.set_rows(es.activity_rows(s, result))
        q = result.activity
        ok = result.consistent and math.isfinite(q.uncertainty)
        self._set_status(
            f"{es.result_label(s)} — {q.value:.5g} ± {q.uncertainty:.2g} Bq at the source "
            "reference time",
            ok,
        )
